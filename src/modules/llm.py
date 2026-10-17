"""Streaming client for OpenAI-compatible /chat/completions."""

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from threading import Event

from .config import Model


class ApiError(Exception):
    pass


def _events(resp) -> Iterator[dict]:
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        yield json.loads(data)


def complete(
        model: Model,
        messages: list[dict],
        tools: list[dict],
        thinking: str,
        on_text: Callable[[str], None],
        on_reasoning: Callable[[str], None],
        stop: Event,
    ) -> dict:
    """Stream one completion, return the assistant message; ends early when stop is set."""
    body = {"model": model.id, "messages": messages, "tools": tools, "stream": True}
    if effort := model.efforts.get(thinking):
        body["reasoning_effort"] = effort
    headers = {"Content-Type": "application/json"}
    if key := model.api_key:
        headers["Authorization"] = f"Bearer {key}"
    url = model.base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)

    try:
        resp = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as e:
        raise ApiError(f"{e.code} {e.reason}: {e.read().decode(errors='replace')}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"{url}: {e.reason}") from None

    content, reasoning, calls = [], [], {}
    with resp:
        for event in _events(resp):
            if stop.is_set():
                break
            if "error" in event:
                raise ApiError(json.dumps(event["error"]))
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                # deepseek and llama.cpp use reasoning_content, openrouter uses reasoning
                if text := delta.get("reasoning_content") or delta.get("reasoning"):
                    reasoning.append(text)
                    on_reasoning(text)
                if text := delta.get("content"):
                    content.append(text)
                    on_text(text)
                for tc in delta.get("tool_calls") or []:
                    call = calls.setdefault(tc.get("index", 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    call["id"] = tc.get("id") or call["id"]
                    fn = tc.get("function") or {}
                    call["function"]["name"] += fn.get("name") or ""
                    call["function"]["arguments"] += fn.get("arguments") or ""

    msg = {"role": "assistant", "content": "".join(content)}
    # deepseek requires reasoning to be sent back during a tool-call loop
    if reasoning:
        msg["reasoning_content"] = "".join(reasoning)
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return msg
