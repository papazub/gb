"""Utilitas bersama: sanitasi, log, fingerprint, penamaan, notes."""
import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime

from config import NOTES_PATH

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
BAD_NAME_RE = re.compile(r'[\\/:*?"<>|]')


def clean(text):
    """Buang kode warna ANSI dan potong agar aman untuk log/UI."""
    return ANSI_RE.sub("", str(text))[:300]


def now_hms():
    return datetime.now().strftime("%H:%M:%S")


def sanitize_name(name, maxlen=120):
    """Sanitasi nama file dari pengguna/server agar aman ditulis ke disk."""
    name = BAD_NAME_RE.sub("_", (name or "").strip()).strip(" .") or "file"
    if len(name) > maxlen:
        root, dot, ext = name.rpartition(".")
        name = (root[: maxlen - len(ext) - 1] + dot + ext) if dot else name[:maxlen]
    return name


def log(job, msg):
    """Tambah baris log ke job (thread-safe), format 'HH:MM:SS  pesan'."""
    line = f"{now_hms()}  {clean(msg)}"
    with job["_lock"]:
        job["log"].append(line)
        if len(job["log"]) > 200:
            job["log"] = job["log"][-200:]


def fingerprint(path):
    """Sidik jari file: ukuran + sha1(1MB awal, tengah, akhir)."""
    size = os.path.getsize(path)
    h = hashlib.sha1()
    h.update(str(size).encode())
    with open(path, "rb") as f:
        for pos in (0, max(0, size // 2 - 524288), max(0, size - 1048576)):
            f.seek(pos)
            h.update(f.read(1048576))
    return f"{size}:{h.hexdigest()}"


def next_timestamp_name(out_dir, ext):
    """Nama unik YYYYMMDD_HHMMSS[.ext]; tambah _2, _3 bila bentrok."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}{ext}"
    n = 1
    while os.path.exists(os.path.join(out_dir, name)):
        n += 1
        name = f"{stamp}_{n}{ext}"
    return name


def human_size(num):
    if num is None:
        return "?"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}"
        num /= 1024


# ------------------------------------------------------------------- notes
_notes_lock = threading.Lock()


def read_notes():
    try:
        with open(NOTES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"text": data.get("text", ""), "rev": int(data.get("rev", 0))}
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return {"text": "", "rev": 0}


def write_notes(text):
    """Tulis atomik (.tmp lalu os.replace), maks 200.000 karakter, return rev baru."""
    with _notes_lock:
        cur = read_notes()
        text = (text or "")[:200000]
        rev = cur["rev"] + 1
        fd, tmp = tempfile.mkstemp(dir=str(NOTES_PATH.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"text": text, "rev": rev}, f, ensure_ascii=False)
            os.replace(tmp, NOTES_PATH)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return rev
