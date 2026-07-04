from __future__ import annotations

from typing import Any


HIGH_BETA_TAGS = {"high_beta", "leveraged", "speculative_growth", "crypto_linked"}
INDEX_TAGS = {"index_beta", "index_income"}


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
        {
            "key": "beta",
            "title": "Beta 与主题暴露",
            "status": _status(high_beta_pct, (35, 55)),
            "evidence": f"高 beta / 投机成长类暴露 {high_beta_pct:.1f}%，指数相关暴露 {index_pct:.1f}%。",
            "impact": "组合更依赖风险偏好和科技成长风格，市场转弱时回撤会同步放大。",
            "change": "过去一周恶化，部分强势标的上涨后被动抬高风险预算占用。",
        },
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
        {
            "key": "event",
            "title": "事件风险",
            "status": snapshot.get("event_risk", {}).get("status", "正常"),
            "evidence": snapshot.get("event_risk", {}).get("evidence", "未来一周没有被标记为最高优先级的财报事件。"),
            "impact": "财报、CPI、FOMC 等事件前后，卖权和杠杆加仓需要降级处理。",
            "change": "随日历滚动变化。",
        },
        {
            "key": "liquidity",
            "title": "流动性风险",
            "status": "正常",
            "evidence": "核心持仓为美股/ETF，大部分正股流动性充足；期权腿仍需按 OI、volume、bid/ask 单独筛选。",
            "impact": "流动性差会让保护策略看似可行，但实际滑点过高。",
            "change": "基本持平。",
        },
        {
            "key": "fx",
            "title": "汇率与多币种风险",
            "status": account.get("fx_status", "正常"),
            "evidence": account.get("fx_evidence", "主要风险资产以 USD 计价，少量 HKD/CNH 现金影响可控。"),
            "impact": "多币种现金和融资会让账户收益与汇率波动叠加。",
            "change": "基本持平。",
        },
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
