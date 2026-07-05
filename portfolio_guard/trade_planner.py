from __future__ import annotations

import re
from typing import Any

from .risk_scan import HIGH_BETA_TAGS
from .market_data import build_market_structure, fetch_yahoo_chart, search_yahoo_symbol
from .volc_agent import parse_intent_with_llm


SYMBOL_ALIASES = {
    "NVDL": ["nvdl", "英伟达2倍", "英伟达 2倍", "英伟达杠杆", "nvidia 2x"],
    "TSLA": ["tsla", "特斯拉", "tesla"],
    "HOOD": ["hood", "robinhood", "罗宾汉"],
    "NVDA": ["nvda", "英伟达", "nvidia"],
    "SNDK": ["sndk", "闪迪", "sandisk", "san disk"],
    "PDD": ["pdd", "拼多多"],
    "MSFT": ["msft", "微软"],
    "0700.HK": ["0700.hk", "00700", "腾讯", "tencent"],
    "9988.HK": ["9988.hk", "09988", "阿里", "阿里巴巴", "alibaba"],
    "3690.HK": ["3690.hk", "03690", "美团", "meituan"],
    "510300.SS": ["510300", "沪深300etf", "沪深300 ETF", "csi300 etf"],
}
LEVERAGED_TOOL_MAP = {
    "TSLA": "TSLL",
    "NVDA": "NVDL",
    "QQQ": "TQQQ",
    "SPY": "SPXL",
    "VOO": "SPXL",
    "TSM": "SOXL",
    "SNDK": "SOXL",
}
LEVERAGED_PRODUCTS = {"NVDL", "TSLL", "TQQQ", "SOXL", "SPXL", "UPRO"}
PLACEHOLDER_LEVELS = {
    "第一支撑区",
    "关键支撑区",
    "短线支撑区",
    "突破观察区",
    "上方压力区",
    "下一压力区",
}


def _position_value(position: dict[str, Any]) -> float:
    if position.get("market_value") is not None:
        return float(position.get("market_value") or 0)
    return float(position.get("quantity") or 0) * float(position.get("price") or 0)


def _portfolio_rows(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    positions = snapshot.get("positions", [])
    total = sum(_position_value(pos) for pos in positions)
    rows = []
    for pos in positions:
        value = _position_value(pos)
        rows.append(
            {
                **pos,
                "value": value,
                "weight_pct": round(value / total * 100, 2) if total else 0.0,
            }
        )
    return rows, total


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _position_market(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "").upper()
    currency = str(row.get("currency") or "").upper()
    if row.get("market"):
        return str(row["market"]).upper()
    if symbol.endswith(".HK") or currency == "HKD":
        return "HK"
    if symbol.endswith((".SS", ".SZ")) or currency in {"CNY", "CNH"}:
        return "CN"
    return "US"


def _portfolio_markets(rows: list[dict[str, Any]]) -> set[str]:
    return {_position_market(row) for row in rows} or {"US"}


def _leveraged_tool_for(symbol: str) -> tuple[str | None, str]:
    symbol = symbol.upper()
    if symbol in LEVERAGED_PRODUCTS:
        return None, f"{symbol} 本身已经是杠杆工具，不再叠加第二层杠杆。"
    tool = LEVERAGED_TOOL_MAP.get(symbol)
    if tool:
        return tool, f"{tool} 可作为短线杠杆表达，但仓位应小于正股计划的三分之一。"
    return None, "当前标的没有合适的标准杠杆工具，优先用正股或定义风险的期权结构。"


def _ret_bucket(structure: dict[str, Any]) -> str:
    ret5 = _num(structure.get("return_5d"))
    if ret5 is not None and ret5 >= 6:
        return "extended"
    if ret5 is not None and ret5 <= -5:
        return "weak"
    return "neutral"


def _level(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text or text in PLACEHOLDER_LEVELS or text == "NA":
        return fallback
    return text


TICKER_STOPWORDS = {
    "BUY",
    "SELL",
    "CALL",
    "PUT",
    "ETF",
    "USD",
    "QQ",
}


def _detect_symbol(
    query: str,
    rows: list[dict[str, Any]],
    llm_symbol: str | None = None,
) -> tuple[str | None, list[dict[str, str]]]:
    trace: list[dict[str, str]] = []
    if llm_symbol:
        symbol = llm_symbol.upper()
        trace.append({"tool": "Symbol resolver", "status": "matched", "detail": f"采用模型识别标的 {symbol}"})
        return symbol, trace
    lower = query.lower()
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        name = str(row.get("name") or "").lower()
        if symbol and symbol.lower() in lower:
            trace.append({"tool": "Portfolio matcher", "status": "matched", "detail": f"从当前持仓 symbol 匹配 {symbol}"})
            return symbol, trace
        if name and name in lower:
            trace.append({"tool": "Portfolio matcher", "status": "matched", "detail": f"从当前持仓名称匹配 {symbol}"})
            return symbol, trace
    for symbol, aliases in SYMBOL_ALIASES.items():
        if symbol.lower() in lower or any(alias in lower for alias in aliases):
            trace.append({"tool": "Local synonym resolver", "status": "matched", "detail": f"识别为 {symbol}"})
            return symbol, trace
    for match in re.findall(r"[A-Za-z]{1,6}", query):
        candidate = match.upper()
        if candidate not in TICKER_STOPWORDS:
            trace.append({"tool": "Ticker extractor", "status": "matched", "detail": f"从输入中抽取 ticker {candidate}"})
            return candidate, trace
    search = search_yahoo_symbol(query)
    if search and search.get("symbol"):
        symbol = str(search["symbol"]).upper()
        trace.append({"tool": "Yahoo Finance search", "status": "matched", "detail": f"{search.get('name')} -> {symbol}"})
        return symbol, trace
    trace.append({"tool": "Symbol resolver", "status": "failed", "detail": "未能识别可交易标的"})
    return None, trace


def _detect_intent(query: str, llm_intent: str | None = None) -> tuple[str, list[dict[str, str]]]:
    trace: list[dict[str, str]] = []
    if llm_intent in {"buy_or_add", "protect_profit", "control_loss"}:
        trace.append({"tool": "Intent resolver", "status": "matched", "detail": f"采用模型识别意图 {llm_intent}"})
        return llm_intent, trace
    lower = query.lower()
    if any(word in lower for word in ["保护", "止盈", "卖", "sell", "profit", "涨很多", "涨了一段", "要不要卖"]):
        trace.append({"tool": "Rule intent parser", "status": "matched", "detail": "识别为利润保护/卖出评估"})
        return "protect_profit", trace
    if any(word in lower for word in ["跌", "亏", "补仓", "drawdown", "loss"]):
        trace.append({"tool": "Rule intent parser", "status": "matched", "detail": "识别为回撤控制/补仓评估"})
        return "control_loss", trace
    trace.append({"tool": "Rule intent parser", "status": "matched", "detail": "识别为买入/加仓评估"})
    return "buy_or_add", trace


def _find_position(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("symbol") == symbol:
            return row
    return None


def _market_structure(snapshot: dict[str, Any], symbol: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    trace: list[dict[str, str]] = []
    structures = snapshot.get("market_structure", {})
    existing = structures.get(symbol)
    if existing:
        trace.append({"tool": "Market Structure", "status": "cached", "detail": f"使用 {symbol} 已有结构数据"})
        return existing, trace
    chart = fetch_yahoo_chart(symbol)
    if chart:
        structure = build_market_structure(symbol, chart) or {}
        trace.append({"tool": "Market data", "status": "live", "detail": f"拉取 {symbol} 公开日线并计算结构区间"})
        return structure, trace
    trace.append({"tool": "Market data", "status": "failed", "detail": f"未能取得 {symbol} 行情，输出仅保留账户适配和通用纪律"})
    return {}, trace


def _unresolved_plan(query: str, trace: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "as_of": None,
        "query": query,
        "symbol": "未识别",
        "intent": "unknown",
        "target": {
            "symbol": "未识别",
            "quantity": 0,
            "weight_pct": 0,
            "price": None,
            "return_5d": None,
            "return_20d": None,
            "support_near": "NA",
            "support_major": "NA",
            "resistance": "NA",
            "next_resistance": "NA",
            "structure_state": "未识别到可交易标的。",
            "data_source": "none",
            "tags": [],
        },
        "headline": "我还没有识别出你要分析的标的，请输入股票代码或更完整的公司名。",
        "sections": [
            {
                "title": "如何继续",
                "bullets": [
                    "可以输入：我要买 AAPL、SHOP 涨很多了要不要卖、我想保护 NVDA 浮盈。",
                    "如果是中文简称，模型解析层开启后会更稳；当前规则层也会尝试公开金融搜索。",
                ],
            }
        ],
        "recommended": ["补充股票代码或公司英文名后重新分析。"],
        "avoid": ["不要在标的未确认时生成交易计划。"],
        "trace": trace,
        "source": "agent pipeline",
    }


def _portfolio_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    high_beta = sum(
        row["weight_pct"]
        for row in rows
        if set(row.get("tags", [])) & HIGH_BETA_TAGS
    )
    top_two = sorted(rows, key=lambda row: row["weight_pct"], reverse=True)[:2]
    return {
        "high_beta_pct": round(high_beta, 2),
        "top_two": top_two,
    }


def _target_snapshot(position: dict[str, Any] | None, structure: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "quantity": position.get("quantity") if position else 0,
        "weight_pct": position.get("weight_pct") if position else 0,
        "price": structure.get("price") or (position.get("price") if position else None),
        "return_5d": structure.get("return_5d") if structure.get("return_5d") is not None else (position.get("return_5d") if position else None),
        "return_20d": structure.get("return_20d") if structure.get("return_20d") is not None else (position.get("return_20d") if position else None),
        "support_near": structure.get("support_near", "NA"),
        "support_major": structure.get("support_major", "NA"),
        "resistance": structure.get("resistance", "NA"),
        "next_resistance": structure.get("next_resistance", "NA"),
        "structure_state": structure.get("state", "暂无结构状态。"),
        "data_source": structure.get("source", "embedded fallback snapshot"),
        "tags": position.get("tags", []) if position else [],
    }


def _market_section(snapshot: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    market = snapshot.get("market", {})
    portfolio_markets = _portfolio_markets(rows)
    views = [
        view
        for view in market.get("views", [])
        if view.get("market") in portfolio_markets
    ]
    if views:
        bullets = [f"{view.get('label', view.get('market'))}：{view.get('summary')}" for view in views[:3]]
    else:
        label_map = {"US": "美股", "HK": "港股", "CN": "A 股"}
        bullets = [
            f"{label_map.get(market_code, market_code)}：大盘处在震荡修复阶段，资金风险偏好尚未顺畅。"
            for market_code in sorted(portfolio_markets)
        ]
        bullets.append("交易含义：新的买入动作要分批进行，杠杆工具只适合在大盘明显转强时短线使用。")
    return {
        "title": "大盘风险",
        "bullets": bullets[:3],
    }


def _account_section(position: dict[str, Any] | None, context: dict[str, Any], symbol: str) -> dict[str, Any]:
    if position:
        exposure = (
            f"当前已持有 {symbol} {position.get('quantity')} 股，权重 {position['weight_pct']:.1f}%。"
        )
    else:
        exposure = f"当前账户没有 {symbol} 持仓。"
    top = context["top_two"]
    top_text = "、".join(f"{row['symbol']} {row['weight_pct']:.1f}%" for row in top)
    return {
        "title": "账户风险适配",
        "bullets": [
            exposure,
            f"组合里高波动、成长类资产占比约 {context['high_beta_pct']:.1f}%，最大仓位集中在 {top_text}。",
            "交易含义：如果这次操作还是买入同一类高波动资产，单笔仓位需要比平时更小。",
        ],
    }


def _buy_plan(symbol: str, structure: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str], list[str]]:
    support = _level(structure.get("support_near"), "支撑区")
    major = _level(structure.get("support_major"), "关键支撑区")
    resistance = _level(structure.get("resistance"), "压力区")
    next_resistance = _level(structure.get("next_resistance"), "下一压力区")
    tool, tool_note = _leveraged_tool_for(symbol)
    tool_note = tool_note.rstrip("。")
    bucket = _ret_bucket(structure)
    if bucket == "extended":
        headline = f"{symbol} 短线已经有点偏热，不适合一次性买满。更稳妥的做法是等价格回到{support}附近站稳后分批买；只有突破{resistance}并站住，才考虑很小仓位的杠杆工具。"
    elif bucket == "weak":
        headline = f"{symbol} 还在回撤或弱修复阶段，先看价格能不能守住{support}。如果要买，只适合用很小的正股试探仓，暂时不要用杠杆工具。"
    else:
        headline = f"{symbol} 现在更适合用分批计划来表达看涨，而不是一次性买入。突破{resistance}并站稳后才提高仓位；如果跌破{support}，先暂停买入。"
    sections = [
        {
            "title": f"{symbol} 结构与点位",
            "bullets": [
                structure.get("state", "标的处于震荡或修复结构。"),
                f"核心支撑：{support}；关键防守区：{major}。",
                f"上方压力：{resistance}；若有效突破，下一观察区在 {next_resistance}。",
            ],
        },
        {
            "title": "情景计划",
            "bullets": [
                f"如果价格先回落：等它回到{support}附近并重新走稳，再买入计划仓位的 30%-40%；如果后面还能守住这个区域，再加第二笔。",
                f"如果价格直接向上突破：只有放量站稳{resistance}后，才跟随买入 20%-30% 的计划仓位，第一目标先看{next_resistance}。",
                f"如果想用杠杆工具：{tool_note}；只有在突破确认、且大盘风险偏好不弱时才短线使用。一旦价格跌回{resistance}下方，就先退出杠杆仓位。",
                f"如果计划失败：跌破{support}后，先不要再买入这个标的，也不要用杠杆加仓；如果继续跌向{major}，等价格重新站稳后再评估。",
            ],
        },
    ]
    recommended = [
        f"正股主计划：{support} 企稳后分批买入。",
        f"杠杆备选：{tool or '不使用杠杆工具'}，只在突破确认后短线使用。",
    ]
    avoid = [f"跌破 {support} 后暂停新增风险。"]
    return headline, sections, recommended, avoid


def _profit_plan(symbol: str, structure: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str], list[str]]:
    support = _level(structure.get("support_near"), "支撑区")
    resistance = _level(structure.get("resistance"), "压力区")
    next_resistance = _level(structure.get("next_resistance"), "下一压力区")
    tool, tool_note = _leveraged_tool_for(symbol)
    tool_note = tool_note.rstrip("。")
    bucket = _ret_bucket(structure)
    if bucket == "extended":
        headline = f"{symbol} 已经涨到需要保护利润的阶段。核心仓位可以继续留一部分，但浮盈仓要按{resistance}和{support}两条线分批处理。"
    elif bucket == "weak":
        headline = f"{symbol} 的短线动能已经转弱，现在保护已有利润比继续等反弹更重要。如果跌破{support}，先卖出一部分或买保护。"
    else:
        headline = f"{symbol} 目前还没有明显破坏结构，可以先用{support}作为利润保护线。向上突破{resistance}就继续持有，跌破{support}就执行保护。"
    sections = [
        {
            "title": f"{symbol} 结构与点位",
            "bullets": [
                structure.get("state", "标的短线涨幅较大，进入利润保护观察区。"),
                f"若继续站稳 {resistance}，趋势仍可延续到 {next_resistance}。",
                f"若跌回 {support} 下方，说明短线动能转弱，应降低风险。",
            ],
        },
        {
            "title": "利润保护算法",
            "bullets": [
                f"如果继续强势：只要价格站稳{resistance}，可以保留核心仓位；同时把{support}设为保护参考线，跌破就处理。",
                f"如果高位震荡：在{resistance}附近先卖出 10%-20% 的浮盈仓位，剩下的仓位继续跟踪趋势。",
                f"如果开始转弱：一旦跌破{support}，先卖出一部分或买保护，不要用继续补仓来替代风控。",
                f"如果持有杠杆工具：{tool_note}；如果已经持有 {tool or '杠杆仓'}，先降低杠杆仓位，再决定正股核心仓要不要继续留。",
                "如果不想卖出正股：可以用 protective put 或 collar 给下跌风险上保险。",
            ],
        },
    ]
    recommended = [
        f"{resistance} 附近分批锁定部分利润。",
        f"如果跌破 {support}，先卖出一部分或买保护，不要继续加杠杆。",
    ]
    avoid = ["不把保护问题简化成一次性卖光或继续硬扛。"]
    return headline, sections, recommended, avoid


def _loss_plan(symbol: str, structure: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str], list[str]]:
    support = _level(structure.get("support_near"), "支撑区")
    major = _level(structure.get("support_major"), "关键支撑区")
    tool, tool_note = _leveraged_tool_for(symbol)
    tool_note = tool_note.rstrip("。")
    bucket = _ret_bucket(structure)
    if bucket == "weak":
        headline = f"{symbol} 处在弱势回撤阶段，第一目标是控制亏损继续扩大。如果守不住{support}，先卖出一部分；这个阶段不要用杠杆工具。"
    elif bucket == "extended":
        headline = f"{symbol} 波动比较大，回撤控制要先看{support}能不能守住。只有守住后才讨论正股修复；如果有杠杆仓，先降下来。"
    else:
        headline = f"{symbol} 还在支撑观察区，补仓不是默认动作。先确认价格能不能守住{support}，再决定是否用小仓位正股加回。"
    sections = [
        {
            "title": f"{symbol} 结构与点位",
            "bullets": [
                structure.get("state", "标的处于回撤或弱修复结构。"),
                f"短线支撑：{support}；若有效跌破，下一防守区在 {major}。",
                "只有重新收回支撑并形成更高低点，才恢复加仓资格。",
            ],
        },
        {
            "title": "回撤控制算法",
            "bullets": [
                f"如果价格守住：在{support}上方先保留观察仓，不急着补仓摊低成本。",
                f"如果价格跌破：先卖出一部分，把亏损控制在能承受的范围内；下一步再看{major}附近有没有重新企稳。",
                f"如果价格重新转强：只有重新站回{support}并形成更高的低点后，才考虑用小仓位正股加回。",
                f"如果涉及杠杆工具：{tool_note}；回撤阶段不要新开杠杆仓，已经有的杠杆仓也应该优先退出。",
                "如果仍想继续持有：可以用 protective put 或 put spread 来限制继续下跌的损失。",
            ],
        },
    ]
    recommended = [
        f"守住 {support} 再讨论正股小仓加回。",
        f"如果跌破 {support}，先卖出一部分把亏损控制住，暂时不要使用杠杆工具。",
    ]
    avoid = ["不把卖 put 或加杠杆当作止损替代。"]
    return headline, sections, recommended, avoid


def plan_trade(query: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    rows, _ = _portfolio_rows(snapshot)
    intent, intent_trace = _detect_intent(query)
    symbol, symbol_trace = _detect_symbol(query, rows)
    if symbol:
        trace = [
            *intent_trace,
            *symbol_trace,
            {
                "tool": "LLM intent parser",
                "status": "skipped",
                "detail": "规则/公开搜索已识别标的，未调用模型以降低延迟",
            },
        ]
    else:
        llm, llm_trace = parse_intent_with_llm(query, snapshot)
        if llm:
            symbol, symbol_trace = _detect_symbol(query, rows, llm.get("symbol"))
            intent, intent_trace = _detect_intent(query, llm.get("intent"))
            trace = [*llm_trace, *intent_trace, *symbol_trace]
        else:
            trace = [*llm_trace, *intent_trace, *symbol_trace]
    if not symbol:
        return _unresolved_plan(query, trace)
    position = _find_position(rows, symbol)
    context = _portfolio_context(rows)
    structure, structure_trace = _market_structure(snapshot, symbol)
    trace.extend(structure_trace)
    trace.append({"tool": "Portfolio risk adapter", "status": "computed", "detail": "结合当前持仓权重和高 beta 暴露调整交易建议"})

    if intent == "protect_profit":
        headline, plan_sections, recommended, avoid = _profit_plan(symbol, structure)
    elif intent == "control_loss":
        headline, plan_sections, recommended, avoid = _loss_plan(symbol, structure)
    else:
        headline, plan_sections, recommended, avoid = _buy_plan(symbol, structure)

    sections = [
        _market_section(snapshot, rows),
        _account_section(position, context, symbol),
        *plan_sections,
    ]

    return {
        "as_of": snapshot.get("as_of"),
        "query": query,
        "symbol": symbol,
        "intent": intent,
        "target": _target_snapshot(position, structure, symbol),
        "headline": headline,
        "sections": sections,
        "recommended": recommended,
        "avoid": avoid,
        "trace": trace,
        "source": "demo portfolio + market snapshot + market-structure rules",
    }
