from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

from .market_data import fetch_fx_rate_to_usd, refresh_snapshot_with_public_data, search_yahoo_symbol


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
CHINA_INTERNET_SYMBOLS = {
    "PDD",
    "BABA",
    "JD",
    "BIDU",
    "NTES",
    "KWEB",
    "0700.HK",
    "0981.HK",
    "1024.HK",
    "3690.HK",
    "9618.HK",
    "9988.HK",
}
CURRENCY_ALIASES = {
    "USD": "USD",
    "US$": "USD",
    "美元": "USD",
    "美金": "USD",
    "HKD": "HKD",
    "HK$": "HKD",
    "港币": "HKD",
    "港元": "HKD",
    "CNY": "CNY",
    "RMB": "CNY",
    "人民币": "CNY",
    "CNH": "CNH",
}


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
        value = value.upper()
        for token in ("HK$", "US$", "HKD", "USD", "CNY", "CNH", "RMB", "港币", "港元", "美元", "美金", "人民币"):
            value = value.replace(token, "")
        value = value.replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip()
    symbol = symbol.replace(".US", "").replace("US:", "")
    return "".join(ch for ch in symbol if ch.isalnum() or ch in {".", "-"})


def _text_contains(value: Any, needles: tuple[str, ...]) -> bool:
    text = str(value or "").upper()
    return any(needle.upper() in text for needle in needles)


def _currency_from_text(value: Any) -> str | None:
    text = str(value or "").upper().strip()
    if not text:
        return None
    for alias, code in CURRENCY_ALIASES.items():
        if alias.upper() in text:
            return code
    return None


def _infer_market_currency(raw: dict[str, Any], symbol: str) -> tuple[str, str]:
    explicit_currency = (
        _currency_from_text(raw.get("currency"))
        or _currency_from_text(raw.get("币种"))
        or _currency_from_text(raw.get("market_value_currency"))
        or _currency_from_text(raw.get("市值币种"))
    )
    market_text = " ".join(
        str(raw.get(key) or "")
        for key in ("market", "exchange", "交易市场", "市场", "交易所")
    )
    symbol_upper = symbol.upper()
    if explicit_currency:
        currency = explicit_currency
    elif symbol_upper.endswith(".HK") or symbol_upper.isdigit() or _text_contains(market_text, ("HK", "香港", "港股", "SEHK", "HKEX")):
        currency = "HKD"
    elif symbol_upper.endswith((".SS", ".SZ")) or _text_contains(market_text, ("上海", "深圳", "沪", "深", "A股", "CN")):
        currency = "CNY"
    else:
        currency = "USD"

    if symbol_upper.endswith(".HK") or currency == "HKD" or _text_contains(market_text, ("HK", "香港", "港股", "SEHK", "HKEX")):
        market = "HK"
    elif symbol_upper.endswith((".SS", ".SZ")) or currency in {"CNY", "CNH"}:
        market = "CN"
    else:
        market = "US"
    return market, currency


def _normalize_symbol_for_market(symbol: str, market: str) -> str:
    if market == "HK" and symbol and not symbol.endswith(".HK"):
        digits = "".join(ch for ch in symbol if ch.isdigit())
        if digits:
            return f"{str(int(digits)).zfill(4)}.HK"
    return symbol


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
    if symbol in CHINA_INTERNET_SYMBOLS:
        tags.update({"china_internet", "high_beta"})
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


def _fx_rates_for(positions: list[dict[str, Any]]) -> dict[str, float]:
    currencies = sorted(
        {
            str(pos.get("currency") or "USD").upper()
            for pos in positions
        }
    )
    return {currency: fetch_fx_rate_to_usd(currency) for currency in currencies}


def normalize_positions(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    positions = extracted.get("positions") or extracted.get("holdings") or []
    rows: list[dict[str, Any]] = []
    if not isinstance(positions, list):
        return rows

    first_pass: list[dict[str, Any]] = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        symbol, name = _resolve_symbol(item)
        if not symbol:
            continue
        market, currency = _infer_market_currency(item, symbol)
        symbol = _normalize_symbol_for_market(symbol, market)
        quantity = _num(item.get("quantity") or item.get("shares") or item.get("股数"))
        native_price = _num(
            item.get("price")
            or item.get("current_price")
            or item.get("last_price")
            or item.get("latest_price")
            or item.get("现价")
            or item.get("当前价")
            or item.get("最新价")
        )
        native_market_value = _num(
            item.get("market_value")
            or item.get("value")
            or item.get("position_value")
            or item.get("市值")
        )
        if native_market_value is None and quantity is not None and native_price is not None:
            native_market_value = quantity * native_price
        if native_price is None and native_market_value is not None and quantity not in (None, 0):
            native_price = native_market_value / quantity
        if quantity is None and native_market_value is not None and native_price not in (None, 0):
            quantity = native_market_value / native_price
        if quantity is None and native_market_value is not None:
            quantity = 0
            native_price = native_market_value
        if native_price is None:
            native_price = 0

        row = {
            "symbol": symbol,
            "name": name or symbol,
            "quantity": round(quantity or 0, 4),
            "price": round(native_price, 4),
            "native_price": round(native_price, 4),
            "currency": currency,
            "market": market,
            "tags": _tags_for_symbol(symbol),
            "source": "screenshot_vision",
        }
        if native_market_value is not None:
            row["native_market_value"] = round(native_market_value, 2)
        ret5 = _num(item.get("return_5d") or item.get("5d_return") or item.get("近5日"))
        ret20 = _num(item.get("return_20d") or item.get("20d_return") or item.get("近20日"))
        if ret5 is not None:
            row["return_5d"] = round(ret5, 2)
        if ret20 is not None:
            row["return_20d"] = round(ret20, 2)
        first_pass.append(row)

    fx_rates = _fx_rates_for(first_pass)
    for row in first_pass:
        currency = str(row.get("currency") or "USD").upper()
        fx_to_usd = fx_rates.get(currency, 1.0)
        row["fx_rate_to_usd"] = round(fx_to_usd, 6)
        row["base_currency"] = "USD"
        native_market_value = row.get("native_market_value")
        if native_market_value is not None:
            row["market_value"] = round(float(native_market_value) * fx_to_usd, 2)
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
    currencies = sorted({str(pos.get("currency") or "USD") for pos in positions})
    non_usd = [currency for currency in currencies if currency != "USD"]
    account["fx_status"] = "偏高" if non_usd else "正常"
    account["fx_evidence"] = (
        f"截图持仓包含 {', '.join(currencies)}；已按公开汇率统一换算为 USD 基准用于风险扫描。"
        if currencies
        else "截图未识别到多币种持仓。"
    )
    snapshot["account"] = account

    snapshot = refresh_snapshot_with_public_data(snapshot)
    snapshot["data_mode"] = {
        **(snapshot.get("data_mode") or {}),
        "portfolio": "screenshot portfolio (USD base)",
    }
    return snapshot
