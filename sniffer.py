"""Sniffer browser (Playwright): tangkap stream video (HLS/DASH) dan file
digital langsung (dokumen/audio/arsip/ebook/gambar) dari halaman web."""
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from config import (UA, PLAYLIST_RE, SEGMENT_RE, FILE_RE, DOWNLOADABLE_EXT,
                    VIDEO_EXT, AUDIO_EXT, IMAGE_EXT, MAIN_MIN, AD_HOST_RE,
                    AD_PATH_RE, PLAY_SELECTORS, content_type_is_file,
                    media_of_url)
from util import log, clean

MIN_DIRECT_BYTES = 256 * 1024  # abaikan file kecil (ikon, thumbnail)

# ------------------------------------------------------------- JS snippets
STEALTH_JS = """() => {
  Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
  window.chrome = {runtime: {}};
}"""

JS_DISMISS_COOKIES = """() => {
  const sels = ['#onetrust-accept-btn-handler', '.cc-allow', '[data-testid="cookie-accept"]',
    '[aria-label*="accept" i]', '[id*="cookie" i] button', '[class*="cookie" i] button'];
  for (const s of sels) { const el = document.querySelector(s); if (el) { el.click(); return 1; } }
  const texts = ['accept all', 'terima semua', 'setuju', 'allow all', '同意', 'aceptar', 'accepter',
    'accetta', 'alle akzeptieren', 'aceitar'];
  for (const b of document.querySelectorAll('button, a')) {
    const t = (b.innerText || '').trim().toLowerCase();
    if (texts.includes(t)) {
      const box = b.closest('[id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i]');
      if (box || ['ok', 'ya', 'got it'].includes(t)) { b.click(); return 1; }
    }
  }
  return 0;
}"""

JS_SKIP = """(pageUrl) => {
  let n = 0;
  for (const el of document.querySelectorAll('button, div, span, a')) {
    const t = (el.innerText || '').trim().toLowerCase();
    if (/^(skip ad|lewati iklan|skip|lewati)$/.test(t) && el.offsetParent) { el.click(); n++; }
  }
  document.querySelectorAll('ins.adsbygoogle').forEach(e => e.remove());
  for (const el of document.querySelectorAll('div, section')) {
    const cs = getComputedStyle(el);
    if ((cs.position === 'fixed' || cs.position === 'absolute') && parseInt(cs.zIndex || 0) >= 50) {
      const r = el.getBoundingClientRect();
      const cover = (r.width * r.height) / (innerWidth * innerHeight);
      if (cover >= 0.7 && !el.querySelector('video') && !/player|video/i.test(el.className)) {
        el.remove(); n++;
      }
    }
  }
  if (location.href !== pageUrl) { location.href = pageUrl; return -1; }
  return n;
}"""

JS_PLAY = """() => {
  let n = 0;
  document.querySelectorAll('video').forEach(v => {
    v.muted = true; v.play().catch(() => {}); n++;
  });
  return n;
}"""

JS_QUALITY = """() => {
  const num = s => { const m = String(s).match(/^(\\d{3,4})\\s*p?/i); if (m) return +m[1];
    s = String(s).toLowerCase();
    if (/4k|uhd/.test(s)) return 2160; if (/2k|qhd/.test(s)) return 1440;
    if (/fhd|full\\s*hd|1080/.test(s)) return 1080; if (/hd|720/.test(s)) return 720;
    return 0; };
  try {
    // 1) JW Player
    if (window.jwplayer) { const p = jwplayer(); const q = p.getQualityLevels();
      if (q && q.length) { let bi = 0, bh = 0;
        q.forEach((l, i) => { const h = l.height || num(l.label); if (h > bh) { bh = h; bi = i; } });
        p.setCurrentQuality(bi); return 'jw:' + bh; } }
    // 2) hls.js
    for (const k of ['hls', 'player', 'hlsPlayer']) {
      const h = window[k] && (window[k].hls || window[k]);
      if (h && h.levels && h.levels.length) { let bi = 0, bh = 0;
        h.levels.forEach((l, i) => { const hh = l.height || 0; if (hh > bh) { bh = hh; bi = i; } });
        h.currentLevel = bi; return 'hls:' + bh; } }
    // 3) <select> kualitas
    for (const sel of document.querySelectorAll('select')) {
      const opts = [...sel.options].map(o => ({t: o.text, v: o.value, h: num(o.text)}))
        .filter(o => o.h > 0);
      if (opts.length >= 2) { opts.sort((a, b) => b.h - a.h);
        sel.value = opts[0].v; sel.dispatchEvent(new Event('change', {bubbles: true}));
        return 'select:' + opts[0].h; } }
    // 4) menu tombol (video.js, Plyr, dsb.)
    for (const g of document.querySelectorAll('[class*="setting" i], [class*="gear" i], [aria-label*="ualit" i], [aria-label*="setting" i]')) {
      if (!g.offsetParent) continue; g.click();
      const items = [...document.querySelectorAll('li, div[role="menuitem"], button')]
        .map(e => ({e, h: num(e.innerText)})).filter(x => x.h > 0 && !x.e.closest('a[href]'));
      if (items.length >= 2) { items.sort((a, b) => b.h - a.h); items[0].e.click(); return 'menu:' + items[0].h; }
      break;
    }
  } catch (e) { return 'err'; }
  return '';
}"""

JS_CLICK_TEXT = """(txt) => {
  const els = [...document.querySelectorAll('a, button, span, div')]
    .filter(e => (e.innerText || '').trim() === txt && e.offsetParent);
  if (els.length) { els[0].click(); return 1; }
  const part = [...document.querySelectorAll('a, button, span, div')]
    .filter(e => (e.innerText || '').includes(txt) && e.offsetParent);
  if (part.length) { part[0].click(); return 1; }
  return 0;
}"""


# ------------------------------------------------------------- kandidat
def _classify(url, content_type=""):
    if PLAYLIST_RE.search(url.split("?", 1)[0]):
        return "playlist", "video"
    if content_type_is_file(content_type):
        return "direct", media_of_url(url)
    if url.split("?", 1)[0].lower().endswith(DOWNLOADABLE_EXT):
        return "direct", media_of_url(url)
    return None, None


def _rank(kind, media):
    # named(-1) > playlist master(0) > playlist(1) > direct video(2) >
    # direct audio(3) > direct dokumen/dll(4)
    if kind == "named":
        return -1
    if kind == "playlist":
        return 0
    if kind == "direct":
        return {"video": 2, "audio": 3}.get(media, 4)
    return 5


def probe_height(url, kind, headers):
    """Ukur tinggi resolusi kandidat video; None bila gagal."""
    try:
        if kind == "playlist":
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       **(headers or {})})
            txt = urllib.request.urlopen(req, timeout=20).read(500000).decode(
                "utf-8", "ignore")
            if txt.lstrip().startswith("<MPD"):
                hs = [int(x) for x in re.findall(r'height="(\d+)"', txt)]
                return max(hs) if hs else None
            hs = [int(m.group(2)) for m in
                  re.finditer(r"RESOLUTION=\d+x(\d+)", txt)]
            if hs:
                return max(hs)
            return 5000  # master tanpa RESOLUTION -> anggap terbaik
        from downloader import FFMPEG
        if not FFMPEG:
            return None
        import subprocess
        r = subprocess.run([FFMPEG, "-i", url], capture_output=True, text=True,
                           timeout=60)
        m = re.search(r"Video:.*?\s(\d+)x(\d+)", r.stderr)
        return int(m.group(2)) if m else None
    except Exception:
        return None


def probe_size(url, headers):
    """Ukur ukuran file via HEAD; None bila gagal."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, **(headers or {})}, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as resp:
            cl = resp.headers.get("Content-Length")
            return int(cl) if cl and cl.isdigit() else None
    except Exception:
        return None


def _measure(candidates):
    """Ukur resolusi (video) / ukuran (dokumen) tiap kandidat secara paralel."""
    def work(c):
        if c.get("kind") == "playlist" or c.get("media") == "video":
            c["height"] = probe_height(c["url"], c.get("kind"),
                                       c.get("headers"))
        else:
            c["size"] = probe_size(c["url"], c.get("headers"))
        return c
    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(work, candidates))


def _sort(candidates):
    def key(c):
        r = _rank(c.get("kind"), c.get("media"))
        metric = c.get("height") or 0
        if c.get("media") not in ("video",) or not metric:
            metric = (c.get("size") or 0) // 1024  # ukuran dalam KB
        return (r, -metric)
    return sorted(candidates, key=key)


# ------------------------------------------------------------- sniffer utama
def sniff(url, job=None, smart=False, pick=None):
    """Coba 2x: headless dulu, lalu jendela terlihat. Return daftar kandidat."""
    def lg(m):
        if job is not None:
            log(job, m)
    lg(f"membuka halaman ({'smart' if smart else 'biasa'}) …")
    try:
        cands = sniff_once(url, headless=True, smart=smart, pick=pick, job=job)
        if cands:
            return _finish(cands, job)
    except Exception as e:
        lg(f"percobaan headless gagal: {clean(str(e))}")
    lg("coba lagi dengan jendela terlihat …")
    try:
        cands = sniff_once(url, headless=False, smart=smart, pick=pick, job=job)
        return _finish(cands, job)
    except Exception as e:
        lg(f"sniffer gagal: {clean(str(e))}")
        return []


def _finish(candidates, job):
    if not candidates:
        return []
    if len(candidates) > 1:
        if job is not None:
            log(job, f"mengukur {len(candidates)} kandidat …")
        candidates = _measure(candidates)
        candidates = _sort(candidates)
        if job is not None:
            for c in candidates[:5]:
                desc = (f"{c.get('height')}p" if c.get("height")
                        else (f"{(c.get('size') or 0) // 1024} KB"
                              if c.get("size") else "?"))
                log(job, f"kandidat: {c.get('media')}/{c.get('kind')} {desc} "
                         f"{c['url'][:80]}")
    return candidates


def sniff_once(url, headless, smart, pick, job):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright belum terpasang "
                           "(pip install -r requirements.txt; "
                           "python -m playwright install chromium)")

    hits = []          # kandidat tertangkap
    seen = []          # 6 request terakhir (diagnosis)
    req_headers = {}   # url -> header referer/origin/ua
    total_size = {}    # url -> ukuran total dari header
    durations = {}     # url -> durasi (smart)
    qa = [False]       # kualitas tertinggi sudah dipilih
    clicked = [0]      # hitungan klik (mode pick)

    def lg(m):
        if job is not None:
            log(job, m)

    def add(u, kind, media, headers, cookie_str):
        for h in hits:
            if h["url"] == u:
                return
        heads = {}
        rh = req_headers.get(u, {})
        for k in ("referer", "origin", "user-agent"):
            if rh.get(k):
                heads[k.title()] = rh[k]
        if cookie_str:
            heads["Cookie"] = cookie_str
        heads.update(headers or {})
        hits.append({"url": u, "kind": kind, "media": media,
                     "headers": heads, "dur": durations.get(u)})

    def on_request(req):
        u = req.url
        seen.append(u)
        if len(seen) > 6:
            seen.pop(0)
        for k, v in req.headers.items():
            if k.lower() in ("referer", "origin", "user-agent"):
                req_headers.setdefault(u, {})[k.lower()] = v
        if PLAYLIST_RE.search(u):
            add(u, "playlist", "video", None, None)

    def on_response(resp):
        try:
            u = resp.url
            if SEGMENT_RE.search(u):
                return
            ct = (resp.headers.get("content-type") or "").lower()
            status = resp.status
            if status not in (200, 206):
                return
            # ukuran total dari header
            cr = resp.headers.get("content-range", "")
            m = re.search(r"/(\d+)\s*$", cr)
            if m:
                total_size[u] = int(m.group(1))
            elif resp.headers.get("content-length", "").isdigit():
                total_size[u] = int(resp.headers["content-length"])
            if PLAYLIST_RE.search(u) or "mpegurl" in ct or "dash+xml" in ct:
                add(u, "playlist", "video", None, None)
                return
            # playlist yang ekstensinya disamarkan (.woff/.txt/dll) -> cek isi
            if resp.request.resource_type in ("xhr", "fetch", "other"):
                try:
                    body = resp.body()[:200]
                    if body.startswith(b"#EXTM3U") or body.startswith(b"<MPD"):
                        add(u, "playlist", "video", None, None)
                        return
                except Exception:
                    pass
            # file langsung: video ATAU dokumen (content-type/ekstensi)
            kind, media = _classify(u, ct)
            if kind == "direct":
                size = total_size.get(u, 0)
                rt = resp.request.resource_type
                if media == "video":
                    if (rt == "media" or content_type_is_file(ct)) \
                            and size >= 1024 * 1024:
                        add(u, "direct", media, None, None)
                else:
                    # dokumen/audio/arsip/gambar: cukup besar -> tangkap
                    if size >= MIN_DIRECT_BYTES:
                        add(u, "direct", media, None, None)
                    elif u.split("?", 1)[0].lower().endswith(
                            DOWNLOADABLE_EXT) and rt in ("document", "other"):
                        add(u, "direct", media, None, None)
        except Exception:
            pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, args=[
            "--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required",
            "--mute-audio"])
        ctx = browser.new_context(user_agent=UA,
                                  viewport={"width": 1280, "height": 720})
        ctx.add_init_script(STEALTH_JS)
        if smart:
            ctx.add_init_script("window.open=()=>null;")
            ctx.route("**/*", lambda route: _route_block(route))
        page = ctx.new_page()
        ctx.on("page", lambda pg: pg.close() if pg != page else None)
        page.on("request", on_request)
        page.on("response", on_response)

        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page_url = page.url
        tick = 0
        first_hit = None
        strong_since = None
        hijacks = 0
        t_end = time.time() + (60 if smart else 35)
        cookie_tries = 0

        while time.time() < t_end:
            tick += 1
            # 1) tutup banner cookie
            if cookie_tries < 6:
                try:
                    page.evaluate(JS_DISMISS_COOKIES)
                except Exception:
                    pass
                cookie_tries += 1
            # 2) smart: skip iklan, tutup overlay, tangani tab dibajak
            if smart:
                try:
                    r = page.evaluate(JS_SKIP, page_url)
                    if r == -1:
                        hijacks += 1
                        if hijacks > 3:
                            break
                        lg("tab dibajak iklan, kembali ke halaman …")
                        continue
                except Exception:
                    pass
                # ukur durasi kandidat baru
                for h in hits:
                    if h["url"] not in durations and h.get("media") == "video":
                        d = _quick_duration(h["url"])
                        if d:
                            durations[h["url"]] = d
                            h["dur"] = d
                            if d >= MAIN_MIN and strong_since is None:
                                strong_since = time.time()
            # 3) putar media otomatis
            try:
                page.evaluate(JS_PLAY)
                if tick % 2 == 1:
                    for sel in PLAY_SELECTORS:
                        try:
                            el = page.query_selector(sel)
                            if el and el.is_visible():
                                el.click(timeout=500)
                                break
                        except Exception:
                            continue
                if tick % 3 == 0:
                    try:
                        page.mouse.click(640, 360)
                    except Exception:
                        pass
            except Exception:
                pass
            # mode pick: klik nama file saja, tanpa klik acak
            if pick and tick % 2 == 0 and clicked[0] < 3:
                try:
                    if clicked[0] == 0:
                        hits.clear()
                    n = page.evaluate(JS_CLICK_TEXT, pick)
                    if n:
                        if clicked[0] % 2 == 1:  # klik ganda di percobaan genap
                            page.evaluate(JS_CLICK_TEXT, pick)
                        clicked[0] += 1
                        lg(f"klik '{pick}' ({clicked[0]}/3) …")
                        time.sleep(2)
                except Exception:
                    pass
            elif not pick and tick == 4:
                # halaman daftar file: klik teks nama file digital
                try:
                    for el in page.query_selector_all("a"):
                        t = (el.inner_text() or "").strip()
                        if FILE_RE.search(t):
                            el.click(timeout=800)
                            lg(f"klik tautan file: {t[:60]}")
                            break
                except Exception:
                    pass
            # 4) pilih kualitas tertinggi (video) di tick 3,5,8
            if not qa[0] and tick in (3, 5, 8) and not pick:
                try:
                    for frame in page.frames:
                        label = frame.evaluate(JS_QUALITY)
                        if label:
                            qa[0] = True
                            lg(f"kualitas tertinggi dipilih ({label})")
                            break
                except Exception:
                    pass
            if hits and first_hit is None:
                first_hit = time.time()
            # 5) kondisi berhenti
            if hits and not pick:
                age = time.time() - first_hit
                if smart:
                    strong = any((h.get("dur") or 0) >= MAIN_MIN for h in hits)
                    if (strong and age >= 3) or age >= 40:
                        break
                    if age >= 15 and any(h.get("dur") for h in hits):
                        break
                else:
                    settled = tick >= 5 and (time.time() - first_hit) >= 7 \
                        if qa[0] else tick >= 5
                    if settled and age >= 3:
                        break
            if pick and clicked[0] >= 3 and hits:
                break
            time.sleep(1.2)

        # lengkapi cookie untuk tiap kandidat
        try:
            cookies = ctx.cookies()
        except Exception:
            cookies = []
        for h in hits:
            try:
                from urllib.parse import urlparse
                host = urlparse(h["url"]).hostname or ""
                jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                                if host.endswith(c.get("domain", "").lstrip(".")))
                if jar:
                    h["headers"]["Cookie"] = jar
            except Exception:
                pass
        if not hits:
            lg("tidak ada yang tertangkap. 6 request terakhir:")
            for u in seen[-6:]:
                lg("  " + u[:120])
        browser.close()
    return hits


def _route_block(route):
    """Blokir request iklan di mode smart (fulfill 204 agar halaman tetap)."""
    try:
        u = route.request.url.lower()
        host = u.split("/")[2] if "/" in u else ""
        if AD_HOST_RE.search(host) or AD_PATH_RE.search(u.split("?")[0]):
            route.fulfill(status=204, body="")
            return
    except Exception:
        pass
    route.continue_()


def _quick_duration(url):
    """Ukur durasi stream cepat via header/ffmpeg; None bila gagal."""
    try:
        from downloader import FFMPEG, _probe_duration
        return _probe_duration(url) if FFMPEG else None
    except Exception:
        return None
