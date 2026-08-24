from __future__ import annotations

import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from connectors import ConnectorProtocolError, ConnectorTimeout, ConnectorUnavailable

PROTOCOL_PREFIX = "/v1"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 256 * 1024

_OPERATION_ROUTES = {
    "info": ("GET", f"{PROTOCOL_PREFIX}/info"),
    "status": ("GET", f"{PROTOCOL_PREFIX}/status"),
    "capabilities": ("GET", f"{PROTOCOL_PREFIX}/capabilities"),
    "search": ("POST", f"{PROTOCOL_PREFIX}/search"),
}


class HTTPConnectorTransport:
    """Connector Protocol v1 client transport over HTTP/JSON."""

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must not be empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = float(timeout)

    def request(self, operation: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        try:
            method, path = _OPERATION_ROUTES[operation]
        except KeyError as exc:
            raise ConnectorProtocolError(f"Unsupported protocol operation: {operation}") from exc

        body = None
        headers = {"Accept": "application/json", "X-Life-Atlas-Connector-Protocol": "1"}
        if method == "POST":
            encoded = json.dumps(dict(params or {}), separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_REQUEST_BYTES:
                raise ConnectorProtocolError("Connector request exceeds protocol size limit")
            body = encoded
            headers["Content-Type"] = "application/json"
        elif params:
            raise ConnectorProtocolError(f"Operation {operation} does not accept request parameters")

        req = Request(urljoin(self.base_url, path.lstrip("/")), data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ConnectorProtocolError("Connector response exceeds protocol size limit")
                content_type = response.headers.get_content_type()
                if content_type != "application/json":
                    raise ConnectorProtocolError(f"Connector returned unsupported content type: {content_type}")
                return _decode_object(raw)
        except HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ConnectorProtocolError("Connector error response exceeds protocol size limit") from exc
            detail = _error_detail(raw)
            if exc.code == 504:
                raise ConnectorTimeout(detail or "Connector HTTP 504") from exc
            if exc.code in (502, 503):
                raise ConnectorUnavailable(detail or f"Connector HTTP {exc.code}") from exc
            raise ConnectorProtocolError(detail or f"Connector HTTP {exc.code}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ConnectorTimeout("Connector HTTP request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ConnectorTimeout("Connector HTTP request timed out") from exc
            raise ConnectorUnavailable(f"Connector HTTP request failed: {exc.reason}") from exc
        except OSError as exc:
            raise ConnectorUnavailable(f"Connector HTTP request failed: {exc}") from exc


def make_protocol_handler(service):
    """Create an HTTP handler for a source-neutral connector service."""

    class ProtocolHandler(BaseHTTPRequestHandler):
        server_version = "LifeAtlasConnector/1"

        def do_GET(self):
            operation = {
                f"{PROTOCOL_PREFIX}/info": "info",
                f"{PROTOCOL_PREFIX}/status": "status",
                f"{PROTOCOL_PREFIX}/capabilities": "capabilities",
            }.get(self.path)
            if operation is None:
                self._json_error(404, "not_found", "Unknown Connector Protocol endpoint")
                return
            self._dispatch(operation, None)

        def do_POST(self):
            if self.path != f"{PROTOCOL_PREFIX}/search":
                self._json_error(404, "not_found", "Unknown Connector Protocol endpoint")
                return
            length = self.headers.get("Content-Length")
            try:
                size = int(length or "0")
            except ValueError:
                self._json_error(400, "bad_request", "Invalid Content-Length")
                return
            if size < 0 or size > MAX_REQUEST_BYTES:
                self._json_error(413, "request_too_large", "Request exceeds Connector Protocol size limit")
                return
            if self.headers.get_content_type() != "application/json":
                self._json_error(415, "unsupported_media_type", "Content-Type must be application/json")
                return
            try:
                raw = self.rfile.read(size)
                params = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json_error(400, "invalid_json", "Request body must be valid UTF-8 JSON")
                return
            if not isinstance(params, dict):
                self._json_error(400, "invalid_json", "Request body must be a JSON object")
                return
            self._dispatch("search", params)

        def _dispatch(self, operation: str, params: Mapping[str, Any] | None):
            try:
                payload = service.handle(operation, params)
                if not isinstance(payload, Mapping):
                    raise ConnectorProtocolError("Connector operation did not return an object")
                self._json_response(200, payload)
            except ConnectionError as exc:
                self._json_error(503, "unavailable", str(exc))
            except TimeoutError as exc:
                self._json_error(504, "timeout", str(exc))
            except ConnectorProtocolError as exc:
                self._json_error(500, "protocol_error", str(exc))
            except ValueError as exc:
                self._json_error(400, "bad_request", str(exc))
            except Exception:
                self._json_error(500, "internal_error", "Connector operation failed")

        def _json_response(self, status: int, payload: Mapping[str, Any]):
            raw = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            if len(raw) > MAX_RESPONSE_BYTES:
                self._json_error(500, "response_too_large", "Response exceeds Connector Protocol size limit")
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Life-Atlas-Connector-Protocol", "1")
            self.end_headers()
            self.wfile.write(raw)

        def _json_error(self, status: int, code: str, message: str):
            self._json_response(status, {"error": {"code": code, "message": message}})

        def log_message(self, format, *args):
            return

    return ProtocolHandler


class ProtocolTestServer:
    """Small localhost-only server helper for deterministic integration tests."""

    def __init__(self, service) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_protocol_handler(service))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _decode_object(raw: bytes) -> Mapping[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectorProtocolError("Connector returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ConnectorProtocolError("Connector response must be a JSON object")
    return payload


def _error_detail(raw: bytes) -> str | None:
    try:
        payload = _decode_object(raw)
    except ConnectorProtocolError:
        return None
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    message = error.get("message")
    return message if isinstance(message, str) and message else None
