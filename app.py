from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from portfolio_guard.data_loader import load_demo_snapshot
from portfolio_guard.portfolio_upload import build_uploaded_snapshot, normalize_positions
from portfolio_guard.risk_scan import build_scan
from portfolio_guard.trade_planner import plan_trade
from portfolio_guard.volc_agent import diagnose_llm, extract_portfolio_from_images


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MAX_JSON_BYTES = 24_000_000
MAX_UPLOAD_IMAGES = 6
SESSION_SNAPSHOTS: dict[str, dict] = {}


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

    def _read_json(self, max_bytes: int = 200_000) -> tuple[dict | None, str | None]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > max_bytes:
            return None, "payload too large"
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None, "invalid json"
        if not isinstance(payload, dict):
            return None, "json must be an object"
        return payload, None

    def _session_id(self) -> str | None:
        raw = str(self.headers.get("X-Guard-Session") or "").strip()
        if not raw:
            return None
        safe = "".join(ch for ch in raw[:80] if ch.isalnum() or ch in {"-", "_"})
        return safe or None

    def _snapshot(self) -> dict:
        session_id = self._session_id()
        if session_id and session_id in SESSION_SNAPSHOTS:
            return copy.deepcopy(SESSION_SNAPSHOTS[session_id])
        return load_demo_snapshot()

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
        path = self.path.split("?", 1)[0]
        if path == "/api/scan":
            snapshot = self._snapshot()
            self._send_json(build_scan(snapshot))
            return
        if path == "/api/snapshot":
            self._send_json(self._snapshot())
            return
        if path == "/api/health":
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
        path = self.path.split("?", 1)[0]
        if path == "/api/plan":
            payload, error = self._read_json()
            if error:
                self._send_json({"error": error}, status=400)
                return
            query = str((payload or {}).get("query") or "我想买特斯拉")
            self._send_json(plan_trade(query, self._snapshot()))
            return
        if path == "/api/portfolio/upload":
            self._handle_portfolio_upload()
            return
        self.send_error(404)

    def _handle_portfolio_upload(self) -> None:
        payload, error = self._read_json(max_bytes=MAX_JSON_BYTES)
        if error:
            status = 413 if error == "payload too large" else 400
            self._send_json({"error": error}, status=status)
            return
        raw_images = (payload or {}).get("images") or (payload or {}).get("image_data_list")
        if raw_images is None:
            raw_images = [(payload or {}).get("image_data")]
        if not isinstance(raw_images, list):
            self._send_json({"error": "images must be a list of data:image URLs"}, status=400)
            return
        image_data_urls = [str(item or "") for item in raw_images if item]
        if not image_data_urls:
            self._send_json({"error": "no images provided"}, status=400)
            return
        if len(image_data_urls) > MAX_UPLOAD_IMAGES:
            self._send_json({"error": f"最多一次上传 {MAX_UPLOAD_IMAGES} 张截图"}, status=400)
            return
        if not all(item.startswith("data:image/") for item in image_data_urls):
            self._send_json({"error": "all images must be data:image URLs"}, status=400)
            return
        extracted, vision_trace = extract_portfolio_from_images(image_data_urls)
        if not extracted:
            self._send_json({"error": "vision parse failed", "trace": vision_trace}, status=422)
            return
        positions = normalize_positions(extracted)
        if not positions:
            self._send_json(
                {"error": "no positions detected", "trace": vision_trace, "extracted": extracted},
                status=422,
            )
            return
        snapshot = build_uploaded_snapshot(load_demo_snapshot(refresh_market=False), extracted)
        session_id = self._session_id()
        if session_id:
            SESSION_SNAPSHOTS[session_id] = copy.deepcopy(snapshot)
        scan = build_scan(snapshot)
        self._send_json(
            {
                "ok": True,
                "image_count": len(image_data_urls),
                "positions": snapshot.get("positions", []),
                "scan": scan,
                "trace": [
                    *vision_trace,
                    {
                        "tool": "Portfolio snapshot",
                        "status": "computed",
                        "detail": f"从 {len(image_data_urls)} 张截图识别并标准化 {len(snapshot.get('positions', []))} 个持仓",
                    },
                ],
            }
        )

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
