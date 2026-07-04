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


def _float_env(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


def llm_config_status() -> dict[str, Any]:
    api_key_source = ""
    if _env("ARK_API_KEY"):
        api_key_source = "ARK_API_KEY"
    elif _env("VOLCENGINE_API_KEY"):
        api_key_source = "VOLCENGINE_API_KEY"
    model = _env("ARK_MODEL")
    base_url = (_env("ARK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    timeout_seconds = _float_env("ARK_TIMEOUT_SECONDS", 12.0)
    return {
        "api_key_configured": bool(api_key_source),
        "api_key_source": api_key_source or None,
        "model_configured": bool(model),
        "model": model or None,
        "base_url": base_url,
        "responses_url": f"{base_url}/responses",
        "chat_completions_url": f"{base_url}/chat/completions",
        "preferred_api": "responses",
        "timeout_seconds": timeout_seconds,
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


def _system_prompt() -> str:
    return (
        "You are a trading-agent intent parser. Return strict JSON only. "
        "Choose intent from: buy_or_add, protect_profit, control_loss. "
        "Use protect_profit for sell, trim, take profit, hedge a winner, or '要不要卖'. "
        "Use control_loss for drawdown, loss control, average down, or losing positions. "
        "Extract the most likely US/HK/CN ticker symbol. Prefer symbols in the provided portfolio. "
        "If the user names a company in Chinese, map it to the listed ticker when widely known, "
        "for example 苹果 -> AAPL, 英伟达 -> NVDA, 特斯拉 -> TSLA, 闪迪 -> SNDK."
    )


def _user_prompt(query: str, snapshot: dict[str, Any]) -> str:
    return (
        f"Portfolio symbols:\n{_portfolio_context(snapshot)}\n\n"
        f"User query: {query}\n\n"
        'Return JSON like {"symbol":"NVDA","intent":"protect_profit"}'
    )


def _post_json(
    url: str,
    body: dict[str, Any],
    api_key: str,
    timeout_seconds: float,
    label: str,
) -> tuple[dict[str, Any] | None, dict[str, str], bool]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        return None, {
            "tool": label,
            "status": "error",
            "detail": f"{label} HTTP {error.code}: {_error_detail(body_text)}",
        }, False
    except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
        return None, {
            "tool": label,
            "status": "error",
            "detail": f"{label} 请求失败或超时: {error}",
        }, True
    except Exception as error:
        return None, {
            "tool": label,
            "status": "error",
            "detail": f"{label} 调用异常: {type(error).__name__}: {error}",
        }, True
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return None, {
            "tool": label,
            "status": "invalid_response",
            "detail": f"{label} 返回的不是有效 JSON",
        }, True
    return payload, {
        "tool": label,
        "status": "transport_ok",
        "detail": f"{label} 已返回",
    }, False


def _responses_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "\n".join(parts)


def _chat_text(payload: dict[str, Any]) -> str:
    return (
        ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
        or ""
    )


def _parse_llm_payload(payload: dict[str, Any], label: str) -> tuple[dict[str, str] | None, dict[str, str]]:
    content = _responses_text(payload) if label == "Responses API" else _chat_text(payload)
    parsed = _extract_json(content)
    if not parsed:
        return None, {
            "tool": "LLM intent parser",
            "status": "invalid_response",
            "detail": f"{label} 返回内容没有可解析的 JSON",
        }
    symbol = str(parsed.get("symbol") or "").upper().strip()
    intent = str(parsed.get("intent") or "").strip()
    if not symbol or intent not in VALID_INTENTS:
        return None, {
            "tool": "LLM intent parser",
            "status": "invalid_response",
            "detail": f"{label} 返回缺少有效 symbol/intent: {parsed}",
        }
    return {"symbol": symbol, "intent": intent}, {
        "tool": "LLM intent parser",
        "status": "used",
        "detail": f"{label} 识别：{symbol} / {intent}",
    }


def _portfolio_image_prompt() -> str:
    return (
        "You are a portfolio screenshot parser for a risk-control trading agent. "
        "Read one or more brokerage account screenshots and return strict JSON only. "
        "The screenshots may be different pages of the same account; merge them into one deduplicated portfolio. "
        "Extract common stock or ETF holdings. Ignore watchlists, news, orders, and text that is not an actual position. "
        "For each holding return symbol, name, quantity, price, and market_value when visible. "
        "Use US ticker symbols when the screenshot shows company names in Chinese or English. "
        "If quantity or market value is unclear, return null for that field rather than guessing. "
        "Return JSON exactly like: "
        '{"positions":[{"symbol":"NVDA","name":"Nvidia","quantity":20,"price":158.2,"market_value":3164}],'
        '"cash_pct":null,"margin_buffer_pct":null,"notes":[]}'
    )


def _json_from_responses_payload(
    payload: dict[str, Any],
    label: str,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    parsed = _extract_json(_responses_text(payload))
    if not parsed:
        return None, {
            "tool": label,
            "status": "invalid_response",
            "detail": "模型返回内容没有可解析的 JSON",
        }
    return parsed, {
        "tool": label,
        "status": "used",
        "detail": "图片已转为结构化持仓 JSON",
    }


def extract_portfolio_from_image(
    image_data_url: str,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    return extract_portfolio_from_images([image_data_url])


def extract_portfolio_from_images(
    image_data_urls: list[str],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
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
                "tool": "Ark Vision Parser",
                "status": "not_configured",
                "detail": f"缺少 {', '.join(missing)}，无法识别截图持仓",
            }
        ]

    base_url = (_env("ARK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    content = [{"type": "input_text", "text": _portfolio_image_prompt()}]
    for image_url in image_data_urls:
        content.append({"type": "input_image", "image_url": image_url})
    payload, transport_trace, _ = _post_json(
        f"{base_url}/responses",
        {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "thinking": {"type": "disabled"},
        },
        api_key,
        _float_env("ARK_TIMEOUT_SECONDS", 30.0),
        "Ark Vision Parser",
    )
    if not payload:
        return None, [transport_trace]
    parsed, parse_trace = _json_from_responses_payload(payload, "Ark Vision Parser")
    if parsed and not isinstance(parsed.get("positions"), list):
        return None, [
            {
                "tool": "Ark Vision Parser",
                "status": "invalid_response",
                "detail": "模型返回 JSON 中没有 positions 数组",
            }
        ]
    return parsed, [parse_trace]


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
    timeout_seconds = _float_env("ARK_TIMEOUT_SECONDS", 12.0)
    system = _system_prompt()
    user = _user_prompt(query, snapshot)
    trace: list[dict[str, str]] = []

    responses_payload, responses_trace, terminal = _post_json(
        f"{base_url}/responses",
        {
            "model": model,
            "input": f"{system}\n\n{user}",
            "thinking": {"type": "disabled"},
        },
        api_key,
        timeout_seconds,
        "Responses API",
    )
    if responses_payload:
        result, parse_trace = _parse_llm_payload(responses_payload, "Responses API")
        return (result, [parse_trace]) if result else (None, [parse_trace])
    trace.append(responses_trace)
    if terminal:
        return None, trace

    chat_payload, chat_trace, _ = _post_json(
        f"{base_url}/chat/completions",
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 120,
        },
        api_key,
        timeout_seconds,
        "Chat Completions fallback",
    )
    if chat_payload:
        result, parse_trace = _parse_llm_payload(chat_payload, "Chat Completions fallback")
        return (result, [*trace, parse_trace]) if result else (None, [*trace, parse_trace])
    return None, [*trace, chat_trace]


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
