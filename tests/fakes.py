"""Test doubles: an in-process OpenAI-compatible server and a scripted llm.complete."""

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def call(id: str, name: str, **args) -> dict:
    """One assistant tool call."""
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def assistant(content: str = "", calls: list[dict] | None = None, usage: dict | None = None,
              reasoning: str = "") -> dict:
    """An assistant message as llm.complete returns it."""
    msg: dict = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if calls:
        msg["tool_calls"] = calls
    if usage:
        msg["usage"] = usage
    return msg


def chunks(msg: dict) -> list[dict]:
    """Split an assistant message into streamed chunks, like a real server sends them."""
    events = []
    if text := msg.get("reasoning_content"):
        events.append({"choices": [{"delta": {"reasoning_content": text}}]})
    if text := msg.get("content"):
        # two halves so the client really has to join deltas
        half = len(text) // 2
        events += [{"choices": [{"delta": {"content": part}}]} for part in (text[:half], text[half:]) if part]
    for i, c in enumerate(msg.get("tool_calls", [])):
        args = c["function"]["arguments"]
        events.append({"choices": [{"delta": {"tool_calls": [
            {"index": i, "id": c["id"], "type": "function", "function": {"name": c["function"]["name"], "arguments": args[:3]}}]}}]})
        events.append({"choices": [{"delta": {"tool_calls": [{"index": i, "function": {"arguments": args[3:]}}]}}]})
    if usage := msg.get("usage"):
        events.append({"choices": [], "usage": usage})
    return events


class FakeLLM:
    """Stands in for llm.complete: returns queued replies and records every request."""

    def __init__(self) -> None:
        # each reply is a message dict, an exception to raise, or a callable(stop) returning one
        self.replies: list = []
        self.requests: list[dict] = []

    def __call__(self, model, messages: list[dict], tools: list[dict], thinking: str,
                 on_text: Callable[[str], None], on_reasoning: Callable[[str], None], stop: threading.Event,
                 on_retry: Callable[[float, str], None] | None = None) -> dict:
        # deep copy, since the agent keeps appending to the same list
        self.requests.append({"model": model, "messages": json.loads(json.dumps(messages)),
                              "tools": tools, "thinking": thinking})
        reply = self.replies.pop(0) if self.replies else assistant("ok")
        if callable(reply):
            reply = reply(stop)
        if isinstance(reply, BaseException):
            raise reply
        if reply.get("reasoning_content"):
            on_reasoning(reply["reasoning_content"])
        if reply.get("content"):
            on_text(reply["content"])
        return json.loads(json.dumps(reply))


class FakeServer:
    """OpenAI-compatible /chat/completions on localhost; replies come from a queue, requests are recorded."""

    def __init__(self) -> None:
        # each reply is an assistant message dict (streamed as sse), or (status, body) for an http error
        self.replies: list = []
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append({"path": self.path, **body})
                server.headers.append(dict(self.headers))
                reply = server.replies.pop(0) if server.replies else assistant("ok")
                if isinstance(reply, tuple):
                    status, text = reply
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(text.encode())
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in chunks(reply):
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *args) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        """Base url to put in settings.json."""
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def __enter__(self) -> "FakeServer":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
