"""The live (OpenAI-compatible) backend's HTTP handling.

Driven through httpx.MockTransport rather than a real server, so these cover
the failure paths that matter - a server that rejects json_schema, a 500, a
response in an unexpected shape - without needing a model.
"""

import json

import httpx
import pytest

from app.backends import BackendError, LlamaCppBackend, VllmBackend

GOOD_BODY = {
    "choices": [{"message": {"content": '{"summary": "ok", "findings": []}'}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


@pytest.fixture
def mock_http(monkeypatch):
    """Install a handler for every httpx.Client the backend creates."""
    def _install(handler):
        real_client = httpx.Client

        def factory(*args, **kwargs):
            kwargs.pop("timeout", None)
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "Client", factory)
    return _install


def test_successful_review(mock_http, make_request):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=GOOD_BODY)

    mock_http(handler)
    parsed, usage, replayed = LlamaCppBackend(
        "http://model:8080", "m", "tok").review(make_request("+x\n"))

    assert parsed.summary == "ok"
    assert usage["total_tokens"] == 15
    assert replayed is False
    assert seen["url"] == "http://model:8080/v1/chat/completions"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["temperature"] == 0.0


def test_no_auth_header_without_a_key(mock_http, make_request):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=GOOD_BODY)

    mock_http(handler)
    LlamaCppBackend("http://model:8080", "m").review(make_request("+x\n"))
    assert seen["auth"] is None


def test_trailing_slash_in_endpoint_is_tolerated(mock_http, make_request):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=GOOD_BODY)

    mock_http(handler)
    LlamaCppBackend("http://model:8080/", "m").review(make_request("+x\n"))
    assert seen["url"] == "http://model:8080/v1/chat/completions"


def test_json_schema_rejection_retries_with_json_object(mock_http, make_request):
    """Older llama.cpp builds 400 on json_schema. Degrade, do not fail."""
    attempts = []

    def handler(request):
        body = json.loads(request.content)
        attempts.append(body["response_format"]["type"])
        if len(attempts) == 1:
            return httpx.Response(400, json={"error": "unsupported response_format"})
        return httpx.Response(200, json=GOOD_BODY)

    mock_http(handler)
    parsed, _usage, _ = LlamaCppBackend("http://m", "m").review(make_request("+x\n"))

    assert attempts == ["json_schema", "json_object"]
    assert parsed.summary == "ok"


def test_persistent_400_fails_closed(mock_http, make_request):
    mock_http(lambda request: httpx.Response(400, json={"error": "nope"}))
    with pytest.raises(BackendError):
        LlamaCppBackend("http://m", "m").review(make_request("+x\n"))


def test_server_error_fails_closed(mock_http, make_request):
    mock_http(lambda request: httpx.Response(500, text="internal error"))
    with pytest.raises(BackendError):
        LlamaCppBackend("http://m", "m").review(make_request("+x\n"))


def test_connection_error_fails_closed(mock_http, make_request):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    mock_http(handler)
    with pytest.raises(BackendError) as exc:
        LlamaCppBackend("http://m", "m").review(make_request("+x\n"))
    assert "unreachable" in str(exc.value)


def test_timeout_fails_closed(mock_http, make_request):
    def handler(request):
        raise httpx.ReadTimeout("too slow")

    mock_http(handler)
    with pytest.raises(BackendError):
        LlamaCppBackend("http://m", "m").review(make_request("+x\n"))


def test_missing_choices_is_reported_clearly(mock_http, make_request):
    mock_http(lambda request: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(BackendError) as exc:
        LlamaCppBackend("http://m", "m").review(make_request("+x\n"))
    assert "unexpected response shape" in str(exc.value)


def test_empty_choices_is_reported_clearly(mock_http, make_request):
    mock_http(lambda request: httpx.Response(200, json={"choices": []}))
    with pytest.raises(BackendError) as exc:
        LlamaCppBackend("http://m", "m").review(make_request("+x\n"))
    assert "unexpected response shape" in str(exc.value)


def test_unparseable_content_fails_closed(mock_http, make_request):
    body = {"choices": [{"message": {"content": "I cannot do that."}}]}
    mock_http(lambda request: httpx.Response(200, json=body))
    with pytest.raises(BackendError):
        LlamaCppBackend("http://m", "m").review(make_request("+x\n"))


def test_reasoning_preamble_is_handled_on_the_wire(mock_http, make_request):
    body = {"choices": [{"message": {"content":
        '<think>checking the ownership branch</think>\n'
        '```json\n{"summary": "reasoned", "findings": []}\n```'}}]}
    mock_http(lambda request: httpx.Response(200, json=body))
    parsed, _usage, _ = LlamaCppBackend("http://m", "m").review(make_request("+x\n"))
    assert parsed.summary == "reasoned"


def test_max_tokens_is_configurable(mock_http, make_request, monkeypatch):
    """Truncated reasoning is the usual cause of a missed finding on CPU."""
    monkeypatch.setenv("MODEL_MAX_TOKENS", "4096")
    seen = {}

    def handler(request):
        seen["max_tokens"] = json.loads(request.content)["max_tokens"]
        return httpx.Response(200, json=GOOD_BODY)

    mock_http(handler)
    LlamaCppBackend("http://m", "m").review(make_request("+x\n"))
    assert seen["max_tokens"] == 4096


def test_vllm_backend_shares_the_implementation(mock_http, make_request):
    mock_http(lambda request: httpx.Response(200, json=GOOD_BODY))
    backend = VllmBackend("http://vllm:8080", "m")
    parsed, _usage, replayed = backend.review(make_request("+x\n"))
    assert backend.name == "vllm"
    assert parsed.summary == "ok"
    assert replayed is False
