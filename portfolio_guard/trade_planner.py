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
}


def _position_value(position: dict[str, Any]) -> float:
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


def _market_section(snapshot: dict[str, Any]) -> dict[str, Any]:
    market = snapshot.get("market", {})
    return {
        "title": "大盘风险",
        "bullets": [
            market.get("summary", "当前市场处在震荡修复阶段，适合先控制仓位再寻找确认点。"),
            market.get("risk_note", "风险偏好并不顺畅，追涨和加杠杆需要降级处理。"),
            f"QQQ: {market.get('qqq_change', 'NA')}，SPY: {market.get('spy_change', 'NA')}，VIX: {market.get('vix', 'NA')}",
        ],
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
            f"组合高 beta / 投机成长暴露约 {context['high_beta_pct']:.1f}%，最大仓位集中在 {top_text}。",
            "交易含义：如果新动作继续增加同方向高 beta，仓位应低于普通单笔交易规模。",
        ],
    }


def _buy_plan(symbol: str, structure: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str], list[str]]:
    support = structure.get("support_near", "第一支撑区")
    major = structure.get("support_major", "关键支撑区")
    resistance = structure.get("resistance", "突破观察区")
    next_resistance = structure.get("next_resistance", "下一压力区")
    headline = f"不建议直接追高买入 {symbol}；优先等回踩确认，再用小仓位正股表达看涨。"
    sections = [
        {
            "title": f"{symbol} Market Structure Trading",
            "bullets": [
                structure.get("state", "标的处于震荡或修复结构。"),
                f"核心支撑：{support}；关键防守区：{major}。",
                f"上方压力：{resistance}；若有效突破，下一观察区在 {next_resistance}。",
            ],
        },
        {
            "title": "情景计划",
            "bullets": [
                f"回踩方案：回到 {support} 并企稳，第一笔小仓位买正股。",
                f"突破方案：放量站稳 {resistance} 后，才考虑更小仓位跟随。",
                f"防守方案：跌破 {support} 且大盘同步走弱，不新增买入；跌向 {major} 后重新评估。",
            ],
        },
    ]
    recommended = [
        "回踩企稳后买正股，新增风险控制在组合净值 1%-2%。",
        "突破跟随只用更小仓位，不把突破当作重仓信号。",
        "如果账户风险下降，再考虑 call spread，而不是直接买短期期权。",
    ]
    avoid = [
        "不建议现在直接追高。",
        "不建议买短期期权 Call。",
        "不建议使用 2 倍或 3 倍杠杆产品表达同一方向。",
    ]
    return headline, sections, recommended, avoid


def _profit_plan(symbol: str, structure: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str], list[str]]:
    support = structure.get("support_near", "短线支撑区")
    resistance = structure.get("resistance", "上方压力区")
    next_resistance = structure.get("next_resistance", "下一压力区")
    headline = f"不建议只用“卖光或不卖”来处理 {symbol}；更合理的是保留核心仓位，同时保护一部分浮盈。"
    sections = [
        {
            "title": f"{symbol} Market Structure Trading",
            "bullets": [
                structure.get("state", "标的短线涨幅较大，进入利润保护观察区。"),
                f"若继续站稳 {resistance}，趋势仍可延续到 {next_resistance}。",
                f"若跌回 {support} 下方，说明短线动能转弱，应降低风险。",
            ],
        },
        {
            "title": "利润保护算法",
            "bullets": [
                f"强势延续：保留核心仓位，保护线跟随上移到 {support} 附近。",
                f"高位震荡：分批卖出 10%-20% 浮盈仓位，或卖较远虚值 covered call。",
                "不想卖股：用 protective put 或 collar 保护下行，同时保留主要上行参与。",
                f"跌破 {support}：优先减仓或买保护，而不是继续加仓摊低。",
            ],
        },
    ]
    recommended = [
        "保留核心仓位，先保护 20%-30% 的浮盈暴露。",
        "接近压力区时分批止盈，不做一次性全卖。",
        "愿意保留股票时，优先 protective put 或 collar。",
    ]
    avoid = [
        "不建议因为涨多就全仓清掉。",
        "不建议在压力区继续加杠杆。",
        "不建议卖太近的 covered call，以免过早封顶核心仓位。",
    ]
    return headline, sections, recommended, avoid


def _loss_plan(symbol: str, structure: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str], list[str]]:
    support = structure.get("support_near", "短线支撑区")
    major = structure.get("support_major", "关键支撑区")
    headline = f"先控制 {symbol} 的回撤拖累，不把补仓当作默认动作。"
    sections = [
        {
            "title": f"{symbol} Market Structure Trading",
            "bullets": [
                structure.get("state", "标的处于回撤或弱修复结构。"),
                f"短线支撑：{support}；若有效跌破，下一防守区在 {major}。",
                "只有重新收回支撑并形成更高低点，才恢复加仓资格。",
            ],
        },
        {
            "title": "回撤控制算法",
            "bullets": [
                f"守住 {support}：只观察，不急补。",
                f"跌破 {support}：降低仓位，避免亏损仓位继续扩大。",
                "仍想持有：用 protective put 或 put spread 限制下行。",
                "若隐含波动过高，减仓通常比买保护更直接。",
            ],
        },
    ]
    recommended = [
        "等待收回支撑后再讨论加仓。",
        "跌破支撑时优先降低拖累。",
        "需要继续持有时，使用 protective put 或 put spread。",
    ]
    avoid = [
        "不建议越跌越补。",
        "不建议用卖 put 代替止损。",
        "不建议在大盘走弱时扩大同一风险敞口。",
    ]
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
        _market_section(snapshot),
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
