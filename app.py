from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from portfolio_guard.data_loader import load_demo_snapshot
from portfolio_guard.risk_scan import build_scan
from portfolio_guard.trade_planner import plan_trade
from portfolio_guard.volc_agent import diagnose_llm


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


class Handler(BaseHTTPRequestHandler):
    server_version = "PortfolioGuard/0.1"

    def _headers_for_file(self, path: Path) -> tuple[bytes | None, str]:
        if not path.exists() or not path.is_file():
            return None, "text/plain"
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return path.read_bytes(), content_type

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        body, content_type = self._headers_for_file(path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve_static_path(self) -> Path:
        route = unquote(self.path.split("?", 1)[0])
        if route in {"/", ""}:
            return STATIC / "index.html"
        return STATIC / route.lstrip("/")

    def do_HEAD(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return
        body, content_type = self._headers_for_file(self._resolve_static_path())
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/scan":
            snapshot = load_demo_snapshot()
            self._send_json(build_scan(snapshot))
            return
        if self.path == "/api/snapshot":
            self._send_json(load_demo_snapshot())
            return
        if self.path == "/api/health":
            snapshot = load_demo_snapshot()
            self._send_json(
                {
                    "ok": True,
                    "as_of": snapshot.get("as_of"),
                    "data_mode": snapshot.get("data_mode", {}),
                    "llm": diagnose_llm(snapshot),
                }
            )
            return
        self._send_file(self._resolve_static_path())

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/plan":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": "invalid json"}, status=400)
            return
        query = str(payload.get("query") or "我想买特斯拉")
        self._send_json(plan_trade(query, load_demo_snapshot()))

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Portfolio Guard Agent demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Portfolio Guard Agent running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
