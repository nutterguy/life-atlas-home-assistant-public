from __future__ import annotations

import http.client
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

APP_HOST = "127.0.0.1"
APP_PORT = int(os.environ.get("LIFE_ATLAS_BACKEND_PORT", "8100"))
MCP_HOST = "127.0.0.1"
MCP_PORT = int(os.environ.get("GOOGLE_PHOTOS_MCP_PORT", "3000"))
PROXY_HOST = os.environ.get("LIFE_ATLAS_HOST", "0.0.0.0")
PROXY_PORT = int(os.environ.get("LIFE_ATLAS_PORT", "8099"))
TOKEN_DB = Path(os.environ.get("LIFE_ATLAS_DATA_DIR", "/data")) / "google-photos-mcp" / "tokens.db"
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
MAX_PROXY_REQUEST_BYTES = 56 * 1024 * 1024
MAX_RESTORE_CHUNK_BYTES = 4 * 1024 * 1024


def request_local(port: int, method: str, target: str, body: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 15):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(method, target, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.reason, list(response.getheaders()), response.read()
    finally:
        connection.close()


def mcp_status():
    configured = bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "")
    health = "unavailable"
    try:
        status, _, _, body = request_local(MCP_PORT, "GET", "/health")
        if status == 200:
            health = json.loads(body.decode("utf-8")).get("status", "healthy")
    except Exception:
        pass

    authenticated = False
    if TOKEN_DB.exists():
        try:
            con = sqlite3.connect(f"file:{TOKEN_DB}?mode=ro", uri=True)
            try:
                authenticated = bool(con.execute("SELECT 1 FROM keyv WHERE key LIKE 'tokens:%' LIMIT 1").fetchone())
            finally:
                con.close()
        except sqlite3.Error:
            authenticated = False

    return {
        "available": health == "healthy",
        "health": health,
        "configured": configured,
        "authenticated": authenticated,
        "redirect_uri": redirect_uri,
        "token_storage": "/data/google-photos-mcp/tokens.db",
    }


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def relay(self, port: int, target: str):
        size = int(self.headers.get("Content-Length", "0") or 0)
        route = urlparse(target).path
        limit = MAX_RESTORE_CHUNK_BYTES if route.startswith("/api/restore/sessions/") and route.endswith("/chunks") else MAX_PROXY_REQUEST_BYTES
        if size < 0 or size > limit:
            return self.send_json({"error": "Request is too large"}, 413)
        body = self.rfile.read(size) if size else None
        forwarded = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS and k.lower() != "host"}
        try:
            timeout = 300 if route.startswith("/api/restore/") else 15
            status, reason, headers, response_body = request_local(port, self.command, target, body, forwarded, timeout=timeout)
        except Exception as exc:
            return self.send_json({"error": f"Local service unavailable: {type(exc).__name__}"}, 503)
        self.send_response(status, reason)
        for key, value in headers:
            if key.lower() not in HOP_HEADERS and key.lower() not in {"content-length", "server", "date"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response_body)

    def handle_request(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/api/google-photos-mcp/status" and self.command == "GET":
            return self.send_json(mcp_status())
        if route == "/api/google-photos-mcp/auth" and self.command == "GET":
            return self.relay(MCP_PORT, "/auth")
        if route == "/api/google-photos-mcp/auth/callback" and self.command == "GET":
            target = "/auth/callback" + (f"?{parsed.query}" if parsed.query else "")
            return self.relay(MCP_PORT, target)
        return self.relay(APP_PORT, self.path)

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_PATCH = handle_request
    do_DELETE = handle_request
    do_HEAD = handle_request


def run():
    server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)
    print(f"Life Atlas ingress proxy listening on {PROXY_HOST}:{PROXY_PORT}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
