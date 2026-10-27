"""Flask web GUI. The scrapers need desktop Chromium (Playwright), so this
runs on the Windows PC; the PC's own browser and an Android phone's browser
on the same network are both just clients of it.
"""
from __future__ import annotations

import csv
import hmac
import io
import logging
import secrets
import shutil
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

from kinetik_stock.config import REPO_ROOT
from kinetik_stock.gui import jobs
from kinetik_stock.gui.jobs import Busy, JobManager

MAX_PREVIEW_ROWS = 5000

# The welcome photo is personal, so it lives in a gitignored folder on the PC
# running the GUI (never in the repo): private/greeting.<ext>.
PRIVATE_DIR = REPO_ROOT / "private"
BROWSER_IMAGES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
HEIF_EXTS = (".heic", ".heif")


def load_greeting_photo(private_dir: Path) -> tuple[bytes, str] | None:
    """Returns (bytes, mimetype) for private/greeting.*, or None if there's no
    usable photo. Browsers on Windows/Android can't show HEIF, so HEIC/HEIF
    files are converted to JPEG on the fly (needs `pip install pillow pillow-heif`).
    """
    for ext, mimetype in BROWSER_IMAGES.items():
        path = private_dir / f"greeting{ext}"
        if path.is_file():
            return path.read_bytes(), mimetype
    for ext in HEIF_EXTS:
        path = private_dir / f"greeting{ext}"
        if not path.is_file():
            continue
        try:
            import pillow_heif
            from PIL import Image, ImageOps

            pillow_heif.register_heif_opener()
            image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            image.thumbnail((900, 1200))
            buf = io.BytesIO()
            image.save(buf, "JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"
        except Exception:
            logging.getLogger(__name__).warning(
                "Couldn't read %s - run: pip install pillow pillow-heif", path, exc_info=True
            )
    return None


def create_app(
    username: str, password: str, output_dir: Path | None = None, private_dir: Path | None = None
) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
    manager = JobManager()
    app.extensions["job_manager"] = manager
    out_dir = output_dir or jobs.OUTPUT_DIR
    photo_dir = private_dir or PRIVATE_DIR

    def login_required(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if not session.get("ok"):
                if request.path.startswith("/api/"):
                    return jsonify(error="login required"), 401
                return redirect(url_for("login"))
            return view(*a, **kw)

        return wrapped

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            # compare both fields so timing doesn't reveal which one was wrong
            user_ok = hmac.compare_digest(request.form.get("username", "").strip().lower().encode(), username.lower().encode())
            pass_ok = hmac.compare_digest(request.form.get("password", "").encode(), password.encode())
            if user_ok and pass_ok:
                session["ok"] = True
                return redirect(url_for("welcome"))
            error = "Wrong email or password"
        return render_template("login.html", error=error), (401 if error else 200)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/welcome")
    @login_required
    def welcome():
        return render_template("welcome.html", has_photo=load_greeting_photo(photo_dir) is not None)

    # Served through a login-protected route (not /static) so the photo isn't
    # visible to anyone on the network without signing in.
    @app.get("/greeting-photo")
    @login_required
    def greeting_image():
        photo = load_greeting_photo(photo_dir)
        if photo is None:
            abort(404)
        return app.response_class(photo[0], mimetype=photo[1], headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/")
    @login_required
    def index():
        return render_template("index.html")

    @app.get("/api/brands")
    @login_required
    def api_brands():
        return jsonify(brands=jobs.brand_info())

    @app.post("/api/run")
    @login_required
    def api_run():
        body = request.get_json(silent=True) or {}
        known = {b["key"]: b for b in jobs.brand_info()}
        keys = body.get("brands")
        if not isinstance(keys, list) or not keys or not set(keys) <= set(known):
            return jsonify(error="pick one or more known brands"), 400
        if any(known[k]["coming_soon"] for k in keys):
            return jsonify(error="That brand is still in progress"), 400
        label = ", ".join(known[k]["name"] for k in keys) if len(keys) <= 2 else f"{len(keys)} brands"
        try:
            job = manager.start(label, jobs.make_brand_work(keys))
        except Busy as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(job=job.to_dict())

    @app.get("/api/job")
    @login_required
    def api_job():
        job = manager.current
        if job is None:
            return jsonify(job=None)
        return jsonify(job=job.to_dict(request.args.get("log_from", 0, type=int)))

    def csv_files() -> list[Path]:
        if not out_dir.is_dir():
            return []
        return sorted(out_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)

    def csv_path(name: str) -> Path:
        # Only files that actually sit in output/ - no path traversal.
        for p in csv_files():
            if p.name == name:
                return p
        abort(404)

    @app.get("/api/files")
    @login_required
    def api_files():
        return jsonify(files=[
            {"name": p.name, "size": p.stat().st_size,
             "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
            for p in csv_files()
        ])

    @app.get("/api/file/<name>")
    @login_required
    def api_file(name):
        path = csv_path(name)
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            columns = next(reader, [])
            rows = []
            for i, row in enumerate(reader):
                if i >= MAX_PREVIEW_ROWS:
                    break
                rows.append(row)
        return jsonify(columns=columns, rows=rows, truncated=len(rows) >= MAX_PREVIEW_ROWS)

    @app.get("/download/<name>")
    @login_required
    def download(name):
        return send_from_directory(out_dir, csv_path(name).name, as_attachment=True)

    # --- input SKU lists for the per-brand reports ---------------------------
    @app.get("/api/inputs")
    @login_required
    def api_inputs():
        result = []
        for key in jobs.REPORTS:
            path = jobs.input_csv_path(key)
            count = None
            if path.exists():
                with open(path, newline="", encoding="utf-8-sig") as f:
                    count = max(sum(1 for _ in f) - 1, 0)
            result.append({"brand": key, "exists": path.exists(), "rows": count})
        return jsonify(inputs=result)

    @app.get("/inputs/<key>")
    @login_required
    def download_input(key):
        if key not in jobs.REPORTS:
            abort(404)
        path = jobs.input_csv_path(key)
        if not path.exists():
            abort(404)
        return send_from_directory(path.parent, path.name, as_attachment=True)

    @app.post("/api/inputs/<key>")
    @login_required
    def upload_input(key):
        if key not in jobs.REPORTS:
            abort(404)
        if manager.current and manager.current.status in ("queued", "running"):
            return jsonify(error="A job is running - wait for it to finish"), 409
        upload = request.files.get("file")
        if upload is None:
            return jsonify(error="no file"), 400
        try:
            text = upload.read().decode("utf-8-sig")
            header = next(csv.reader(io.StringIO(text)), [])
        except (UnicodeDecodeError, csv.Error):
            return jsonify(error="That doesn't look like a UTF-8 CSV"), 400
        if "Manufact. SKU" not in header:
            return jsonify(error="CSV needs a 'Manufact. SKU' column (same layout as the current list)"), 400
        path = jobs.input_csv_path(key)
        if path.exists():
            shutil.copyfile(path, path.with_suffix(".csv.bak"))
        path.write_text(text, encoding="utf-8", newline="")
        return jsonify(ok=True)

    return app
