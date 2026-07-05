from __future__ import annotations

import json
import math
import statistics
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any


YAHOO_SYMBOLS = {
    "VIX": "^VIX",
    "SPX": "^GSPC",
    "HSI": "^HSI",
    "HSTECH": "3033.HK",
    "CSI300": "000300.SS",
    "SSE": "000001.SS",
}
FX_FALLBACK_TO_USD = {
    "USD": 1.0,
    "HKD": 0.128,
    "CNY": 0.138,
    "CNH": 0.138,
}


def _num(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_symbol(symbol: str) -> str:
    return symbol.upper().replace(".US", "")


def _position_market(position: dict[str, Any]) -> str:
    symbol = str(position.get("symbol") or "").upper()
    currency = str(position.get("currency") or "").upper()
    if position.get("market"):
        return str(position["market"]).upper()
    if symbol.endswith(".HK") or currency == "HKD":
        return "HK"
    if symbol.endswith((".SS", ".SZ")) or currency in {"CNY", "CNH"}:
        return "CN"
    return "US"


def _yahoo_symbol(symbol: str) -> str:
    clean = _clean_symbol(symbol)
    return YAHOO_SYMBOLS.get(clean, clean)


def _fetch_json(url: str, timeout: float = 2.2) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PortfolioGuardHackathon/0.1 contact@example.com",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def search_yahoo_symbol(query: str) -> dict[str, Any] | None:
    text = query.strip()
    if not text:
        return None
    url = (
        "https://query1.finance.yahoo.com/v1/finance/search?"
        + urllib.parse.urlencode({"q": text, "quotesCount": 8, "newsCount": 0})
    )
    try:
        payload = _fetch_json(url, timeout=2.5)
    except Exception:
        return None
    quotes = payload.get("quotes") or []
    for item in quotes:
        symbol = str(item.get("symbol") or "").upper()
        quote_type = str(item.get("quoteType") or "").upper()
        if not symbol or quote_type not in {"EQUITY", "ETF", "INDEX"}:
            continue
        if "." in symbol and not symbol.endswith((".HK", ".SS", ".SZ")):
            continue
        if "-" in symbol:
            continue
        return {
            "symbol": symbol,
            "name": item.get("shortname") or item.get("longname") or symbol,
            "exchange": item.get("exchDisp") or item.get("exchange"),
            "quote_type": quote_type,
            "source": "Yahoo Finance search",
        }
    return None


def fetch_fx_rate_to_usd(currency: str) -> float:
    code = currency.upper().strip()
    if code == "USD":
        return 1.0
    direct_symbol = f"{code}USD=X"
    chart = fetch_yahoo_chart(direct_symbol, range_="5d")
    if chart and chart.get("last"):
        return float(chart["last"])
    inverse_symbol = f"USD{code}=X"
    inverse = fetch_yahoo_chart(inverse_symbol, range_="5d")
    if inverse and inverse.get("last"):
        return 1.0 / float(inverse["last"])
    return FX_FALLBACK_TO_USD.get(code, 1.0)


def _pct(current: float | None, base: float | None) -> float | None:
    if current is None or base in (None, 0):
        return None
    return (current / float(base) - 1.0) * 100


def _sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return statistics.mean(values[-n:])


def _atr(bars: list[dict[str, Any]], n: int = 14) -> float | None:
    clean = [bar for bar in bars if all(_num(bar.get(key)) is not None for key in ("high", "low", "close"))]
    if len(clean) < n + 1:
        return None
    ranges: list[float] = []
    previous_close = _num(clean[-n - 1].get("close"))
    for bar in clean[-n:]:
        high = _num(bar.get("high"))
        low = _num(bar.get("low"))
        close = _num(bar.get("close"))
        if high is None or low is None:
            continue
        if previous_close is None:
            ranges.append(high - low)
        else:
            ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = close
    return statistics.mean(ranges) if ranges else None


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "NA"
    if abs(value) >= 1000:
        return f"{value:.0f}"
    if abs(value) >= 100:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _market_tone(primary: dict[str, Any], secondary: dict[str, Any] | None = None) -> str:
    ret5 = _num(primary.get("return_5d"))
    ret20 = _num(primary.get("return_20d"))
    secondary_ret5 = _num((secondary or {}).get("return_5d"))
    if ret5 is not None and ret5 >= 1.5 and (ret20 or 0) >= 0:
        tone = "偏强"
    elif ret5 is not None and ret5 <= -1.5:
        tone = "偏弱"
    elif ret20 is not None and ret20 < -2:
        tone = "修复不足"
    else:
        tone = "震荡"
    if secondary_ret5 is not None and ret5 is not None and ret5 > secondary_ret5 + 0.8:
        return f"{tone}，成长风格更占优"
    if secondary_ret5 is not None and ret5 is not None and ret5 < secondary_ret5 - 0.8:
        return f"{tone}，防守/宽基相对占优"
    return tone


def _risk_appetite(qqq: dict[str, Any], spy: dict[str, Any], vix: dict[str, Any]) -> str:
    qqq_ret = _num(qqq.get("return_5d"))
    spy_ret = _num(spy.get("return_5d"))
    vix_value = _num(vix.get("last"))
    if vix_value is not None and vix_value >= 22:
        return "风险偏好偏弱，新增风险和杠杆应降级"
    if qqq_ret is not None and spy_ret is not None and qqq_ret > spy_ret and (vix_value or 99) < 18:
        return "风险偏好尚可，但高 beta 仓位不宜继续堆叠"
    if qqq_ret is not None and qqq_ret < -1:
        return "风险偏好转弱，先看支撑而不是主动加风险"
    return "风险偏好中性，适合等确认后分批交易"


def _market_views(charts: dict[str, dict[str, Any]], markets: set[str]) -> list[dict[str, str]]:
    views: list[dict[str, str]] = []
    if "US" in markets:
        views.append(
            {
                "market": "US",
                "label": "美股",
                "summary": f"{_market_tone(charts.get('QQQ') or {}, charts.get('SPY'))}；{_risk_appetite(charts.get('QQQ') or {}, charts.get('SPY') or {}, charts.get('VIX') or {})}。",
            }
        )
    if "HK" in markets:
        hk_chart = charts.get("HSI") or charts.get("2800.HK") or {}
        hstech_chart = charts.get("HSTECH") or charts.get("3033.HK") or {}
        views.append(
            {
                "market": "HK",
                "label": "港股",
                "summary": f"{_market_tone(hstech_chart or hk_chart, hk_chart)}；若科技指数弱于恒指，港股互联网仓位先控制追涨。",
            }
        )
    if "CN" in markets:
        cn_chart = charts.get("CSI300") or charts.get("000300.SS") or charts.get("SSE") or {}
        views.append(
            {
                "market": "CN",
                "label": "A 股",
                "summary": f"{_market_tone(cn_chart)}；风险偏好主要看宽基能否延续修复，弱势时不宜放大高 beta。",
            }
        )
    return views


def _zone(center: float | None, price: float | None, kind: str = "stock") -> str:
    if center is None:
        return "NA"
    width = max(abs(center) * (0.004 if kind == "index" else 0.008), abs(price or center) * 0.002)
    return f"{_fmt_price(center - width)}-{_fmt_price(center + width)}"


def fetch_yahoo_chart(symbol: str, range_: str = "6mo") -> dict[str, Any] | None:
    yahoo = urllib.parse.quote(_yahoo_symbol(symbol), safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo}?range={range_}&interval=1d"
    try:
        payload = _fetch_json(url)
    except Exception:
        return None
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return None
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0]) or {}
    bars: list[dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        close = _num((quote.get("close") or [None])[idx])
        if close is None:
            continue
        bars.append(
            {
                "time": ts,
                "open": _num((quote.get("open") or [None])[idx]),
                "high": _num((quote.get("high") or [None])[idx]),
                "low": _num((quote.get("low") or [None])[idx]),
                "close": close,
                "volume": _num((quote.get("volume") or [None])[idx]),
            }
        )
    if not bars:
        return None
    closes = [bar["close"] for bar in bars if _num(bar.get("close")) is not None]
    last = _num(meta.get("regularMarketPrice")) or closes[-1]
    previous_close = closes[-2] if len(closes) >= 2 else _num(meta.get("regularMarketPreviousClose"))
    return {
        "symbol": _clean_symbol(symbol),
        "last": last,
        "previous_close": previous_close,
        "return_1d": _pct(last, previous_close),
        "return_5d": _pct(last, closes[-6] if len(closes) >= 6 else None),
        "return_20d": _pct(last, closes[-21] if len(closes) >= 21 else None),
        "bars": bars,
        "source": "Yahoo Finance public chart",
    }


def build_market_structure(symbol: str, chart: dict[str, Any]) -> dict[str, Any] | None:
    bars = chart.get("bars") or []
    if len(bars) < 25:
        return None
    clean = [bar for bar in bars if _num(bar.get("close")) is not None]
    price = _num(chart.get("last")) or _num(clean[-1].get("close"))
    closes = [_num(bar.get("close")) for bar in clean]
    closes = [value for value in closes if value is not None]
    highs = [_num(bar.get("high")) for bar in clean[-21:]]
    lows = [_num(bar.get("low")) for bar in clean[-21:]]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    sma10 = _sma(closes, 10)
    sma21 = _sma(closes, 21)
    sma50 = _sma(closes, 50)
    atr = _atr(clean, 14)
    five_high = max([_num(bar.get("high")) for bar in clean[-5:] if _num(bar.get("high")) is not None], default=None)
    five_low = min([_num(bar.get("low")) for bar in clean[-5:] if _num(bar.get("low")) is not None], default=None)
    support_candidates = [value for value in (five_low, sma10, sma21, sma50, min(lows) if lows else None) if value and price and value <= price]
    resistance_candidates = [value for value in (five_high, sma10, sma21, sma50, max(highs) if highs else None) if value and price and value >= price]
    support = max(support_candidates) if support_candidates else five_low or sma21
    major_support = min(support_candidates) if support_candidates else sma50 or support
    resistance = min(resistance_candidates) if resistance_candidates else ((price or 0) + max(atr or 0, (price or 0) * 0.04))
    next_resistance = max(resistance_candidates) if len(resistance_candidates) >= 2 else ((resistance or price or 0) + max(atr or 0, (price or 0) * 0.05))
    ret5 = chart.get("return_5d")
    ret20 = chart.get("return_20d")
    if price and sma21 and price > sma21 and (ret5 or 0) > 3:
        state = f"{symbol} 短线偏强，价格高于 21 日均线，但追高的风险回报需要结合账户暴露控制。"
    elif price and support and price <= support * 1.03:
        state = f"{symbol} 正在测试短线支撑，能否守住支撑决定是否恢复加仓资格。"
    else:
        state = f"{symbol} 处在震荡观察区，先等待突破或跌破关键区间。"
    return {
        "state": state,
        "support_near": _zone(support, price),
        "support_major": _zone(major_support, price),
        "resistance": _zone(resistance, price),
        "next_resistance": _zone(next_resistance, price),
        "price": price,
        "return_5d": ret5,
        "return_20d": ret20,
        "source": chart.get("source"),
    }


def refresh_snapshot_with_public_data(snapshot: dict[str, Any]) -> dict[str, Any]:
    positions = snapshot.get("positions", [])
    symbols = sorted({_clean_symbol(str(pos.get("symbol", ""))) for pos in positions if pos.get("symbol")})
    markets = {_position_market(pos) for pos in positions} or {"US"}
    symbols.extend(["QQQ", "SPY", "VIX"])
    if "HK" in markets:
        symbols.extend(["HSI", "HSTECH", "2800.HK"])
    if "CN" in markets:
        symbols.extend(["CSI300", "SSE", "510300.SS"])
    charts: dict[str, dict[str, Any]] = {}
    executor = ThreadPoolExecutor(max_workers=8)
    try:
        futures = {executor.submit(fetch_yahoo_chart, symbol): symbol for symbol in symbols}
        try:
            completed = as_completed(futures, timeout=6.0)
            for future in completed:
                symbol = futures[future]
                try:
                    chart = future.result(timeout=0)
                except Exception:
                    chart = None
                if chart:
                    charts[_clean_symbol(symbol)] = chart
        except FuturesTimeoutError:
            pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    for pos in positions:
        symbol = _clean_symbol(str(pos.get("symbol", "")))
        chart = charts.get(symbol)
        if not chart:
            continue
        if chart.get("last") is not None:
            pos["price"] = round(float(chart["last"]), 4)
        if chart.get("return_5d") is not None:
            pos["return_5d"] = round(float(chart["return_5d"]), 2)
        if chart.get("return_20d") is not None:
            pos["return_20d"] = round(float(chart["return_20d"]), 2)

    structures = dict(snapshot.get("market_structure") or {})
    for symbol, chart in charts.items():
        structure = build_market_structure(symbol, chart)
        if structure:
            structures[symbol] = structure
    snapshot["market_structure"] = structures

    qqq = charts.get("QQQ") or {}
    spy = charts.get("SPY") or {}
    vix = charts.get("VIX") or {}
    market = dict(snapshot.get("market") or {})
    if qqq or spy or vix:
        market["summary"] = f"美股{_market_tone(qqq, spy)}，{_risk_appetite(qqq, spy, vix)}。"
        market["risk_note"] = "组合交易以大盘强弱和风险偏好定仓位，顺风时分批加，逆风时先保护。"
        market["qqq_change"] = f"{float(qqq.get('return_1d')):+.2f}%" if qqq.get("return_1d") is not None else market.get("qqq_change", "NA")
        market["spy_change"] = f"{float(spy.get('return_1d')):+.2f}%" if spy.get("return_1d") is not None else market.get("spy_change", "NA")
        market["vix"] = f"{float(vix.get('last')):.2f}" if vix.get("last") is not None else market.get("vix", "NA")
    market["views"] = _market_views(charts, markets)
    snapshot["market"] = market
    snapshot["data_mode"] = {
        "portfolio": "demo/static portfolio input",
        "market": "public market data" if charts else "embedded fallback snapshot",
        "market_symbols_loaded": sorted(charts),
    }
    return snapshot
