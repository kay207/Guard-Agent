from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

from .market_data import refresh_snapshot_with_public_data, search_yahoo_symbol


HIGH_BETA_SYMBOLS = {
    "AFRM",
    "COIN",
    "CRCL",
    "HOOD",
    "MARA",
    "MSTR",
    "NIO",
    "NVDA",
    "PLTR",
    "RBLX",
    "SHOP",
    "SNDK",
    "SOFI",
    "TSLA",
}
INDEX_SYMBOLS = {"DIA", "IVV", "QQQ", "QQQI", "SPY", "VOO", "VTI"}
LEVERAGED_SYMBOLS = {
    "NVDL",
    "SOXL",
    "SPXL",
    "TQQQ",
    "TSLL",
    "UPRO",
}
CRYPTO_LINKED_SYMBOLS = {"COIN", "CRCL", "MARA", "MSTR", "RIOT"}
MEGA_CAP_SYMBOLS = {"AAPL", "AMZN", "GOOG", "GOOGL", "META", "MSFT"}


def _now_asia_shanghai() -> str:
    if ZoneInfo is not None:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    else:
        now = datetime.utcnow()
    return now.strftime("%Y-%m-%d %H:%M Asia/Shanghai")


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip()
    symbol = symbol.replace(".US", "").replace("US:", "")
    return "".join(ch for ch in symbol if ch.isalnum() or ch in {".", "-"})


def _tags_for_symbol(symbol: str) -> list[str]:
    tags: set[str] = set()
    if symbol in HIGH_BETA_SYMBOLS:
        tags.add("high_beta")
    if symbol in INDEX_SYMBOLS:
        tags.add("index_beta")
    if symbol in LEVERAGED_SYMBOLS:
        tags.update({"leveraged", "high_beta"})
    if symbol in CRYPTO_LINKED_SYMBOLS:
        tags.update({"crypto_linked", "speculative_growth"})
    if symbol in MEGA_CAP_SYMBOLS:
        tags.add("mega_cap_quality")
    if symbol in {"NVDA", "NVDL", "SOXL", "SNDK", "TSM"}:
        tags.add("semiconductor")
    return sorted(tags)


def _resolve_symbol(raw: dict[str, Any]) -> tuple[str | None, str]:
    symbol = _clean_symbol(raw.get("symbol") or raw.get("ticker") or raw.get("代码"))
    name = str(raw.get("name") or raw.get("company") or raw.get("名称") or symbol).strip()
    if symbol:
        return symbol, name or symbol
    if name:
        match = search_yahoo_symbol(name)
        if match and match.get("symbol"):
            return _clean_symbol(match["symbol"]), str(match.get("name") or name)
    return None, name


def normalize_positions(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    positions = extracted.get("positions") or extracted.get("holdings") or []
    rows: list[dict[str, Any]] = []
    if not isinstance(positions, list):
        return rows

    for item in positions:
        if not isinstance(item, dict):
            continue
        symbol, name = _resolve_symbol(item)
        if not symbol:
            continue
        quantity = _num(item.get("quantity") or item.get("shares") or item.get("股数"))
        price = _num(
            item.get("price")
            or item.get("current_price")
            or item.get("last_price")
            or item.get("latest_price")
            or item.get("现价")
            or item.get("当前价")
            or item.get("最新价")
        )
        market_value = _num(
            item.get("market_value")
            or item.get("value")
            or item.get("position_value")
            or item.get("市值")
        )
        if market_value is None and quantity is not None and price is not None:
            market_value = quantity * price
        if price is None and market_value is not None and quantity not in (None, 0):
            price = market_value / quantity
        if quantity is None and market_value is not None and price not in (None, 0):
            quantity = market_value / price
        if quantity is None and market_value is not None:
            quantity = 0
            price = market_value
        if price is None:
            price = 0

        row = {
            "symbol": symbol,
            "name": name or symbol,
            "quantity": round(quantity or 0, 4),
            "price": round(price, 4),
            "tags": _tags_for_symbol(symbol),
            "source": "screenshot_vision",
        }
        if market_value is not None:
            row["market_value"] = round(market_value, 2)
        ret5 = _num(item.get("return_5d") or item.get("5d_return") or item.get("近5日"))
        ret20 = _num(item.get("return_20d") or item.get("20d_return") or item.get("近20日"))
        if ret5 is not None:
            row["return_5d"] = round(ret5, 2)
        if ret20 is not None:
            row["return_20d"] = round(ret20, 2)
        rows.append(row)

    return rows


def build_uploaded_snapshot(base_snapshot: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    snapshot = copy.deepcopy(base_snapshot)
    positions = normalize_positions(extracted)
    snapshot["positions"] = positions
    snapshot["as_of"] = _now_asia_shanghai()
    snapshot["risk_history"] = {"mode": "current_snapshot_only"}
    snapshot["event_risk"] = {
        "status": "正常",
        "evidence": "截图导入模式下未识别到近期最高优先级事件；交易前仍应结合财报和宏观日历复核。",
    }

    account = dict(snapshot.get("account") or {})
    cash_pct = _num(extracted.get("cash_pct") or extracted.get("cash_weight"))
    margin_buffer_pct = _num(extracted.get("margin_buffer_pct") or extracted.get("margin_buffer"))
    if cash_pct is not None:
        account["cash_pct"] = cash_pct
    if margin_buffer_pct is not None:
        account["margin_buffer_pct"] = margin_buffer_pct
    account.setdefault("cash_pct", 5.0)
    account.setdefault("margin_buffer_pct", 25.0)
    snapshot["account"] = account

    snapshot = refresh_snapshot_with_public_data(snapshot)
    snapshot["data_mode"] = {
        **(snapshot.get("data_mode") or {}),
        "portfolio": "screenshot portfolio via Ark vision",
    }
    return snapshot
