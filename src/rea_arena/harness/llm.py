"""A tiny OpenAI-compatible chat client, standard library only.

The gym has no runtime dependencies and the harness keeps it that way: this talks to any
OpenAI-compatible ``/chat/completions`` endpoint (vLLM, TGI, SGLang, a hosted API) over
``urllib`` rather than pulling in the ``openai`` package. ``Chat`` is the small protocol the
agent loop depends on, so tests can supply a scripted model with no network.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

Message = dict[str, str]


@dataclass
class ChatResponse:
    content: str
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    refusal: str | None = None  # set when the provider blocked the reply (e.g. a safety filter)
    finish_reason: str | None = None  # "stop", "length" (truncated), "content_filter", ...

    @property
    def truncated(self) -> bool:
        """True when the model hit the token cap mid-reply. For a reasoning model the chain of
        thought shares the completion budget, so a short cap can truncate before any ``content``
        is emitted; the fix is more tokens, not a nudge."""
        return self.finish_reason == "length"


class Chat(Protocol):
    """What the agent loop needs from a model: a name, and one blocking completion call."""

    model: str

    def complete(self, messages: list[Message]) -> ChatResponse: ...


@dataclass
class OpenAIChat:
    """Chat completions over an OpenAI-compatible HTTP endpoint.

    ``base_url`` includes the ``/v1`` prefix, e.g. ``http://1.2.3.4:8000/v1``. ``api_key`` is
    sent as a bearer token; many self-hosted servers accept any value (``"EMPTY"``).
    """

    base_url: str
    model: str
    api_key: str = "EMPTY"
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout: float = 180.0
    retries: int = 6
    backoff: float = 2.0
    max_backoff: float = 30.0

    def complete(self, messages: list[Message]) -> ChatResponse:
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }).encode()
        last_error: BaseException | None = None
        for attempt in range(self.retries + 1):
            retry_after = 0.0
            try:
                return _parse(self._post(body))
            except _Retryable as e:
                last_error, retry_after = e.__cause__, e.retry_after
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                last_error = e
            if attempt < self.retries:
                wait = min(self.backoff * (2 ** attempt), self.max_backoff)
                # jitter decorrelates retries so concurrent callers do not resynchronise on a throttle
                time.sleep(max(wait, retry_after) + random.uniform(0, self.backoff))
        raise RuntimeError(f"chat completion failed after {self.retries + 1} attempts: {last_error}")

    def _post(self, body: bytes) -> dict:
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            error = RuntimeError(f"HTTP {e.code} from {url}: {detail}")
            if e.code not in (408, 409, 429) and e.code < 500:
                raise error from e  # a 4xx that will not get better on retry
            raise _Retryable(_retry_after(e)) from error


class _Retryable(Exception):
    """A throttle or server error worth retrying, after at least ``retry_after`` seconds."""

    def __init__(self, retry_after: float):
        super().__init__(retry_after)
        self.retry_after = retry_after


def _retry_after(e: urllib.error.HTTPError) -> float:
    """The wait a throttle (429/503) asks for, in seconds; 0 when it does not say."""
    try:
        return float(e.headers.get("Retry-After", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse(data: dict) -> ChatResponse:
    choice = data["choices"][0]
    content = choice["message"].get("content") or ""
    usage = {k: int(v) for k, v in (data.get("usage") or {}).items() if isinstance(v, int)}
    finish_reason = choice.get("finish_reason")
    refusal = choice["message"].get("refusal") or (
        "blocked by the provider's content filter" if finish_reason == "content_filter" and not content else None)
    return ChatResponse(content, usage, data, refusal, finish_reason)
