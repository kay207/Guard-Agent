from __future__ import annotations

from typing import Any


HIGH_BETA_TAGS = {"high_beta", "leveraged", "speculative_growth", "crypto_linked"}
INDEX_TAGS = {"index_beta", "index_income"}
EVENT_SENSITIVE_TAGS = {
    "high_beta",
    "leveraged",
    "speculative_growth",
    "crypto_linked",
    "semiconductor",
    "china_internet",
    "fintech",
}
EVENT_SENSITIVE_SYMBOLS = {
    "TSLA",
    "TSLL",
    "NVDA",
    "NVDL",
    "TSM",
    "SNDK",
    "SOXL",
    "HOOD",
    "SOFI",
    "COIN",
    "CRCL",
    "MSTR",
    "MARA",
    "PDD",
    "BABA",
    "JD",
    "BIDU",
    "0700.HK",
    "0981.HK",
    "1024.HK",
    "3690.HK",
    "9618.HK",
    "9988.HK",
}


def _value(position: dict[str, Any]) -> float:
    if position.get("market_value") is not None:
        return float(position.get("market_value") or 0)
    return float(position.get("quantity") or 0) * float(position.get("price") or 0)


def _pct(value: float, total: float) -> float:
    return round(value / total * 100, 2) if total else 0.0


def _status(value: float, thresholds: tuple[float, float]) -> str:
    warn, high = thresholds
    if value >= high:
        return "高风险"
    if value >= warn:
        return "偏高"
    return "正常"


def _market_for(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "").upper()
    if row.get("market"):
        return str(row["market"]).upper()
    if symbol.endswith(".HK"):
        return "HK"
    if symbol.endswith((".SS", ".SZ")):
        return "CN"
    return "US"


def _join_top(rows: list[dict[str, Any]], limit: int = 3) -> str:
    if not rows:
        return "无主要贡献项"
    return "、".join(f"{row['symbol']} {row['weight_pct']:.1f}%" for row in rows[:limit])


def _pct_by(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    grouped: dict[str, float] = {}
    for row in rows:
        label = str(row.get(key) or "USD").upper()
        grouped[label] = grouped.get(label, 0.0) + float(row.get("weight_pct") or 0)
    return {label: round(value, 1) for label, value in sorted(grouped.items())}


def _market_pct(rows: list[dict[str, Any]]) -> dict[str, float]:
    grouped: dict[str, float] = {}
    for row in rows:
        market = _market_for(row)
        grouped[market] = grouped.get(market, 0.0) + float(row.get("weight_pct") or 0)
    return {label: round(value, 1) for label, value in sorted(grouped.items())}


def _group_text(grouped: dict[str, float]) -> str:
    return "、".join(f"{label} {value:.1f}%" for label, value in grouped.items()) if grouped else "无"


def _change_label(value: float) -> str:
    if value >= 6:
        return "明显恶化"
    if value >= 2:
        return "恶化"
    if value <= -6:
        return "明显改善"
    if value <= -2:
        return "改善"
    return "基本持平"


def _risk_score(
    top1_pct: float,
    top3_pct: float,
    high_beta_pct: float,
    leveraged_pct: float,
    option_theta_daily_pct: float,
    cash_pct: float,
) -> int:
    concentration = min(25, top1_pct * 0.45 + max(0, top3_pct - 45) * 0.45)
    beta = min(30, high_beta_pct * 0.35 + leveraged_pct * 0.8)
    options = min(15, abs(option_theta_daily_pct) * 9)
    cash = 14 if cash_pct < 0 else 8 if cash_pct < 5 else 2
    score = 18 + concentration + beta + options + cash
    return int(round(max(0, min(100, score))))


def _risk_level(score: int) -> str:
    if score >= 75:
        return "偏高"
    if score >= 55:
        return "中等"
    return "可控"


def _top_positions(positions: list[dict[str, Any]], total_value: float) -> list[dict[str, Any]]:
    rows = []
    for pos in positions:
        value = _value(pos)
        rows.append(
            {
                "symbol": pos["symbol"],
                "name": pos.get("name", pos["symbol"]),
                "value": round(value, 2),
                "currency": pos.get("currency", "USD"),
                "base_currency": pos.get("base_currency", "USD"),
                "market": pos.get("market"),
                "native_market_value": pos.get("native_market_value"),
                "fx_rate_to_usd": pos.get("fx_rate_to_usd"),
                "weight_pct": _pct(value, total_value),
                "return_5d": pos.get("return_5d"),
                "return_20d": pos.get("return_20d"),
                "tags": pos.get("tags", []),
            }
        )
    return sorted(rows, key=lambda item: item["value"], reverse=True)


def _profit_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        ret = float(row.get("return_5d") or 0)
        weight = float(row.get("weight_pct") or 0)
        tags = set(row.get("tags", []))
        if ret >= 6 and (weight >= 8 or tags & HIGH_BETA_TAGS):
            alerts.append(
                {
                    "symbol": row["symbol"],
                    "title": f"{row['symbol']} 上涨后风险贡献上升",
                    "severity": "profit",
                    "evidence": f"近 5 日 {ret:+.1f}%，当前权重 {weight:.1f}%。",
                    "meaning": "上涨让浮盈变大，也让组合更依赖该标的继续走强。",
                    "actions": [
                        "保留核心仓位，但不要顺势加杠杆。",
                        "若接近压力区，可分批锁定 10%-20% 浮盈。",
                        "不想卖股时，优先评估 protective put 或 collar。",
                        "只有愿意放弃部分上行时，才考虑较远虚值 covered call。",
                    ],
                }
            )
    return alerts


def _loss_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        ret = float(row.get("return_5d") or 0)
        weight = float(row.get("weight_pct") or 0)
        tags = set(row.get("tags", []))
        if ret <= -6 and (weight >= 8 or tags & HIGH_BETA_TAGS):
            alerts.append(
                {
                    "symbol": row["symbol"],
                    "title": f"{row['symbol']} 回撤开始拖累账户",
                    "severity": "loss",
                    "evidence": f"近 5 日 {ret:+.1f}%，当前权重 {weight:.1f}%。",
                    "meaning": "主要风险不是价格变便宜，而是继续补仓会放大集中度和回撤。",
                    "actions": [
                        "不要在破位后用补仓替代风控。",
                        "若跌破结构支撑，优先分批降低拖累。",
                        "仍想持有时，选择 protective put 或 put spread 限制下行。",
                        "卖 put 摊低成本前，必须确认现金和接股风险。",
                    ],
                }
            )
    return alerts


def _beta_module(
    rows: list[dict[str, Any]],
    high_beta_pct: float,
    index_pct: float,
    leveraged_pct: float,
) -> dict[str, Any]:
    high_beta_rows = [
        row for row in rows if set(row.get("tags", [])) & HIGH_BETA_TAGS
    ]
    index_rows = [
        row for row in rows if set(row.get("tags", [])) & INDEX_TAGS
    ]
    high_beta_source = _join_top(high_beta_rows) if high_beta_rows else "无明显高 beta/主题持仓"
    index_source = _join_top(index_rows) if index_rows else "无指数/宽基底仓"
    status = _status(high_beta_pct + leveraged_pct * 0.5, (35, 55))
    if index_pct < 10:
        index_read = (
            "指数或宽基底仓很低，组合缺少用来分散单股风险的稳定底座。"
        )
    elif index_pct < 25:
        index_read = "有一部分指数底仓，但还不足以明显抵消高波动主题股的回撤。"
    else:
        index_read = "指数底仓占比不低，对冲了一部分个股和主题波动。"
    if high_beta_pct >= 55:
        beta_read = "账户更像在押注少数高波动主题继续走强，而不是一个均衡组合。"
    elif high_beta_pct >= 35:
        beta_read = "进攻性资产占比较高，市场转弱时净值容易跟随风险偏好下行。"
    else:
        beta_read = "高波动主题没有主导账户，整体风险偏好相对可控。"
    leveraged_text = (
        f"其中杠杆产品约 {leveraged_pct:.1f}%，会放大日内和隔夜波动。"
        if leveraged_pct > 0
        else "当前未识别到明显杠杆产品暴露。"
    )
    return {
        "key": "beta",
        "title": "Beta 与主题暴露",
        "status": status,
        "evidence": (
            f"高 beta / 投机成长类暴露 {high_beta_pct:.1f}%，主要来自 {high_beta_source}；"
            f"指数/宽基相关暴露 {index_pct:.1f}%，主要来自 {index_source}。"
        ),
        "impact": f"{beta_read}{index_read}{leveraged_text}",
        "change": "风险含义：新增交易如果继续买高 beta 或同一主题，应降低单笔仓位；若先补足底仓或提高现金，账户抗波动能力会更强。",
    }


def _event_items(rows: list[dict[str, Any]]) -> list[str]:
    symbols = {str(row.get("symbol") or "").upper() for row in rows}
    tags = set().union(*(set(row.get("tags", [])) for row in rows)) if rows else set()
    items: list[str] = []
    if any(symbol in symbols for symbol in {"TSLA", "TSLL"}):
        items.append("TSLA：交付量、财报、毛利率和自动驾驶/机器人叙事")
    if symbols & {"NVDA", "NVDL", "TSM", "SNDK", "SOXL"} or "semiconductor" in tags:
        items.append("半导体/AI 链：财报指引、出口管制、台积电月度营收和资本开支")
    if symbols & {"HOOD", "SOFI"} or "fintech" in tags:
        items.append("金融科技：财报、交易量、加密交易活跃度和监管变化")
    if symbols & {"COIN", "CRCL", "MARA", "MSTR", "RIOT"} or "crypto_linked" in tags:
        items.append("加密相关：BTC 波动、稳定币监管和风险偏好变化")
    if symbols & {"PDD", "BABA", "JD", "9988.HK", "0700.HK", "3690.HK"} or "china_internet" in tags:
        items.append("中概/港股互联网：财报、平台监管、消费数据和政策预期")
    markets = _market_pct(rows)
    if markets.get("US", 0) >= 20:
        items.append("美国宏观：CPI/PCE、非农、FOMC 和美债利率会影响成长股估值")
    if markets.get("HK", 0) + markets.get("CN", 0) >= 15:
        items.append("中国/香港宏观：PMI、社融、LPR、政策会议和港股流动性")
    return items[:5]


def _event_module(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sensitive_rows = [
        row
        for row in rows
        if set(row.get("tags", [])) & EVENT_SENSITIVE_TAGS
        or str(row.get("symbol") or "").upper() in EVENT_SENSITIVE_SYMBOLS
    ]
    sensitive_pct = round(sum(float(row.get("weight_pct") or 0) for row in sensitive_rows), 1)
    core = _join_top(rows, 4)
    items = _event_items(rows)
    status = "偏高" if sensitive_pct >= 45 or (rows and rows[0]["weight_pct"] >= 25) else "正常"
    watch_text = "；".join(items) if items else "当前没有识别到特别集中的事件驱动持仓，仍需按财报和宏观日历复核。"
    return {
        "key": "event",
        "title": "事件风险",
        "status": status,
        "evidence": f"核心持仓为 {core}；事件敏感型资产约 {sensitive_pct:.1f}%。需要关注：{watch_text}。",
        "impact": "这些事件会改变盈利预期、估值折现率或市场流动性；事件前后继续加仓、卖期权或使用杠杆，容易把普通波动放大成账户级回撤。",
        "change": "当前版本先输出事件清单，不判断具体日期；真实上线后应接入财报和宏观日历，把未来 7-14 天事件自动标红。",
    }


def _fx_module(rows: list[dict[str, Any]], account: dict[str, Any]) -> dict[str, Any]:
    currency_pct = _pct_by(rows, "currency")
    non_usd_pct = round(sum(value for currency, value in currency_pct.items() if currency != "USD"), 1)
    fx_rows = [row for row in rows if row.get("currency") and row.get("currency") != "USD"]
    status = "偏高" if non_usd_pct >= 15 else "正常"
    if fx_rows:
        rates = "、".join(
            f"{row['symbol']} {row.get('currency')}→USD {float(row.get('fx_rate_to_usd') or 1):.4f}"
            for row in fx_rows[:3]
        )
        evidence = (
            f"账户以 USD 作为风险扫描基准；币种敞口为 {_group_text(currency_pct)}。"
            f"非美元资产约 {non_usd_pct:.1f}%，已按公开汇率换算，示例汇率：{rates}。"
        )
        impact = (
            "非美元持仓的美元口径收益 = 股票本身涨跌 + 汇率变化。"
            "如果港股上涨但 HKD 相对 USD 走弱，美元计价收益会被抵消；若现金/融资币种和资产币种不一致，还会出现补仓或还款时的币种错配。"
        )
    else:
        evidence = account.get("fx_evidence", "当前识别到的核心风险资产主要以 USD 计价。")
        impact = "汇率不是当前账户的主要风险来源；后续如果加入港股、A 股或多币种融资，需要重新计算币种敞口。"
    return {
        "key": "fx",
        "title": "汇率与多币种风险",
        "status": status,
        "evidence": evidence,
        "impact": impact,
        "change": "当前按最新可得公开汇率做静态换算；后续应跟踪非美元敞口占比和汇率变化对账户净值的贡献。",
    }


def build_scan(snapshot: dict[str, Any]) -> dict[str, Any]:
    positions = snapshot.get("positions", [])
    total_value = sum(_value(pos) for pos in positions)
    rows = _top_positions(positions, total_value)

    top1 = rows[0]["weight_pct"] if rows else 0.0
    top3 = round(sum(row["weight_pct"] for row in rows[:3]), 2)
    high_beta_value = sum(
        row["value"] for row in rows if set(row.get("tags", [])) & HIGH_BETA_TAGS
    )
    index_value = sum(row["value"] for row in rows if set(row.get("tags", [])) & INDEX_TAGS)
    leveraged_value = sum(row["value"] for row in rows if "leveraged" in row.get("tags", []))
    high_beta_pct = _pct(high_beta_value, total_value)
    index_pct = _pct(index_value, total_value)
    leveraged_pct = _pct(leveraged_value, total_value)

    account = snapshot.get("account", {})
    cash_pct = float(account.get("cash_pct") or 0)
    margin_buffer_pct = float(account.get("margin_buffer_pct") or 0)
    option_theta_daily_pct = float(account.get("option_theta_daily_pct") or 0)
    score = _risk_score(top1, top3, high_beta_pct, leveraged_pct, option_theta_daily_pct, cash_pct)
    history = snapshot.get("risk_history", {})
    if history.get("mode") == "current_snapshot_only":
        previous_day_score = score
        week_ago_score = score
        month_ago_score = score
    else:
        previous_day_score = int(history.get("previous_day_score", score))
        week_ago_score = int(history.get("week_ago_score", score))
        month_ago_score = int(history.get("month_ago_score", score))
    week_change = score - week_ago_score
    month_change = score - month_ago_score

    modules = [
        {
            "key": "concentration",
            "title": "集中度风险",
            "status": _status(top1, (15, 25)),
            "evidence": f"最大单一持仓 {top1:.1f}%，Top 3 合计 {top3:.1f}%。",
            "impact": "少数股票决定账户大部分波动，容易在单一标的回撤时被动失去交易主动权。",
            "change": "过去一周偏恶化，主要来自高波动持仓权重上升。",
        },
        _beta_module(rows, high_beta_pct, index_pct, leveraged_pct),
        {
            "key": "cash",
            "title": "现金与融资安全垫",
            "status": "正常" if cash_pct >= 5 and margin_buffer_pct >= 25 else "偏高",
            "evidence": f"现金缓冲 {cash_pct:.1f}%，保证金安全垫 {margin_buffer_pct:.1f}%。",
            "impact": "现金和保证金决定下跌时是否有余地做保护，而不是被迫卖出。",
            "change": "基本持平。",
        },
        {
            "key": "options",
            "title": "期权风险",
            "status": "正常" if abs(option_theta_daily_pct) < 0.6 else "偏高",
            "evidence": f"期权时间损耗约 {option_theta_daily_pct:+.2f}%/日，当前没有短期裸卖结构。",
            "impact": "期权风险主要来自时间损耗、隐含波动变化和到期日集中。",
            "change": "基本持平。",
        },
        _event_module(rows),
        {
            "key": "liquidity",
            "title": "流动性风险",
            "status": "正常",
            "evidence": "核心持仓为美股/ETF，大部分正股流动性充足；期权腿仍需按 OI、volume、bid/ask 单独筛选。",
            "impact": "流动性差会让保护策略看似可行，但实际滑点过高。",
            "change": "基本持平。",
        },
        _fx_module(rows, account),
    ]

    return {
        "as_of": snapshot.get("as_of"),
        "data_mode": snapshot.get("data_mode"),
        "total_value": round(total_value, 2),
        "risk_score": score,
        "risk_level": _risk_level(score),
        "risk_trend": {
            "day": _change_label(score - previous_day_score),
            "week": _change_label(week_change),
            "month": _change_label(month_change),
            "week_delta": week_change,
            "month_delta": month_change,
        },
        "headline": f"账户风险 {_risk_level(score)}，过去一周{_change_label(week_change)}。",
        "modules": modules,
        "top_positions": rows[:8],
        "profit_alerts": _profit_alerts(rows),
        "loss_alerts": _loss_alerts(rows),
        "methodology": [
            "单一持仓、Top 3 权重用于衡量集中度。",
            "高 beta、杠杆和投机成长标签用于衡量风险偏好暴露。",
            "现金、保证金、期权 theta、事件和流动性作为风险修正项。",
            "风险变化按日、周、月对比风险预算使用情况。",
        ],
    }
