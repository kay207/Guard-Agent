from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any


VALID_INTENTS = {"buy_or_add", "protect_profit", "control_loss"}
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def llm_config_status() -> dict[str, Any]:
    api_key_source = ""
    if _env("ARK_API_KEY"):
        api_key_source = "ARK_API_KEY"
    elif _env("VOLCENGINE_API_KEY"):
        api_key_source = "VOLCENGINE_API_KEY"
    model = _env("ARK_MODEL")
    base_url = (_env("ARK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    return {
        "api_key_configured": bool(api_key_source),
        "api_key_source": api_key_source or None,
        "model_configured": bool(model),
        "model": model or None,
        "base_url": base_url,
        "chat_completions_url": f"{base_url}/chat/completions",
    }


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


def _error_detail(payload: str) -> str:
    if not payload:
        return "no response body"
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return payload[:220]
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or error
            return str(message)[:220]
        message = parsed.get("message") or parsed.get("msg")
        if message:
            return str(message)[:220]
    return payload[:220]


def parse_intent_with_llm(
    query: str,
    snapshot: dict[str, Any],
) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
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
        missing = []
        if not api_key:
            missing.append("ARK_API_KEY")
        if not model:
            missing.append("ARK_MODEL")
        return None, [
            {
                "tool": "LLM intent parser",
                "status": "not_configured",
                "detail": f"缺少 {', '.join(missing)}，进入规则/搜索兜底",
            }
        ]
    base_url = (_env("ARK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    url = f"{base_url}/chat/completions"
    system = (
        "You are a trading-agent intent parser. Return strict JSON only. "
        "Choose intent from: buy_or_add, protect_profit, control_loss. "
        "Use protect_profit for sell, trim, take profit, hedge a winner, or '要不要卖'. "
        "Use control_loss for drawdown, loss control, average down, or losing positions. "
        "Extract the most likely US/HK/CN ticker symbol. Prefer symbols in the provided portfolio. "
        "If the user names a company in Chinese, map it to the listed ticker when widely known, "
        "for example 苹果 -> AAPL, 英伟达 -> NVDA, 特斯拉 -> TSLA, 闪迪 -> SNDK."
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
            response_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return None, [
            {
                "tool": "LLM intent parser",
                "status": "error",
                "detail": f"Ark HTTP {error.code}: {_error_detail(body)}",
            }
        ]
    except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
        return None, [
            {
                "tool": "LLM intent parser",
                "status": "error",
                "detail": f"Ark 请求失败或超时: {error}",
            }
        ]
    except Exception as error:
        return None, [
            {
                "tool": "LLM intent parser",
                "status": "error",
                "detail": f"Ark 调用异常: {type(error).__name__}: {error}",
            }
        ]
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return None, [
            {
                "tool": "LLM intent parser",
                "status": "invalid_response",
                "detail": "Ark 返回的不是有效 JSON",
            }
        ]
    content = (
        ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
        or ""
    )
    parsed = _extract_json(content)
    if not parsed:
        return None, [
            {
                "tool": "LLM intent parser",
                "status": "invalid_response",
                "detail": "模型返回内容没有可解析的 JSON",
            }
        ]
    symbol = str(parsed.get("symbol") or "").upper().strip()
    intent = str(parsed.get("intent") or "").strip()
    if not symbol or intent not in VALID_INTENTS:
        return None, [
            {
                "tool": "LLM intent parser",
                "status": "invalid_response",
                "detail": f"模型返回缺少有效 symbol/intent: {parsed}",
            }
        ]
    result = {"symbol": symbol, "intent": intent}
    return result, [
        {
            "tool": "LLM intent parser",
            "status": "used",
            "detail": f"模型识别：{symbol} / {intent}",
        }
    ]


def extract_intent_with_llm(query: str, snapshot: dict[str, Any]) -> dict[str, str] | None:
    result, _ = parse_intent_with_llm(query, snapshot)
    return result


def diagnose_llm(snapshot: dict[str, Any]) -> dict[str, Any]:
    config = llm_config_status()
    result, trace = parse_intent_with_llm("我要买苹果", snapshot)
    return {
        **config,
        "probe_query": "我要买苹果",
        "probe_ok": bool(result),
        "probe_result": result,
        "probe_trace": trace,
    }
