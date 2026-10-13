"""Flask app Grabber: API JSON + halaman UI + keamanan (bagian 11 blueprint)."""
import hmac
import threading
import time
import uuid

from flask import Flask, request, jsonify, send_from_directory, Response

from config import (OUT, PORT, SERVER_MODE, APP_TITLE, load_password)
from util import log, clean, read_notes, write_notes
from downloader import start_worker

jobs = {}    # id -> job dict (in-memory, hilang saat restart)
scans = {}   # id -> scan dict
_jobs_lock = threading.Lock()

PASSWORD = load_password()


def _new_job(data):
    jid = uuid.uuid4().hex[:8]
    job = {
        "id": jid, "created": time.time(),
        "url": data.get("url", ""), "stream": data.get("stream", ""),
        "referer": data.get("referer", ""), "browser": data.get("browser", ""),
        "direct": data.get("direct", ""), "pick": data.get("pick", ""),
        "smart": bool(data.get("smart")), "size": data.get("size") or 0,
        "name": data.get("name", ""),
        "status": "queued", "progress": None, "done_bytes": 0,
        "total_bytes": 0, "est": False, "speed_bps": 0,
        "started": 0, "finished": 0, "file": "", "error": "", "orig": "",
        "log": [], "_lock": threading.Lock(),
    }
    try:
        job["size"] = int(job["size"])
    except (ValueError, TypeError):
        job["size"] = 0
    return job


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    # ------------------------------------------------- keamanan
    @app.before_request
    def _guard():
        # 1) anti DNS-rebinding: batasi Host yang diterima
        host = (request.host or "").split(":")[0].lower()
        ok_local = host in ("127.0.0.1", "localhost", "::1")
        ok_server = (SERVER_MODE and
                     (host.endswith(".ts.net") or host.startswith("100.")))
        if not (ok_local or ok_server):
            return Response("Host tidak diizinkan (403)", status=403)
        # 2) password (Basic Auth) bila diaktifkan
        if PASSWORD:
            auth = request.authorization
            if not auth or not hmac.compare_digest(auth.password or "",
                                                   PASSWORD):
                return Response(
                    "Butuh kata sandi",
                    status=401,
                    headers={"WWW-Authenticate": 'Basic realm="Grabber"'})
        # 3) anti-CSRF: semua POST wajib JSON
        if request.method == "POST":
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({"error": "POST harus application/json"}), 400
            request._json = data

    # ------------------------------------------------- halaman utama
    @app.route("/")
    def index():
        return send_from_directory("static", "index.html")

    # ------------------------------------------------- jobs
    @app.route("/api/jobs", methods=["POST"])
    def api_jobs_post():
        data = request._json
        if not data.get("url") and not data.get("stream"):
            return jsonify({"error": "url atau stream wajib diisi"}), 400
        job = _new_job(data)
        with _jobs_lock:
            jobs[job["id"]] = job
        log(job, f"job dibuat: {(job['url'] or job['stream'])[:100]}")
        start_worker(job)
        return jsonify({"id": job["id"]})

    @app.route("/api/jobs", methods=["GET"])
    def api_jobs_get():
        out = []
        with _jobs_lock:
            ordered = sorted(jobs.values(), key=lambda j: j["created"],
                             reverse=True)
        for j in ordered:
            with j["_lock"]:
                item = {k: v for k, v in j.items() if not k.startswith("_")}
                item["log"] = item["log"][-8:]
            item["elapsed"] = round(time.time() - j["created"], 1)
            out.append(item)
        return jsonify(out)

    @app.route("/api/jobs/clear", methods=["POST"])
    def api_jobs_clear():
        data = request._json
        with _jobs_lock:
            drop = [jid for jid, j in jobs.items()
                    if j["status"] == "done"
                    or (data.get("errors") and j["status"] == "error")]
            for jid in drop:
                del jobs[jid]
        return jsonify({"ok": True, "cleared": len(drop)})

    # ------------------------------------------------- scan
    @app.route("/api/scan", methods=["POST"])
    def api_scan_post():
        data = request._json
        if not data.get("url"):
            return jsonify({"error": "url wajib diisi"}), 400
        sid = uuid.uuid4().hex[:8]
        scan = {"id": sid, "url": data["url"], "status": "running",
                "items": [], "log": [], "error": "",
                "_lock": threading.Lock()}
        scans[sid] = scan

        def _run():
            from scan import run_scan
            try:
                items = run_scan(data["url"], scan)
                with scan["_lock"]:
                    scan["items"] = items
                    scan["status"] = "done"
            except Exception as e:
                with scan["_lock"]:
                    scan["status"] = "error"
                    scan["error"] = clean(str(e))

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"id": sid})

    @app.route("/api/scan/<sid>", methods=["GET"])
    def api_scan_get(sid):
        scan = scans.get(sid)
        if not scan:
            return jsonify({"error": "scan tidak ditemukan"}), 404
        with scan["_lock"]:
            item = {k: v for k, v in scan.items() if not k.startswith("_")}
            item["log"] = item["log"][-8:]
        return jsonify(item)

    # ------------------------------------------------- notes
    @app.route("/api/notes", methods=["GET"])
    def api_notes_get():
        resp = jsonify(read_notes())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/api/notes", methods=["POST"])
    def api_notes_post():
        data = request._json
        rev = write_notes(data.get("text", ""))
        return jsonify({"rev": rev})

    # ------------------------------------------------- file hasil
    @app.route("/files/<name>")
    def api_files(name):
        return send_from_directory(str(OUT), name, as_attachment=True,
                                   conditional=True)

    @app.route("/api/health")
    def api_health():
        return jsonify({"ok": True, "title": APP_TITLE,
                        "server_mode": SERVER_MODE})

    return app


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # bersihkan sisa folder kerja sementara dari sesi sebelumnya
    for p in OUT.iterdir():
        if p.name.startswith(("_tmp_", "debug_")):
            import shutil
            shutil.rmtree(p, ignore_errors=True)
    app = create_app()
    bind = "0.0.0.0" if SERVER_MODE else "127.0.0.1"
    if SERVER_MODE and not PASSWORD:
        print("PERINGATAN: mode server tanpa password! Isi password.txt "
              "sebelum membuka akses publik (Funnel).")
    print(f"Grabber jalan di http://{bind}:{PORT}")
    try:
        from waitress import serve
        serve(app, host=bind, port=PORT, threads=12, send_bytes=262144,
              channel_timeout=900, connection_limit=200)
    except ImportError:
        print("waitress tidak ada, pakai server dev Flask")
        app.run(host=bind, port=PORT, threaded=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    # buka browser otomatis 1,5 dtk setelah start (mode lokal saja)
    if not SERVER_MODE:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(
            f"http://127.0.0.1:{PORT}")).start()
    main()
