"""llm: request shape, sse merging, retries, errors and stopping, against a real local http server."""

import io
import json
import urllib.error
from threading import Event

import pytest

from fakes import FakeServer, assistant, call
from modules import llm
from modules.config import Model


def complete(model: Model, messages: list[dict] | None = None, thinking: str = "off",
             stop: Event | None = None, tools: list[dict] | None = None) -> tuple[dict, list[str], list[str]]:
    """Call llm.complete, returning the message and the streamed text and reasoning pieces."""
    texts, reasoning = [], []
    msg = llm.complete(model, messages or [{"role": "user", "content": "hi"}], tools or [], thinking,
                       texts.append, reasoning.append, stop or Event())
    return msg, texts, reasoning


def sse(*lines: str) -> io.BytesIO:
    """Fake response body made of raw lines."""
    return io.BytesIO("".join(line + "\n" for line in lines).encode())


def data(*events: dict) -> io.BytesIO:
    """Fake response body with one data line per event and [DONE]."""
    return sse(*(f"data: {json.dumps(e)}\n" for e in events), "data: [DONE]")


def served(server: FakeServer, **fields) -> Model:
    """A model pointing at the fake server."""
    return Model(**{"provider": "p", "id": "m", "base_url": server.url, "api_key": "", "efforts": {}, **fields})


def test_request_shape(server: FakeServer) -> None:
    """Body, url and headers follow the openai api; no auth header without a key."""
    msgs = [{"role": "user", "content": "hi"}]
    specs = [{"type": "function", "function": {"name": "t"}}]
    complete(served(server), msgs, tools=specs)
    req = server.requests[0]
    assert req["path"] == "/v1/chat/completions"
    assert req["model"] == "m"
    assert req["messages"] == msgs
    assert req["tools"] == specs
    assert req["stream"] is True
    assert req["stream_options"] == {"include_usage": True}
    assert "reasoning_effort" not in req
    assert "Authorization" not in server.headers[0]
    assert server.headers[0]["Content-Type"] == "application/json"


def test_api_key_and_trailing_slash(server: FakeServer) -> None:
    """A key becomes a bearer token; a trailing slash in baseUrl is not doubled."""
    complete(served(server, base_url=server.url + "/", api_key="sk"))
    assert server.headers[0]["Authorization"] == "Bearer sk"
    assert server.requests[0]["path"] == "/v1/chat/completions"


def test_reasoning_effort_mapped(server: FakeServer) -> None:
    """The thinking level is sent as its mapped reasoning_effort; off sends nothing."""
    model = served(server, efforts={"high": "max"})
    complete(model, thinking="high")
    complete(model, thinking="off")
    assert server.requests[0]["reasoning_effort"] == "max"
    assert "reasoning_effort" not in server.requests[1]


def test_text_reasoning_tools_usage(server: FakeServer) -> None:
    """Content, reasoning, tool calls and usage are merged from the stream."""
    usage = {"prompt_tokens": 10, "completion_tokens": 2}
    calls = [call("a", "read", path="x"), call("b", "bash", command="ls")]
    server.replies.append(assistant("hello world", calls, usage, reasoning="thinking"))
    msg, texts, reasoning = complete(served(server))
    assert msg == {"role": "assistant", "content": "hello world", "reasoning_content": "thinking",
                   "tool_calls": calls, "usage": usage}
    assert "".join(texts) == "hello world" and len(texts) == 2
    assert reasoning == ["thinking"]


def test_plain_message_has_no_extra_keys(server: FakeServer) -> None:
    """Without reasoning, tools or usage only role and content are returned."""
    server.replies.append(assistant("hi"))
    assert complete(served(server))[0] == {"role": "assistant", "content": "hi"}


def test_interleaved_tool_call_deltas(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """Two tool calls split across interleaved chunks merge into two whole calls, ordered by index."""
    def tc(index: int, **fields) -> dict:
        return {"choices": [{"delta": {"tool_calls": [{"index": index, **fields}]}}]}

    body = data(
        tc(1, id="b", function={"name": "bash", "arguments": ""}),
        tc(0, id="a", function={"name": "re", "arguments": '{"pa'}),
        tc(0, function={"name": "ad", "arguments": 'th": "x"}'}),
        tc(1, function={"arguments": '{"command": "ls"}'}),
    )
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout: body)
    calls = complete(model)[0]["tool_calls"]
    assert [(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"])) for c in calls] == [
        ("a", "read", {"path": "x"}), ("b", "bash", {"command": "ls"})]
    assert all(c["type"] == "function" for c in calls)


def test_openrouter_reasoning_field(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """openrouter's "reasoning" delta is echoed back as reasoning_content."""
    body = data({"choices": [{"delta": {"reasoning": "a"}}]}, {"choices": [{"delta": {"reasoning": "b"}}]})
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout: body)
    msg, _, reasoning = complete(model)
    assert msg["reasoning_content"] == "ab" and reasoning == ["a", "b"]


def test_ignores_non_data_lines_and_empty_parts(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """Comments, blank lines, null choices and deltas are skipped; data after [DONE] is ignored."""
    body = sse(
        ": keep-alive", "", "event: message",
        'data: {"choices": null}',
        'data: {"choices": [{"delta": null}]}',
        'data: {"choices": [{"delta": {"content": null, "tool_calls": null}}]}',
        'data:{"choices": [{"delta": {"content": "x"}}]}',
        "data: [DONE]",
        'data: {"choices": [{"delta": {"content": "after"}}]}',
    )
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout: body)
    assert complete(model)[0] == {"role": "assistant", "content": "x"}


def test_usage_kept_from_last_chunk_that_has_it(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """A later chunk with null usage does not erase it."""
    body = data({"choices": [], "usage": {"prompt_tokens": 1}}, {"choices": [], "usage": None})
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout: body)
    assert complete(model)[0]["usage"] == {"prompt_tokens": 1}


def test_stream_error_event(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """An error event mid-stream raises ApiError with its json."""
    body = data({"choices": [{"delta": {"content": "x"}}]}, {"error": {"message": "boom"}})
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout: body)
    with pytest.raises(llm.ApiError, match="boom"):
        complete(model)


def test_bad_json_in_stream(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """Broken json in the stream is an ApiError, not a crash."""
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout: sse("data: {nope"))
    with pytest.raises(llm.ApiError):
        complete(model)


def test_connection_drop_mid_stream(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """An OSError while reading is an ApiError."""
    class Broken(io.BytesIO):
        def __iter__(self):
            yield b'data: {"choices": [{"delta": {"content": "x"}}]}\n'
            raise ConnectionResetError("reset")

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout: Broken())
    with pytest.raises(llm.ApiError, match="reset"):
        complete(model)


def test_stop_during_stream(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """Setting stop ends the stream at the next event, keeping what came before."""
    stop = Event()

    def on_text(text: str) -> None:
        stop.set()

    body = data(*({"choices": [{"delta": {"content": c}}]} for c in "abc"))
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout: body)
    msg = llm.complete(model, [], [], "off", on_text, lambda t: None, stop)
    assert msg["content"] == "a"


def test_http_error_body_in_message(server: FakeServer) -> None:
    """A 400 raises at once with status and body."""
    server.replies.append((400, '{"error": "bad model"}'))
    with pytest.raises(llm.ApiError, match='400 .*bad model'):
        complete(served(server))
    assert len(server.requests) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retries_then_succeeds(server: FakeServer, status: int) -> None:
    """Rate limits and server errors are retried."""
    server.replies += [(status, "busy"), (status, "busy"), assistant("hi")]
    assert complete(served(server))[0]["content"] == "hi"
    assert len(server.requests) == 3


def test_retries_give_up(server: FakeServer) -> None:
    """After every delay is used up the last error is raised."""
    server.replies += [(503, "down")] * 10
    with pytest.raises(llm.ApiError, match="503"):
        complete(served(server))
    assert len(server.requests) == len(llm.RETRY_DELAYS)


def test_no_retry_on_client_error(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """4xx other than 429 is not retried."""
    codes = [404, 404]

    def urlopen(req, timeout):
        raise urllib.error.HTTPError("u", codes.pop(0), "nf", {}, io.BytesIO(b"x"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    with pytest.raises(llm.ApiError, match="404 nf: x"):
        complete(model)
    assert codes == [404]


def test_stop_during_retry_wait(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    """Stop while waiting to retry returns an empty message instead of raising."""
    monkeypatch.setattr(llm, "RETRY_DELAYS", [30, None])
    stop = Event()

    def urlopen(req, timeout):
        stop.set()
        raise urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b""))

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    assert llm.complete(model, [], [], "off", print, print, stop) == {"role": "assistant", "content": ""}


def test_unreachable_server(model: Model) -> None:
    """A refused connection is an ApiError naming the url."""
    with pytest.raises(llm.ApiError, match="127.0.0.1:9/v1/chat/completions"):
        complete(model)
