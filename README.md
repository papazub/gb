# 💧 Grabber — Pengunduh File Digital

Tool web lokal untuk mengunduh **semua jenis file digital** dari halaman web:
video (HLS/DASH/MP4), audio, PDF & dokumen office, ebook, arsip (zip/rar/7z),
gambar, dan file lainnya. Satu proses Python (Flask) + satu halaman HTML,
bisa diakses dari HP lewat Tailscale.

> **Batasan etika & legal (wajib dibaca):** tool ini **tidak mendukung dan tidak
> boleh dibuat untuk melewati DRM** (Widevine / PlayReady / FairPlay). Kandidat
> stream yang terdeteksi ber-DRM otomatis **dilewati**, bukan diunduh. Pakai tool
> ini hanya untuk konten yang memang boleh diunduh oleh pemakainya: milik
> sendiri, berlisensi terbuka, atau dengan izin eksplisit pemilik konten.
> Pengunduhan konten berhak cipta tanpa izin adalah tanggung jawab pengguna.

## Fitur

1. **Unduh dari URL halaman** — banyak URL sekaligus, satu per baris.
2. **Unduh file langsung** — URL yang sudah berupa file (PDF, zip, mp3, …)
   diunduh langsung tanpa browser.
3. **Sniffer browser** — buka halaman di Chromium (Playwright), putar media
   otomatis, tangkap request jaringan: playlist `.m3u8`/`.mpd`, file video
   langsung, **maupun file dokumen/audio/arsip/ebook/gambar** (dikenali dari
   content-type atau ekstensi, termasuk playlist yang ekstensinya disamarkan).
4. **Kualitas tertinggi otomatis** (video): klik pilihan kualitas di pemutar,
   ukur resolusi tiap kandidat, ambil yang terbesar (480/720/1080/4K).
5. **Mode "Tembus iklan" (Smart)**: blokir jaringan iklan, tutup pop-up/overlay,
   klik Skip Ad, buang stream iklan, pilih stream video terpanjang.
6. **Pindai halaman**: temukan banyak file dalam satu halaman (daftar file /
   playlist / JSON tersembunyi), tampilkan dengan checkbox, unduh yang dipilih.
7. **Input stream manual** (`master.m3u8`) + Referer + cookie dari browser.
8. **Video dipaksa jadi `.mp4`** (remux tanpa kehilangan kualitas; bila gagal,
   encode ulang H.264/AAC). **File non-video dipertahankan apa adanya**
   (PDF tetap PDF, ZIP tetap ZIP) dengan ekstensi aslinya.
9. **Deteksi file kembar** (fingerprint ukuran + SHA-1) dan penamaan
   `YYYYMMDD_HHMMSS.<ext>`.
10. **Antrean unduhan** (maks. 2 paralel) dengan progres, kecepatan, log,
    tombol simpan file.
11. **Catatan online** — scratchpad teks tersinkron antar perangkat.
12. **Bookmarklet** "Kirim ke Grabber" untuk PC.
13. **Akses jarak jauh**: mode server + password (Basic Auth) + skrip
    Tailscale Funnel.
14. **Video YouTube/Instagram privat** — selama kamu sudah punya aksesnya
    (video privat milikmu / dibagikan ke kamu, atau akun IG privat yang kamu
    follow): buka dulu di browser (login), lalu pilih "Cookie dari browser".
    Grabber memakai sesi loginmu sendiri — tidak membobol privasi orang lain.
    Bila gagal karena privat/login, pesan error kini memberi petunjuk ini.

## Cara menjalankan (Windows)

1. Klik dua kali **`run.bat`** — otomatis membuat venv, memasang paket,
   memasang Chromium, lalu menjalankan Grabber.
2. Browser terbuka sendiri di `http://127.0.0.1:8765`.
3. Tempel link → **Unduh** (atau **Tembus iklan** untuk halaman beriklan,
   atau **Pindai halaman** untuk daftar file).
4. Hasil tersimpan di folder `downloads/` dengan nama `YYYYMMDD_HHMMSS.<ext>`.

Akses dari HP: baca **`SERVER.txt`** (isi `password.txt` dulu, wajib!).

## Struktur proyek

```
grabber/
├── app.py            # titik masuk (python app.py)
├── web.py            # Flask: API + keamanan + halaman
├── downloader.py     # yt-dlp, unduh langsung, konversi, worker job
├── sniffer.py        # sniffer Playwright (video + file dokumen)
├── scan.py           # pindai halaman (yt-dlp + browser)
├── config.py         # konstanta: ekstensi, content-type, regex, keamanan
├── util.py           # sanitasi, fingerprint, penamaan, notes
├── static/index.html # UI berbahasa Indonesia (mobile-first)
├── requirements.txt
├── run.bat / run-server.bat / funnel-on.bat / funnel-off.bat
├── SERVER.txt        # petunjuk Tailscale/Funnel
├── password.txt      # kosong; isi satu baris untuk mengaktifkan password
├── downloads/        # hasil unduhan (dibuat otomatis)
└── notes.json        # catatan (dibuat otomatis)
```

## Kontrak API (ringkas)

| Method | Path | Catatan |
|---|---|---|
| POST | `/api/jobs` | `{url, stream, referer, browser, direct, pick, smart, size, name}` → `{id}`; 400 bila `url` & `stream` kosong |
| GET | `/api/jobs` | daftar job (terbaru dulu, log 8 baris terakhir) |
| POST | `/api/jobs/clear` | `{errors: bool}` → hapus job selesai/gagal |
| POST | `/api/scan` | `{url}` → `{id}` |
| GET | `/api/scan/<id>` | `{status, items:[{name,url,size,thumb}], log, error}`; 404 bila id tidak ada |
| GET/POST | `/api/notes` | `{text, rev}` / `{text}` → `{rev}` |
| GET | `/files/<name>` | unduh file hasil (mendukung lanjut unduh/Range) |
| GET | `/` | halaman UI |

Semua POST wajib `Content-Type: application/json`.

## Keamanan

- **Anti DNS-rebinding**: hanya menerima Host `127.0.0.1`/`localhost`
  (mode server: tambah `*.ts.net` dan `100.x.x.x`); selain itu HTTP 403.
- **Password** (bila `password.txt`/env `GRABBER_PASSWORD` terisi): HTTP Basic
  Auth dengan `hmac.compare_digest`; **wajib** diisi sebelum Funnel dibuka ke
  publik — aplikasi mencetak peringatan bila mode server tanpa password.
- **Anti-CSRF**: semua POST wajib JSON.
- Nama file selalu disanitasi; `send_from_directory` mencegah path traversal.
- Tidak ada eksekusi shell dari input pengguna (argumen ffmpeg/yt-dlp sebagai list).
- DRM (Widevine/PlayReady/FairPlay) dideteksi dari isi playlist dan dilewati.

## Kebutuhan

- Python 3.11+
- `pip install -r requirements.txt`
- `python -m playwright install chromium` (sudah otomatis di run.bat)
- ffmpeg (opsional; bila tidak ada di PATH, dipakai bawaan `imageio-ffmpeg`
  untuk konversi mp4)
