"""Pindai halaman: temukan banyak file digital (video, audio, dokumen,
ebook, arsip, gambar) dalam satu halaman -> daftar item dengan
checkbox untuk diunduh."""
import re
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from config import (UA, DOWNLOADABLE_EXT, content_type_is_file, media_of_url)
from util import log, clean, human_size

JS_SCAN = """() => {
  const out = [];
  const seen = new Set();
  const isFile = u => /\\.(mp4|mkv|mov|avi|webm|m4v|ts|flv|3gp|wmv|mpg|mpeg|mp3|wav|flac|ogg|oga|opus|m4a|aac|wma|pdf|doc|docx|odt|rtf|xls|xlsx|ods|ppt|pptx|odp|txt|csv|tsv|md|json|xml|epub|mobi|azw|azw3|fb2|zip|rar|7z|tar|gz|tgz|bz2|png|jpe?g|gif|webp|svg|bmp|tiff?|avif|ico|psd|ai)(\\?|$)/i.test(u || '');
  document.querySelectorAll('a[href], video[src], source[src]').forEach(el => {
    const u = el.href || el.src || el.getAttribute('src') || '';
    if (!u || seen.has(u)) return;
    if (!isFile(u) && !/download/i.test(u)) return;
    seen.add(u);
    let card = el, thumb = '', name = (el.innerText || '').trim();
    if (!name && el.getAttribute) name = el.getAttribute('download') || '';
    for (let i = 0; i < 6 && card; i++) {
      card = card.parentElement;
      if (!card) break;
      if (!thumb) {
        const img = card.querySelector('img');
        if (img && img.src) thumb = img.src;
        const bg = getComputedStyle(card).backgroundImage;
        const m = bg && bg.match(/url\\("?(.*?)"?\\)/);
        if (m) thumb = m[1];
      }
      if (!name) {
        const t = (card.innerText || '').trim().split('\\n')[0];
        if (t && t.length < 140) name = t;
      }
    }
    let size = 0;
    const szm = (document.body.innerText || '').match(
      new RegExp(name.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&') + '[^\\n]{0,40}(\\\\d[\\\\d.,]*\\\\s*(B|KB|MB|GB))', 'i'));
    out.push({url: u, name: name || u.split('/').pop().split('?')[0], thumb, size: 0,
              sizeText: szm ? szm[1] : ''});
  });
  // nama file sebagai teks di DOM (tanpa tautan langsung)
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const names = [];
  let node;
  while (node = walker.nextNode()) {
    const t = (node.nodeValue || '').trim();
    if (/\\.(mp4|mkv|webm|mp3|pdf|docx?|xlsx?|pptx?|zip|rar|7z|epub)\\s*$/i.test(t) && t.length < 140) {
      if (!seen.has('t:' + t)) { seen.add('t:' + t); names.push(t); }
    }
  }
  return {links: out, names};
}"""

URL_KEYS = ("url", "link", "href", "download_url", "file_url", "src", "source")
NAME_KEYS = ("name", "file_name", "filename", "title")
THUMB_KEYS = ("thumb", "thumbnail", "cover", "poster", "image", "preview")
SKIP_KEYS = ("thumb", "icon", "image", "avatar", "cover", "poster", "preview",
             "subtitle", "share", "logo")


def _first_url(obj, depth=0):
    """Ambil URL file pertama dari objek JSON (prioritas URL_KEYS)."""
    if depth > 8 or not isinstance(obj, dict):
        return None
    for k in URL_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.startswith("http") and \
                not any(s in k for s in SKIP_KEYS):
            return v
    for k, v in obj.items():
        if any(s in k.lower() for s in SKIP_KEYS):
            continue
        if isinstance(v, dict):
            r = _first_url(v, depth + 1)
            if r:
                return r
        elif isinstance(v, list):
            for it in v:
                r = _first_url(it, depth + 1) if isinstance(it, dict) else None
                if r:
                    return r
    return None


def _thumb_of(obj):
    for k in THUMB_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    return ""


def extract_json_files(obj, depth=0, out=None):
    """Cari objek JSON yang tampak seperti file digital (rekursif, kedalaman<=8)."""
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(obj, dict):
        if obj.get("kind") == "drive#folder":
            pass
        else:
            name = ""
            for k in NAME_KEYS:
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    name = v.strip()
                    break
            mime = str(obj.get("mime") or obj.get("mimeType") or "")
            is_file = name and name.split("?", 1)[0].lower().endswith(
                DOWNLOADABLE_EXT)
            is_video_mime = mime.startswith(("video/", "audio/")) or \
                content_type_is_file(mime)
            if (is_file or (name and is_video_mime)):
                url = _first_url(obj, depth + 1)
                size = obj.get("size") or obj.get("file_size") or 0
                try:
                    size = int(size)
                except (ValueError, TypeError):
                    size = 0
                out.append({"name": name, "url": url or "",
                            "thumb": _thumb_of(obj), "size": size})
        for v in obj.values():
            if isinstance(v, (dict, list)):
                extract_json_files(v, depth + 1, out)
    elif isinstance(obj, list):
        for it in obj:
            if isinstance(it, (dict, list)):
                extract_json_files(it, depth + 1, out)
    return out


def scan_ytdlp(url, job=None):
    """Pindai via yt-dlp extract_flat (playlist)."""
    try:
        import yt_dlp
    except ImportError:
        return []
    items = []
    try:
        with yt_dlp.YoutubeDL({"extract_flat": "in_playlist", "quiet": True,
                               "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        for e in (info or {}).get("entries") or []:
            if not e:
                continue
            items.append({
                "name": e.get("title") or e.get("id") or "tanpa nama",
                "url": e.get("url") or e.get("webpage_url") or "",
                "thumb": e.get("thumbnail") or "",
                "size": 0,
            })
    except Exception as ex:
        if job is not None:
            log(job, f"pindai yt-dlp gagal: {clean(str(ex))}")
    return items


def scan_once(url, headless, job=None):
    """Pindai via browser: tangkap JSON xhr/fetch + tautan/nama file di DOM."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright belum terpasang")
    items = []
    seen_names = set()

    def on_response(resp):
        try:
            if resp.request.resource_type not in ("xhr", "fetch"):
                return
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" not in ct:
                return
            data = resp.json()
            for f in extract_json_files(data):
                key = (f["name"], f["url"])
                if key not in seen_names and f["name"]:
                    seen_names.add(key)
                    items.append(f)
        except Exception:
            pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, args=[
            "--disable-blink-features=AutomationControlled", "--mute-audio"])
        ctx = browser.new_context(user_agent=UA)
        from sniffer import STEALTH_JS
        ctx.add_init_script(STEALTH_JS)
        page = ctx.new_page()
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        last_count = -1
        stable = 0
        t_end = time.time() + 25
        while time.time() < t_end:
            try:
                page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                time.sleep(1)
                data = page.evaluate(JS_SCAN)
                for link in data.get("links", []):
                    key = (link["name"], link["url"])
                    if key not in seen_names and link["name"]:
                        seen_names.add(key)
                        items.append({"name": link["name"], "url": link["url"],
                                      "thumb": link.get("thumb", ""),
                                      "size": 0,
                                      "sizeText": link.get("sizeText", "")})
                for nm in data.get("names", []):
                    if nm not in seen_names:
                        seen_names.add(nm)
                        items.append({"name": nm, "url": "", "thumb": "",
                                      "size": 0})
            except Exception:
                pass
            if len(items) == last_count:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
                last_count = len(items)
        browser.close()
    return items


def run_scan(url, job=None):
    """Jalankan pindai lengkap: yt-dlp dulu, fallback browser bila item < 2."""
    def lg(m):
        if job is not None:
            log(job, m)
    lg("memindai halaman …")
    items = scan_ytdlp(url, job)
    if len(items) < 2:
        for headless in (True, False):
            try:
                more = scan_once(url, headless, job)
                items.extend(m for m in more
                             if (m["name"], m["url"]) not in
                             {(i["name"], i["url"]) for i in items})
                if len(items) >= 2:
                    break
            except Exception as e:
                lg(f"pindai browser gagal: {clean(str(e))}")
    return finish_scan(items, job)


def finish_scan(items, job=None):
    """Bersihkan hasil: buang URL yang dipakai >1 item, lengkapi ukuran via HEAD."""
    # URL yang dipakai lebih dari 1 item tidak bisa dipercaya
    counts = {}
    for i in items:
        if i.get("url"):
            counts[i["url"]] = counts.get(i["url"], 0) + 1
    items = [i for i in items if not i.get("url") or counts[i["url"]] == 1]

    def head_size(item):
        if item.get("size") or not item.get("url"):
            return item
        try:
            req = urllib.request.Request(item["url"],
                                         headers={"User-Agent": UA},
                                         method="HEAD")
            with urllib.request.urlopen(req, timeout=8) as resp:
                ct = resp.headers.get("Content-Type", "")
                if content_type_is_file(ct):
                    cl = resp.headers.get("Content-Length")
                    if cl and cl.isdigit():
                        item["size"] = int(cl)
        except Exception:
            pass
        return item

    with ThreadPoolExecutor(max_workers=4) as ex:
        items = list(ex.map(head_size, items))
    # normalisasi tampilan
    for i in items:
        i["sizeText"] = i.get("sizeText") or (
            human_size(i["size"]) if i.get("size") else "")
    if job is not None:
        log(job, f"pindai selesai: {len(items)} file ditemukan")
    return items
