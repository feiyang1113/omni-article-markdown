import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from .omni_article_md import OmniArticleMarkdown

_THROTTLE_DEFAULT_MS = int(os.getenv("OMNIMD_THROTTLE_DEFAULT_MS", "0") or 0)
_THROTTLE_MP_WEIXIN_MS = int(os.getenv("OMNIMD_THROTTLE_MP_WEIXIN_MS", "3000") or 0)
_LAST_REQ_AT: dict[str, float] = {}
_LAST_REQ_LOCK = threading.Lock()


def _throttle(url: str) -> None:
    target = urlsplit(url)
    host = (target.netloc or "").lower()
    if not host:
        return

    min_ms = _THROTTLE_DEFAULT_MS
    if host.endswith("mp.weixin.qq.com"):
        min_ms = _THROTTLE_MP_WEIXIN_MS

    if min_ms <= 0:
        return

    min_s = min_ms / 1000.0
    with _LAST_REQ_LOCK:
        now = time.monotonic()
        last = _LAST_REQ_AT.get(host)
        if last is not None:
            wait_s = min_s - (now - last)
            if wait_s > 0:
                time.sleep(wait_s)
                now = time.monotonic()
        _LAST_REQ_AT[host] = now


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = ""
    sys_version = ""

    def send_response(self, code: int, message: str | None = None):
        self.log_request(code)
        self.send_response_only(code, message)

    def _send_bytes(self, code: int, body: bytes, content_type: str):
        self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(code, body, "application/json; charset=utf-8")

    def _send_markdown(self, code: int, markdown: str):
        body = markdown.encode("utf-8")
        self._send_bytes(code, body, "text/markdown; charset=utf-8")

    def _extract_url(self) -> str | None:
        parts = urlsplit(self.path)
        qs = parse_qs(parts.query)
        url = (qs.get("url") or [None])[0]
        if url:
            return url

        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length <= 0:
            return None

        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if isinstance(payload, dict):
            val = payload.get("url")
            return val if isinstance(val, str) and val else None
        return None

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path in ("/healthz", "/ping"):
            self._send_json(200, {"status": "ok"})
            return

        if parts.path not in ("/", "/parse"):
            self._send_json(404, {"error": "not found"})
            return

        url = self._extract_url()
        if not url:
            self._send_json(400, {"error": "missing url"})
            return
        try:
            _throttle(url)
            ctx = OmniArticleMarkdown(url).parse()
            self._send_markdown(200, ctx.markdown)
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def do_POST(self):
        parts = urlsplit(self.path)
        if parts.path not in ("/", "/parse"):
            self._send_json(404, {"error": "not found"})
            return
        self.do_GET()

    def do_OPTIONS(self):
        self.close_connection = True
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format: str, *args):
        if os.getenv("OMNIMD_QUIET") == "1":
            return
        super().log_message(format, *args)


def main():
    host = os.getenv("FC_SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("FC_SERVER_PORT", "9000"))
    httpd = HTTPServer((host, port), _Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
