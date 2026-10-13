"""Logika unduhan: yt-dlp, unduh langsung, konversi, penamaan, worker job."""
import os
import shutil
import subprocess
import threading
import time
import urllib.request
import urllib.parse

from config import (OUT, MAIN_MIN, AD_MAX_LEN, UA, DOWNLOADABLE_EXT, VIDEO_EXT,
                    DIRECT_CT_PREFIX, content_type_is_file, media_of_url,
                    is_downloadable_url)
from util import log, clean, sanitize_name, fingerprint, next_timestamp_name, human_size

# konkurensi unduhan: maks 2 paralel
slots = threading.Semaphore(2)


# ------------------------------------------------------------- ffmpeg
def find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = find_ffmpeg()


def has_drm(playlist_text):
    """Deteksi DRM (Widevine/PlayReady/FairPlay) dari isi playlist."""
    from config import DRM_PAT
    return bool(DRM_PAT.search(playlist_text or ""))


# ------------------------------------------------------------- yt-dlp
def _ydl_opts(job, target, headers, browser, ffmpeg_loc):
    return {
        "outtmpl": os.path.join(target, "dl.%(ext)s"),
        "http_headers": headers or {},
        "concurrent_fragment_downloads": 8,
        "retries": 10,
        "fragment_retries": 10,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "format": "bv*+ba/b",
        "format_sort": ["res", "fps", "vcodec:h264", "br", "size"],
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_loc,
        "progress_hooks": [lambda d: _ydl_hook(job, d)],
    }


def _ydl_hook(job, d):
    try:
        st = d.get("status")
        if st == "downloading":
            job["done_bytes"] = d.get("downloaded_bytes") or job["done_bytes"]
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            job["total_bytes"] = total or job["total_bytes"]
            job["est"] = total is None
            job["speed_bps"] = d.get("speed") or 0
            frag_i = d.get("fragment_index")
            frag_n = d.get("fragment_count")
            if frag_i and frag_n:
                job["progress"] = frag_i / frag_n
            elif job["total_bytes"]:
                job["progress"] = job["done_bytes"] / job["total_bytes"]
        elif st == "finished":
            job["progress"] = 1.0
    except Exception:
        pass


def run_ydl(job, target, url, headers=None, browser=""):
    """Unduh target lewat yt-dlp ke folder target. Return path file hasil."""
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp belum terpasang (pip install -r requirements.txt)")
    ffmpeg_loc = FFMPEG or ""
    opts = _ydl_opts(job, target, headers, browser, ffmpeg_loc)
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    before = set(os.listdir(target))
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    after = [f for f in os.listdir(target) if f not in before]
    files = [os.path.join(target, f) for f in after
             if os.path.isfile(os.path.join(target, f))]
    if not files:
        raise RuntimeError("yt-dlp selesai tanpa menghasilkan file")
    return max(files, key=os.path.getmtime)


# ------------------------------------------------------------- unduh langsung
def download_direct(job, url, headers=None, name_hint=""):
    """Unduh file langsung via urllib. Return path file hasil."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, **(headers or {})})
    tmpdir = os.path.join(str(OUT), f"_tmp_{job['id']}")
    os.makedirs(tmpdir, exist_ok=True)
    with urllib.request.urlopen(req, timeout=60) as resp:
        ct = resp.headers.get("Content-Type", "")
        if not (content_type_is_file(ct) or ct.startswith(DIRECT_CT_PREFIX)):
            raise RuntimeError(f"bukan file yang bisa diunduh (content-type: {ct})")
        name = name_hint
        cd = resp.headers.get("Content-Disposition", "")
        if not name and "filename=" in cd:
            name = cd.split("filename=")[-1].strip('"; ')
        if not name:
            path = urllib.parse.urlparse(url).path.rstrip("/")
            name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
        if not name or "." not in name:
            ext = _ext_from_ct(ct) or ".bin"
            name = f"file{ext}"
        name = sanitize_name(name)
        dest = os.path.join(tmpdir, name)
        total = resp.headers.get("Content-Length")
        total = int(total) if total and total.isdigit() else 0
        if job.get("size") and total and abs(total - job["size"]) / job["size"] > 0.02:
            raise RuntimeError("link milik file lain (ukuran tidak cocok)")
        job["total_bytes"] = total
        done = 0
        t0 = time.time()
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                job["done_bytes"] = done
                if total:
                    job["progress"] = done / total
                dt = time.time() - t0
                job["speed_bps"] = done / dt if dt > 0 else 0
        job["progress"] = 1.0
        return dest


def _ext_from_ct(ct):
    mapping = {
        "application/pdf": ".pdf",
        "application/zip": ".zip",
        "application/x-rar-compressed": ".rar",
        "application/vnd.rar": ".rar",
        "application/x-7z-compressed": ".7z",
        "application/epub+zip": ".epub",
        "text/csv": ".csv",
        "text/plain": ".txt",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    }
    return mapping.get(ct.lower().split(";")[0].strip())


# ------------------------------------------------------------- finalisasi file
def ensure_final(job, path):
    """Video -> paksa .mp4 (remux/encode ulang). Non-video -> apa adanya."""
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return ensure_mp4(job, path)
    return path


def ensure_mp4(job, path):
    if path.lower().endswith(".mp4"):
        return path
    if not FFMPEG:
        raise RuntimeError("ffmpeg tidak ditemukan; tidak bisa konversi ke mp4")
    job["status"] = "converting"
    log(job, "mengonversi ke .mp4 …")
    out = os.path.splitext(path)[0] + ".mp4"
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # 1) remux tanpa kehilangan kualitas
    r = subprocess.run(
        [FFMPEG, "-y", "-i", path, "-map", "0:v?", "-map", "0:a?",
         "-c", "copy", "-movflags", "+faststart", out],
        capture_output=True, creationflags=creationflags, timeout=3600)
    if r.returncode == 0 and os.path.exists(out):
        os.remove(path)
        return out
    # 2) encode ulang H.264/AAC
    log(job, "remux gagal, encode ulang H.264/AAC …")
    r = subprocess.run(
        [FFMPEG, "-y", "-i", path, "-c:v", "libx264", "-preset", "medium",
         "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out],
        capture_output=True, creationflags=creationflags, timeout=7200)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError("konversi ke mp4 gagal")
    os.remove(path)
    return out


class DuplicateFile(Exception):
    pass


def stamp(job, path):
    """Fingerprint anti-duplikat + pindah ke downloads/ dengan nama timestamp."""
    fp = fingerprint(path)
    for existing in OUT.iterdir():
        if existing.is_file() and existing.name != os.path.basename(path):
            try:
                if fingerprint(str(existing)) == fp:
                    os.remove(path)
                    raise DuplicateFile(
                        f"file kembar, sudah ada sebagai {existing.name}")
            except OSError:
                continue
    ext = os.path.splitext(path)[1].lower() or ".bin"
    name = next_timestamp_name(str(OUT), ext)
    dest = OUT / name
    os.replace(path, dest)
    size = dest.stat().st_size
    job["file"] = name
    info = f"hasil: {ext[1:]}, {human_size(size)}"
    if job.get("size") and size < job["size"] * 0.7:
        info += " (peringatan: lebih kecil dari ukuran asli di situs)"
    log(job, info)
    return name


# ------------------------------------------------------------- worker job
def _worker(job):
    """Urutan keputusan unduhan sesuai blueprint bagian 5 (diperluas ke file digital)."""
    with slots:
        job["status"] = "running"
        job["started"] = time.time()
        tmpdir = OUT / f"_tmp_{job['id']}"
        tmpdir.mkdir(parents=True, exist_ok=True)
        try:
            base_headers = {"User-Agent": UA}
            if job.get("referer"):
                base_headers["Referer"] = job["referer"]
            candidates = []

            if job.get("stream"):
                # stream manual (master.m3u8) + referer/cookie opsional
                candidates = [{"url": job["stream"], "kind": "playlist",
                               "headers": {}, "media": "video"}]
            elif job.get("direct"):
                log(job, "mencoba unduh langsung …")
                try:
                    path = download_direct(job, job["direct"], base_headers,
                                           job.get("name", ""))
                    stamp(job, ensure_final(job, path))
                    job["status"] = "done"
                    return
                except Exception as e:
                    log(job, f"unduh langsung gagal: {e}; coba yt-dlp")
                path = run_ydl(job, str(tmpdir), job["direct"],
                               base_headers, job.get("browser", ""))
                stamp(job, ensure_final(job, path))
                job["status"] = "done"
                return
            elif job.get("pick"):
                from sniffer import sniff
                candidates = sniff(job["url"], pick=job["pick"], job=job)
            else:
                url = job["url"]
                if is_downloadable_url(url) or url.lower().endswith((".m3u8", ".mpd")):
                    # URL-nya sendiri sudah file/stream -> langsung
                    try:
                        path = download_direct(job, url, base_headers, "")
                        stamp(job, ensure_final(job, path))
                        job["status"] = "done"
                        return
                    except Exception as e:
                        log(job, f"unduh langsung gagal ({e}); coba jalur lain")
                if job.get("smart"):
                    from sniffer import sniff
                    candidates = sniff(url, smart=True, job=job)
                if not candidates:
                    # 1) yt-dlp langsung ke halaman
                    try:
                        path = run_ydl(job, str(tmpdir), url, base_headers,
                                       job.get("browser", ""))
                        stamp(job, ensure_final(job, path))
                        job["status"] = "done"
                        return
                    except Exception as e:
                        log(job, f"yt-dlp gagal: {e}")
                    # 2) siapa tahu URL-nya file
                    try:
                        path = download_direct(job, url, base_headers, "")
                        stamp(job, ensure_final(job, path))
                        job["status"] = "done"
                        return
                    except Exception as e:
                        log(job, f"unduh langsung gagal: {e}")
                    # 3) sniffer bila belum smart
                    if not job.get("smart"):
                        from sniffer import sniff
                        candidates = sniff(url, job=job)
                if not candidates:
                    raise RuntimeError(
                        "File/stream tidak tertangkap otomatis. Coba isi "
                        "'Link stream (master.m3u8)' di Pengaturan lanjutan, "
                        "atau pakai Pindai halaman.")

            last_err = ""
            for i, cand in enumerate(candidates):
                headers = dict(base_headers)
                headers.update(cand.get("headers") or {})
                kind = cand.get("kind")
                curl = cand["url"]
                media = cand.get("media") or media_of_url(curl)
                if kind == "playlist":
                    # cek DRM sebelum unduh
                    try:
                        txt = urllib.request.urlopen(
                            urllib.request.Request(
                                curl, headers={"User-Agent": UA}),
                            timeout=30).read(200000).decode("utf-8", "ignore")
                        if has_drm(txt):
                            cand["drm"] = True
                            log(job, "kandidat ber-DRM, dilewati "
                                     "(Widevine/PlayReady/FairPlay tidak didukung)")
                            continue
                    except Exception:
                        pass
                try:
                    if kind == "direct" and media != "video":
                        path = download_direct(job, curl, headers,
                                               job.get("name", ""))
                    else:
                        path = run_ydl(job, str(tmpdir), curl, headers,
                                       job.get("browser", ""))
                    # smart: buang hasil iklan (< 45 dtk) bila masih ada kandidat
                    if (job.get("smart") and i < len(candidates) - 1
                            and media == "video"):
                        dur = _probe_duration(path)
                        if dur and dur < AD_MAX_LEN:
                            log(job, f"hasil {dur:.0f} dtk diduga iklan, "
                                     "lanjut ke kandidat berikut")
                            os.remove(path)
                            continue
                    stamp(job, ensure_final(job, path))
                    job["status"] = "done"
                    return
                except DuplicateFile as e:
                    job["error"] = str(e)
                    job["status"] = "error"
                    return
                except Exception as e:
                    last_err = clean(str(e))
                    log(job, f"kandidat gagal: {last_err}")
            raise RuntimeError(last_err or "semua kandidat gagal")
        except DuplicateFile as e:
            job["error"] = str(e)
            job["status"] = "error"
        except Exception as e:
            job["error"] = clean(str(e))
            job["status"] = "error"
            log(job, f"gagal: {job['error']}")
        finally:
            job["finished"] = time.time()
            shutil.rmtree(tmpdir, ignore_errors=True)


def _probe_duration(path):
    if not FFMPEG:
        return None
    try:
        r = subprocess.run(
            [FFMPEG, "-i", path], capture_output=True, text=True, timeout=60)
        m = __import__("re").search(
            r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return None


def start_worker(job):
    t = threading.Thread(target=_worker, args=(job,), daemon=True)
    t.start()
    return t
