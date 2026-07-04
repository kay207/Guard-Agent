from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import date, datetime, timedelta
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


_CACHE: dict[str, Any] = {"loaded_at": 0.0, "events": {}}
_CACHE_TTL_SECONDS = 6 * 60 * 60

SYMBOL_FOCUS = {
    "TSLA": "交付/毛利率/FSD 叙事",
    "TSLL": "TSLA 交付/毛利率/FSD 叙事",
    "HOOD": "交易量/加密交易活跃度/监管",
    "SOFI": "贷款增速/存款成本/监管",
    "NVDA": "AI 订单/出口管制/财报指引",
    "NVDL": "NVDA AI 订单/出口管制",
    "TSM": "月度营收/资本开支/AI 订单",
    "SNDK": "存储价格/财报指引",
    "SOXL": "半导体财报指引/出口管制",
    "PDD": "消费数据/平台监管/汇率",
    "BABA": "消费数据/平台监管/云业务",
    "JD": "消费数据/平台监管/利润率",
    "BIDU": "AI 商业化/广告需求/监管",
    "0700.HK": "游戏版号/广告需求/监管",
    "0981.HK": "AI 服务器需求/财报指引",
    "1024.HK": "自动驾驶进展/交付数据",
    "3690.HK": "本地生活竞争/消费数据",
    "9618.HK": "消费数据/平台监管/利润率",
    "9988.HK": "消费数据/平台监管/云业务",
    "COIN": "BTC 波动/交易量/监管",
    "CRCL": "稳定币监管/利率收入/BTC 波动",
    "MSTR": "BTC 波动/融资计划",
    "MARA": "BTC 波动/挖矿难度",
}

TAG_FOCUS = [
    ("semiconductor", "财报指引/出口管制/AI 订单"),
    ("fintech", "交易量/利率/监管"),
    ("crypto_linked", "BTC 波动/稳定币监管"),
    ("china_internet", "消费数据/平台监管/汇率"),
    ("ev", "交付数据/毛利率/补贴政策"),
    ("leveraged", "底层标的波动/隔夜跳空"),
    ("high_beta", "财报指引/利率预期"),
]

STATIC_EVENTS = [
    {
        "date": "2026-07-06",
        "title": "美国 ISM / S&P Global 服务业 PMI",
        "markets": ["US"],
        "tags": ["high_beta", "semiconductor", "crypto_linked", "fintech"],
        "severity": "medium",
        "reason": "影响风险偏好、利率预期和成长股估值。",
        "source": "weekly macro calendar",
    },
    {
        "date": "2026-07-07",
        "title": "美国贸易帐",
        "markets": ["US"],
        "tags": ["semiconductor", "china_internet"],
        "severity": "low",
        "reason": "影响美元、利率和跨境贸易链条预期。",
        "source": "weekly macro calendar",
    },
    {
        "date": "2026-07-08",
        "title": "FOMC 会议纪要",
        "markets": ["US", "HK", "CN"],
        "tags": ["high_beta", "leveraged", "semiconductor", "crypto_linked", "fintech", "china_internet"],
        "severity": "high",
        "reason": "会影响美债利率、美元和全球成长股风险偏好。",
        "source": "weekly macro calendar",
    },
    {
        "date": "2026-07-09",
        "title": "美国初请失业金",
        "markets": ["US"],
        "tags": ["high_beta", "semiconductor", "crypto_linked"],
        "severity": "medium",
        "reason": "影响降息预期和高 beta 资产估值。",
        "source": "weekly macro calendar",
    },
    {
        "date": "2026-07-09",
        "title": "中国 CPI / PPI",
        "markets": ["HK", "CN"],
        "tags": ["china_internet"],
        "severity": "medium",
        "reason": "影响中国消费、政策预期和港股互联网风险偏好。",
        "source": "weekly macro calendar",
    },
]


def _today() -> date:
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return datetime.utcnow().date()


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(float(value)).date()
    if isinstance(value, str):
        text = value[:10]
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


def _market_for(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "").upper()
    if row.get("market"):
        return str(row["market"]).upper()
    if symbol.endswith(".HK"):
        return "HK"
    if symbol.endswith((".SS", ".SZ")):
        return "CN"
    return "US"


def _fetch_json(url: str, timeout: float = 2.8) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PortfolioGuardHackathon/0.1",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _yahoo_symbol(symbol: str) -> str:
    return urllib.parse.quote(symbol.upper().replace(".US", ""), safe="")


def _fetch_symbol_events(symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    cache_key = f"{symbol}:{start.isoformat()}:{end.isoformat()}"
    now = time.time()
    if now - float(_CACHE["loaded_at"]) < _CACHE_TTL_SECONDS and cache_key in _CACHE["events"]:
        return list(_CACHE["events"][cache_key])

    url = (
        "https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
        f"{_yahoo_symbol(symbol)}?modules=calendarEvents"
    )
    events: list[dict[str, Any]] = []
    try:
        payload = _fetch_json(url)
    except Exception:
        payload = {}

    result = (((payload.get("quoteSummary") or {}).get("result") or [{}])[0]) or {}
    earnings = ((result.get("calendarEvents") or {}).get("earnings") or {})
    for item in earnings.get("earningsDate") or []:
        raw = item.get("raw") if isinstance(item, dict) else None
        event_date = _parse_date(raw)
        if event_date and start <= event_date <= end:
            events.append(
                {
                    "date": event_date.isoformat(),
                    "title": f"{symbol} 财报窗口",
                    "symbol": symbol,
                    "severity": "high",
                    "reason": "财报会直接改变盈利预期、估值和隐含波动。",
                    "source": "Yahoo calendarEvents",
                }
            )
    _CACHE["events"][cache_key] = list(events)
    _CACHE["loaded_at"] = now
    return events


def _is_relevant(event: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    markets = {_market_for(row) for row in rows}
    tags = set().union(*(set(row.get("tags", [])) for row in rows)) if rows else set()
    event_markets = set(event.get("markets") or [])
    event_tags = set(event.get("tags") or [])
    return bool(markets & event_markets or tags & event_tags)


def upcoming_events(rows: list[dict[str, Any]], days: int = 7) -> list[dict[str, Any]]:
    start = _today()
    end = start + timedelta(days=days)
    events = [
        event for event in STATIC_EVENTS
        if start <= date.fromisoformat(event["date"]) <= end and _is_relevant(event, rows)
    ]

    top_symbols = [str(row.get("symbol") or "") for row in rows[:6] if row.get("symbol")]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_fetch_symbol_events, symbol, start, end) for symbol in top_symbols]
        try:
            completed = as_completed(futures, timeout=8)
            for future in completed:
                try:
                    events.extend(future.result(timeout=0))
                except Exception:
                    continue
        except FuturesTimeoutError:
            pass

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        unique[(event["date"], event["title"])] = event
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        unique.values(),
        key=lambda item: (item["date"], severity_rank.get(item.get("severity", "medium"), 1), item["title"]),
    )


def core_stock_watch_items(rows: list[dict[str, Any]], limit: int = 3) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or symbol in seen:
            continue
        weight = float(row.get("weight_pct") or 0)
        if weight < 5 and len(items) >= 1:
            continue
        focus = SYMBOL_FOCUS.get(symbol)
        tags = set(row.get("tags", []))
        if not focus:
            for tag, tag_focus in TAG_FOCUS:
                if tag in tags:
                    focus = tag_focus
                    break
        if focus:
            items.append(f"{symbol}：{focus}")
            seen.add(symbol)
        if len(items) >= limit:
            break
    return items
