"""Konfigurasi & konstanta Grabber.

Grabber adalah tool pengunduh file digital (video, audio, dokumen, ebook,
arsip, gambar, playlist HLS/DASH) dari halaman web untuk pemakaian pribadi.
"""
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "downloads"
NOTES_PATH = BASE / "notes.json"
PASSWORD_FILE = BASE / "password.txt"

PORT = 8765
SERVER_MODE = os.environ.get("GRABBER_SERVER", "") == "1"

# stream lebih pendek dari ini dianggap iklan bila ada kandidat yang lebih panjang
MAIN_MIN = 120  # detik
# hasil smart yang lebih pendek dari ini dibuang bila masih ada kandidat lain
AD_MAX_LEN = 45  # detik

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- ekstensi file
VIDEO_EXT = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv",
             ".3gp", ".wmv", ".mpg", ".mpeg")
AUDIO_EXT = (".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac",
             ".wma")
DOC_EXT = (".pdf", ".doc", ".docx", ".odt", ".rtf",
           ".xls", ".xlsx", ".ods",
           ".ppt", ".pptx", ".odp",
           ".txt", ".csv", ".tsv", ".md", ".json", ".xml", ".log")
EBOOK_EXT = (".epub", ".mobi", ".azw", ".azw3", ".fb2")
ARCHIVE_EXT = (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
             ".tiff", ".tif", ".avif", ".ico", ".psd", ".ai")
PLAYLIST_EXT = (".m3u8", ".mpd")

# semua ekstensi file digital yang boleh ditangkap sebagai unduhan langsung
DOWNLOADABLE_EXT = (VIDEO_EXT + AUDIO_EXT + DOC_EXT + EBOOK_EXT
                    + ARCHIVE_EXT + IMAGE_EXT)

# regex nama file (untuk klik teks di mode pick & pindai DOM)
FILE_RE = re.compile(
    r"\.(" + "|".join(e.lstrip(".") for e in DOWNLOADABLE_EXT) + r")\s*$",
    re.IGNORECASE)

PLAYLIST_RE = re.compile(r"\.(m3u8|mpd)(\?|$)", re.IGNORECASE)
SEGMENT_RE = re.compile(r"\.(m4s|ts)(\?|$)|seg_", re.IGNORECASE)
DRM_PAT = re.compile(
    r"widevine|playready|fairplay|skd://|ContentProtection|streamingkeydelivery",
    re.IGNORECASE)

# ------------------------------------------------------- content-type langsung
# content-type yang dianggap "file digital yang bisa diunduh", bukan halaman web
DIRECT_CT = {
    "application/pdf",
    "application/msword",
    "application/rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.ms-word",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/epub+zip",
    "application/x-mobipocket-ebook",
    "application/x-fictionbook+xml",
    "application/zip",
    "application/x-zip-compressed",
    "application/x-rar-compressed",
    "application/vnd.rar",
    "application/x-7z-compressed",
    "application/gzip",
    "application/x-tar",
    "application/x-bzip2",
    "text/csv",
    "text/plain",
    "text/markdown",
    "application/octet-stream",
    "binary/octet-stream",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/bmp",
    "image/tiff",
    "image/avif",
    "image/vnd.adobe.photoshop",
}
# prefix tambahan yang selalu dianggap file langsung (video/*, audio/*)
DIRECT_CT_PREFIX = ("video/", "audio/")


def content_type_is_file(ct):
    """True bila content-type menandakan file digital yang bisa diunduh."""
    if not ct:
        return False
    ct = ct.lower().split(";")[0].strip()
    return ct in DIRECT_CT or ct.startswith(DIRECT_CT_PREFIX)


def media_of_url(url):
    """Klasifikasi kasar media dari ekstensi URL: video|audio|doc."""
    path = (url or "").split("?", 1)[0].lower()
    if path.endswith(VIDEO_EXT) or PLAYLIST_RE.search(path):
        return "video"
    if path.endswith(AUDIO_EXT):
        return "audio"
    return "doc"


def is_downloadable_url(url):
    """True bila path URL berakhiran ekstensi file digital yang didukung."""
    path = (url or "").split("?", 1)[0].lower()
    return path.endswith(DOWNLOADABLE_EXT)


# ----------------------------------------------------------------- keamanan
def load_password():
    """Password dari env GRABBER_PASSWORD, fallback baris pertama password.txt."""
    env = os.environ.get("GRABBER_PASSWORD", "").strip()
    if env:
        return env
    try:
        text = PASSWORD_FILE.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return ""
    return text.splitlines()[0].strip() if text else ""


# ----------------------------------------------------------------- sniffer
# pola host jaringan iklan
AD_HOST_RE = re.compile(
    r"(doubleclick|googlesyndication|imasdk|2mdn|taboola|popads|propellerads|"
    r"exoclick|adsterra|hilltopads|criteo|pubmatic|mgid|monetag|adnxs|rubicon|"
    r"adsrvr|outbrain|revcontent|popcash|adcash|clickadu|adcolony|vungle|"
    r"unityads|applovin|ironsource|chartboost|inmobi|smaato|mopub|fyber)",
    re.IGNORECASE)
# pola path iklan (VAST/VPAID/preroll/dll)
AD_PATH_RE = re.compile(
    r"(^|[/_.-])(vast|vmap|vpaid|preroll|midroll|postroll|adserver|"
    r"advert(s|ising)?|ads?)([/_.-]|$)", re.IGNORECASE)

# selector tombol play umum
PLAY_SELECTORS = [
    ".jw-icon-display", ".vjs-big-play-button", ".plyr__control--overlaid",
    ".play-button", ".xgplayer-start", ".art-state", ".dplayer-play-icon",
    "[class*='play-btn' i]", "[class*='big-play' i]",
    "button[aria-label*='play' i]", "button[aria-label*='putar' i]",
    "[class*='vjs-play-control']",
]

# ----------------------------------------------------------------- UI
APP_TITLE = "Grabber"
