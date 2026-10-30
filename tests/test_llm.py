"""The OpenAI-compatible client: parsing, refusals, and which HTTP failures it retries."""

import io
import json
import urllib.error

import pytest

from rea_arena.harness import llm
from rea_arena.harness.llm import OpenAIChat


class FakeTransport:
    """Stands in for urlopen: each call pops the next outcome (a response dict or an HTTP code)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, req, **_options):
        self.requests.append(json.loads(req.data))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, int):
            raise urllib.error.HTTPError(req.full_url, outcome, "error", {"Retry-After": "7"},
                                         io.BytesIO(b"upstream says no"))
        return io.BytesIO(json.dumps(outcome).encode())


def reply(content, finish_reason="stop", **message):
    return {"choices": [{"message": {"content": content, **message}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5, "cost": 0.1}}


@pytest.fixture
def transport(monkeypatch):
    def install(*outcomes):
        fake = FakeTransport(outcomes)
        monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
        sleeps = []
        monkeypatch.setattr(llm.time, "sleep", sleeps.append)
        return fake, sleeps
    return install


def test_parses_content_and_integer_usage(transport):
    fake, _ = transport(reply("hello"))
    response = OpenAIChat("http://x/v1", "m", temperature=0.5, max_tokens=9).complete(
        [{"role": "user", "content": "hi"}])
    assert (response.content, response.usage, response.truncated) == (
        "hello", {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}, False)
    assert fake.requests[0] == {"model": "m", "messages": [{"role": "user", "content": "hi"}],
                                "temperature": 0.5, "max_tokens": 9}


def test_content_filter_without_content_is_a_refusal(transport):
    transport(reply(None, finish_reason="content_filter"))
    response = OpenAIChat("http://x/v1", "m").complete([])
    assert (response.content, response.refusal) == ("", "blocked by the provider's content filter")


def test_truncation_is_reported(transport):
    transport(reply("", finish_reason="length"))
    assert OpenAIChat("http://x/v1", "m").complete([]).truncated


def test_throttle_is_retried_after_the_requested_wait(transport):
    fake, sleeps = transport(429, reply("ok"))
    assert OpenAIChat("http://x/v1", "m", backoff=1.0).complete([]).content == "ok"
    assert len(fake.requests) == 2
    assert len(sleeps) == 1
    assert 7.0 <= sleeps[0] <= 8.0  # Retry-After, plus at most `backoff` of jitter


def test_client_error_is_not_retried(transport):
    fake, sleeps = transport(400)
    with pytest.raises(RuntimeError, match="HTTP 400 .*upstream says no"):
        OpenAIChat("http://x/v1", "m").complete([])
    assert len(fake.requests) == 1
    assert not sleeps


def test_gives_up_after_the_retry_budget(transport):
    fake, _ = transport(503, 503, 503)
    with pytest.raises(RuntimeError, match="failed after 3 attempts: HTTP 503"):
        OpenAIChat("http://x/v1", "m", retries=2).complete([])
    assert len(fake.requests) == 3
