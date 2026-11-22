"""Streaming client for OpenAI-compatible /chat/completions."""

import http.client
import json
import os
import re
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from threading import Event

from .config import Model

# rate limits and server errors usually pass in seconds; wait this long before each retry, None gives up
RETRY_DELAYS: list[float | None] = [1, 2, 4, None]
# how openai, llama.cpp, openrouter and others word a request over the model's context
TOO_LONG = re.compile(r"context[ _](length|size|window)|too long|too many tokens", re.IGNORECASE)


class ApiError(Exception):
    pass


class ContextTooLong(ApiError):
    """The server rejected the request as longer than the model's context."""


def _events(resp) -> Iterator[dict]:
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        yield json.loads(data)


def _shutdown_on_stop(resp, stop: Event, done: Event) -> None:
    # a read waiting for the first token (long prompt processing) never sees stop, so cut the connection
    while not done.wait(0.1):
        if stop.is_set():
            try:
                with socket.socket(fileno=os.dup(resp.fileno())) as sock:
                    sock.shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass
            return


def complete(
        model: Model,
        messages: list[dict],
        tools: list[dict],
        thinking: str,
        on_text: Callable[[str], None],
        on_reasoning: Callable[[str], None],
        stop: Event,
        on_retry: Callable[[float, str], None] | None = None,
    ) -> dict:
    """Stream one completion, return the assistant message plus the server's token "usage"; ends early when stop is set."""
    # keys starting with _ are metadata for py-agent and plugins, not for the model
    messages = [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
    body = {"model": model.id, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
    # some servers reject an empty tools list
    if tools:
        body["tools"] = tools
    if effort := model.efforts.get(thinking):
        body["reasoning_effort"] = effort
    headers = {"Content-Type": "application/json"}
    if key := model.api_key:
        headers["Authorization"] = f"Bearer {key}"
    url = model.base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)

    for delay in RETRY_DELAYS:
        try:
            resp = urllib.request.urlopen(req, timeout=600)
            break
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            error = ApiError(f"{e.code} {e.reason}: {text}")
            if e.code in (400, 413) and TOO_LONG.search(text):
                raise ContextTooLong(str(error)) from None
            if e.code != 429 and e.code < 500:
                raise error from None
        except urllib.error.URLError as e:
            raise ApiError(f"{url}: {e.reason}") from None
        if delay is None:
            raise error from None
        if on_retry:
            on_retry(delay, str(error))
        if stop.wait(delay):
            return {"role": "assistant", "content": ""}

    content, reasoning, calls, usage = [], [], {}, None
    done = Event()
    threading.Thread(target=_shutdown_on_stop, args=(resp, stop, done), daemon=True).start()
    try:
        with resp:
            for event in _events(resp):
                if stop.is_set():
                    break
                if "error" in event:
                    raise ApiError(json.dumps(event["error"]))
                # sent once, usually in a last chunk with no choices
                usage = event.get("usage") or usage
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
    except (OSError, ValueError, http.client.HTTPException) as e:
        # a cut connection after stop is not an error
        if not stop.is_set():
            raise ApiError(str(e)) from None
    finally:
        done.set()

    msg = {"role": "assistant", "content": "".join(content)}
    # deepseek requires reasoning to be sent back during a tool-call loop
    if reasoning:
        msg["reasoning_content"] = "".join(reasoning)
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    if usage:
        msg["usage"] = usage
    return msg

