"""Flask delivery layer. Hardware reads belong exclusively to CollectorService."""

import hmac
import ipaddress
import sqlite3
import time
from urllib.parse import urlsplit

from flask import Flask, current_app, jsonify, render_template, request

from gpuroster.monitoring.service import CollectorService
from gpuroster.settings import load_settings


def create_app(config=None, collector=None):
    application = Flask(__name__)
    application.config.update(load_settings())
    if config:
        application.config.update(config)
    application.extensions["collector"] = (
        collector
        if collector is not None
        else CollectorService(application.config, application.config["DB_PATH"])
    )
    application.before_request(protect_access)
    application.after_request(protect_responses)
    application.add_url_rule("/", view_func=index)
    application.add_url_rule("/api/stats", view_func=api_stats)
    application.add_url_rule("/api/login_stats", view_func=api_login_stats)
    application.add_url_rule("/api/sessions", view_func=api_sessions)
    application.add_url_rule("/api/connections", view_func=api_connections)
    application.add_url_rule("/api/gpu_history", view_func=api_gpu_history)
    application.register_error_handler(sqlite3.Error, storage_error)
    return application


def _is_loopback(address):
    if address == "localhost":
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def protect_access():
    """Authenticate before collection; never trust forwarded identity headers."""
    if current_app.config["AUTH_USER"]:
        auth = request.authorization
        username = auth.username if auth and auth.type == "basic" else ""
        password = auth.password if auth and auth.type == "basic" else ""
        valid_user = hmac.compare_digest(
            (username or "").encode(), current_app.config["AUTH_USER"].encode()
        )
        valid_password = hmac.compare_digest(
            (password or "").encode(), current_app.config["AUTH_PASSWORD"].encode()
        )
        if not (valid_user and valid_password):
            return (
                jsonify(error="authentication_required"),
                401,
                {"WWW-Authenticate": 'Basic realm="GPU Roster", charset="UTF-8"'},
            )
    else:
        try:
            hostname = urlsplit("http://" + request.host).hostname or ""
        except ValueError:
            hostname = ""
        if not _is_loopback(request.remote_addr or "") or not _is_loopback(hostname):
            return jsonify(error="local_access_only"), 403
    if request.path in {"/api/login_stats", "/api/sessions", "/api/connections"}:
        if not current_app.config["SHOW_SESSIONS"]:
            return jsonify(error="session_visibility_disabled"), 403


def protect_responses(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; font-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    )
    return response


def collector():
    return current_app.extensions["collector"]


def index():
    return render_template("index.html")


def api_stats():
    snapshot = collector().snapshot()
    if snapshot["sequence"] == 0:
        return jsonify(error="collector_not_ready", health=snapshot["health"]), 503
    snapshot.pop("login_stats")
    snapshot.pop("sessions")
    return jsonify(snapshot)


def session_response(key, sources):
    snapshot = collector().snapshot()
    if snapshot["sequence"] == 0 or any(
        snapshot["health"]["sources"][source]["status"] not in {"ok", "partial"}
        for source in sources
    ):
        return jsonify(error="session_details_unavailable"), 503
    response = jsonify(snapshot[key])
    response.headers["X-Snapshot-Sequence"] = str(snapshot["sequence"])
    return response


def api_login_stats():
    return session_response("login_stats", ("sessions", "connections"))


def api_sessions():
    return session_response("sessions", ("sessions", "connections"))


def api_connections():
    return session_response("connections", ("connections",))


def api_gpu_history():
    range_key = request.args.get("range", "today")
    if range_key not in {"today", "week", "month"}:
        return jsonify(error="invalid_range"), 400
    service = collector()
    health = service.snapshot()["health"]["sources"]["history"]
    result = service.store.read(range_key, time.time(), current_app.config["TIMEZONE"])
    result["health"] = health
    return jsonify(result)


def storage_error(error):
    return jsonify(error="history_unavailable"), 503
