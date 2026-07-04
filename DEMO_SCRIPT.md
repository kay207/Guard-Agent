# 5-Minute Demo Script

## 0:00-0:30 Opening

Portfolio Guard Agent is a risk-first trading copilot for active retail investors.

The core problem is not lack of information. Retail traders already have quotes, news, and charts. The missing layer is portfolio risk discipline: knowing when a winner has become too large, when a loser should not be averaged down, and how to choose the right protection tool.

## 0:30-1:30 Portfolio Scan

Open the dashboard.

Show the left panel:

- Current risk score and risk level.
- Daily, weekly, and monthly risk trend.
- Top positions and weights.
- Risk change and protection alerts.

Explain:

The scan is not just a health score. It breaks risk into concentration, high-beta exposure, index beta, cash and margin buffer, option risk, event risk, liquidity, and FX risk.

In this demo portfolio, TSLA has risen quickly and now triggers a profit-protection reminder. HOOD has pulled back while still being a large high-beta position, so it triggers drawdown control.

## 1:30-2:30 Dynamic Trade: Buy TSLA

Click "买 TSLA" or type:

```text
我想买特斯拉
```

Show the right panel.

Explain the sequence:

1. The agent checks market risk first.
2. Then it checks whether buying TSLA fits the current portfolio.
3. Then it uses market-structure rules to identify support, resistance, breakout, and breakdown scenarios.
4. Finally it gives recommended and not recommended actions.

Key point:

The answer is not "TSLA is good or bad." The answer is: "Do not chase. If it pulls back and holds support, use a small stock position. Do not use short-dated calls or leveraged products because the portfolio already has high-beta concentration."

## 2:30-3:40 Profit Protection

Click "保护 TSLA 浮盈" or type:

```text
特斯拉涨很多了，我要不要卖
```

Explain:

This switches the algorithm from buy planning to profit protection.

The agent does not tell the user to sell everything. It suggests preserving core exposure while protecting part of the gain.

Show the protection tools:

- Partial trim.
- Trailing protection line.
- Protective put.
- Collar.
- Covered call only if the user is willing to cap part of the upside.

## 3:40-4:30 Drawdown Control

Click "控制 HOOD 回撤" or type:

```text
HOOD 跌很多了，要不要补仓
```

Explain:

This is the opposite case. When a large high-beta position falls, the default retail impulse is to average down. The agent reframes the problem: first control portfolio drag, then decide whether the structure has recovered.

Show:

- Wait for support reclaim.
- Reduce exposure if support breaks.
- Use protective put or put spread if the user wants to keep the position.
- Avoid selling puts unless assignment cash and concentration risk are acceptable.

## 4:30-5:00 Close

The innovation is that this is not a stock picker and not a generic chatbot. It is a portfolio-aware trading discipline agent.

It can run locally in demo mode today. In production, the same interfaces can connect to broker APIs, screenshot OCR, QVeris, public market data sources, SEC/HKEX disclosures, and Volcengine for natural language tool orchestration.

