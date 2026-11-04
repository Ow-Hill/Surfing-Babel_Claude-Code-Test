"""Surfing Babel - web UI for the Babelia slideshow scanner.

Run:  python app.py   (then open http://localhost:5000)
"""

from __future__ import annotations

import logging
import os
import time

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from babel_surfer.classifier import DEFAULT_THRESHOLD, classify
from babel_surfer.scraper import SLIDESHOW_URL, Scraper
from babel_surfer.storage import Store

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))


def create_app(data_dir: str = DATA_DIR) -> Flask:
    app = Flask(__name__)
    store = Store(data_dir)
    scraper = Scraper(store,
                      threshold=float(os.environ.get("THRESHOLD", DEFAULT_THRESHOLD)),
                      interval=float(os.environ.get("INTERVAL", 1.0)))
    app.config["store"] = store
    app.config["scraper"] = scraper

    @app.get("/")
    def index():
        return render_template("index.html", slideshow_url=SLIDESHOW_URL)

    @app.get("/images/<path:name>")
    def image(name):
        return send_from_directory(store.image_dir, name)

    @app.get("/api/finds")
    def finds():
        limit = min(int(request.args.get("limit", 200)), 1000)
        offset = int(request.args.get("offset", 0))
        return jsonify(store.list_finds(limit, offset))

    @app.get("/api/finds/<int:find_id>")
    def find(find_id):
        f = store.get_find(find_id)
        return jsonify(f) if f else abort(404)

    @app.delete("/api/finds/<int:find_id>")
    def delete(find_id):
        return ("", 204) if store.delete_find(find_id) else abort(404)

    @app.get("/api/status")
    def status():
        s = scraper.state
        return jsonify({
            "running": s.running, "status": s.status, "error": s.last_error,
            "last_frame": s.last_frame, "session_scanned": s.session_scanned,
            "session_found": s.session_found, "recent_scores": s.recent,
            "uptime": time.time() - s.started_at if s.started_at and s.running else 0,
            "threshold": scraper.threshold, "totals": store.stats(),
        })

    @app.post("/api/scraper/start")
    def start():
        scraper.start()
        return jsonify({"ok": True})

    @app.post("/api/scraper/stop")
    def stop():
        scraper.stop()
        return jsonify({"ok": True})

    @app.post("/api/threshold")
    def threshold():
        value = float((request.get_json(silent=True) or {}).get("threshold", 0))
        if value <= 1.0:
            abort(400, "threshold must be > 1 (static scores ~1)")
        scraper.threshold = value
        return jsonify({"threshold": value})

    @app.post("/api/classify")
    def classify_upload():
        """Score an uploaded image - handy for checking the classifier."""
        f = request.files.get("image")
        if not f:
            abort(400, "upload a file in the 'image' field")
        return jsonify(classify(f.read(), threshold=scraper.threshold).to_dict())

    return app


if __name__ == "__main__":
    app = create_app()
    if os.environ.get("AUTOSTART", "1") == "1":
        app.config["scraper"].start()
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", 5000)), threaded=True)
