"""Tiny HTTP health-check server.

Runs in a daemon thread so it doesn't interfere with the async bot loop.
Responds 200 OK on GET /health — suitable for UptimeRobot "HTTP" monitors.

Usage:
    from services.health import start_health_server
    start_health_server(port=8080)
"""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)

_server: HTTPServer | None = None


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: object) -> None:
        pass  # suppress per-request access log noise


def start_health_server(port: int = 8080) -> None:
    """Start the health-check HTTP server in a background daemon thread."""
    global _server
    if _server is not None:
        return  # already running
    _server = HTTPServer(("", port), _Handler)
    t = threading.Thread(target=_server.serve_forever, daemon=True, name="health-http")
    t.start()
    log.info("Health endpoint listening on http://0.0.0.0:%d/health", port)


def stop_health_server() -> None:
    global _server
    if _server is not None:
        _server.shutdown()
        _server = None
