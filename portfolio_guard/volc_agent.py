from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


VALID_INTENTS = {"buy_or_add", "protect_profit", "control_loss"}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _extract_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _portfolio_context(snapshot: dict[str, Any]) -> str:
    rows = []
    for pos in snapshot.get("positions", []):
        symbol = str(pos.get("symbol") or "").upper()
        name = str(pos.get("name") or symbol)
        if symbol:
            rows.append(f"{symbol}: {name}")
    return "\n".join(rows[:80])


def extract_intent_with_llm(query: str, snapshot: dict[str, Any]) -> dict[str, str] | None:
    """
    Optional Volcengine Ark/OpenAI-compatible parser.

    Env vars:
      ARK_API_KEY or VOLCENGINE_API_KEY
      ARK_MODEL
      ARK_BASE_URL, defaults to the OpenAI-compatible Ark API base.
    """
    api_key = _env("ARK_API_KEY") or _env("VOLCENGINE_API_KEY")
    model = _env("ARK_MODEL")
    if not api_key or not model:
        return None
    base_url = (_env("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    url = f"{base_url}/chat/completions"
    system = (
        "You are a trading-agent intent parser. Return strict JSON only. "
        "Choose intent from: buy_or_add, protect_profit, control_loss. "
        "Use protect_profit for sell, trim, take profit, hedge a winner, or '要不要卖'. "
        "Use control_loss for drawdown, loss control, average down, or losing positions. "
        "Extract the most likely ticker symbol. Prefer symbols in the provided portfolio."
    )
    user = (
        f"Portfolio symbols:\n{_portfolio_context(snapshot)}\n\n"
        f"User query: {query}\n\n"
        'Return JSON like {"symbol":"NVDA","intent":"protect_profit"}'
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 120,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=4.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    content = (
        ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
        or ""
    )
    parsed = _extract_json(content)
    if not parsed:
        return None
    symbol = str(parsed.get("symbol") or "").upper().strip()
    intent = str(parsed.get("intent") or "").strip()
    if not symbol or intent not in VALID_INTENTS:
        return None
    return {"symbol": symbol, "intent": intent}

