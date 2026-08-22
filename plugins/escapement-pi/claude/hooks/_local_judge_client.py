"""Shared client for local OpenAI-compatible judge calls.

The hooks use local models as tiny classifiers. This module owns the contract:
OpenAI-compatible `/v1/chat/completions`, defaulting to the Rapid-MLX/local-llm
convention on localhost, with environment overrides for machines that run a
different harness.
"""

from __future__ import annotations

import os
import json
import stat
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from typing import Callable, Iterable

DEFAULT_BASE_URL = "http://localhost:8000/v1"
DEFAULT_MODEL = "default"
DEFAULT_TIMEOUT = 60.0

BASE_URL_ENV = "ESCAPEMENT_LOCAL_JUDGE_BASE_URL"
MODEL_ENV = "ESCAPEMENT_LOCAL_JUDGE_MODEL"
TIMEOUT_ENV = "ESCAPEMENT_LOCAL_JUDGE_TIMEOUT"
API_KEY_ENV = "ESCAPEMENT_LOCAL_JUDGE_API_KEY"
API_KEY_FILE_ENV = "ESCAPEMENT_LOCAL_JUDGE_API_KEY_FILE"


def configured_base_url() -> str:
    return (os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")


def configured_model() -> str:
    return os.environ.get(MODEL_ENV) or DEFAULT_MODEL


def configured_timeout() -> float:
    raw = os.environ.get(TIMEOUT_ENV)
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    return timeout if timeout > 0 else DEFAULT_TIMEOUT


def chat_completions_url(base_url: str | None = None) -> str:
    return f"{(base_url or configured_base_url()).rstrip('/')}/chat/completions"


def configured_auth_header() -> str | None:
    """Return configured Bearer authentication, or fail closed to no header."""
    environment_key = os.environ.get(API_KEY_ENV)
    if environment_key:
        return f"Bearer {environment_key}"

    key_path = os.environ.get(API_KEY_FILE_ENV)
    if not key_path:
        return None

    descriptor = None
    try:
        before = os.lstat(key_path)
        if not stat.S_ISREG(before.st_mode):
            return None
        if stat.S_IMODE(before.st_mode) != 0o600 or before.st_uid != os.getuid():
            return None

        flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(key_path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_uid != os.getuid()
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            return None
        raw = os.read(descriptor, 65_537)
        if len(raw) > 65_536:
            return None
        value = raw.decode("utf-8")
    except (OSError, UnicodeError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)

    lines = value.splitlines()
    if len(lines) != 1 or not lines[0]:
        return None
    return f"Bearer {lines[0]}"


def _default_post(url: str, payload: dict, timeout: float) -> str:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    authorization = configured_auth_header()
    if authorization is not None:
        headers["Authorization"] = authorization
    request = Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"judge server returned HTTP {exc.code}") from exc
    return json.loads(raw)["choices"][0]["message"]["content"]


def _label_present(content: str, labels: Iterable[str]) -> bool:
    low = content.strip().lower()
    return any(
        label.lower() in low or label.lower().replace("_", " ") in low
        for label in labels
    )


def boolean_verdict(
    text: str,
    *,
    system_prompt: str,
    positive_labels: tuple[str, ...],
    negative_labels: tuple[str, ...],
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    max_tokens: int = 32,
    post: Callable[[str, dict, float], str] | None = None,
) -> bool | None:
    """Return True/False from a label-only local judge, or None on any uncertainty."""
    if not text or not isinstance(text, str):
        return None
    payload = {
        "model": model or configured_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "max_tokens": max_tokens,
        "enable_thinking": False,
        # Greedy decoding. These judges are label-only classifiers, not generators —
        # sampling makes the SAME message flip verdict across calls, so a Stop gate
        # built on them becomes a coin flip (observed: identical input → winddown one
        # call, not_winddown the next). temperature 0 makes the classifier reproducible,
        # which is a precondition for the gate being testable and for the user seeing
        # consistent behavior. (The server may still carry mild non-determinism; the
        # prompt is what carries correctness — this only removes the sampling noise.)
        "temperature": 0,
    }
    call = post or _default_post
    try:
        content = call(
            chat_completions_url(base_url),
            payload,
            configured_timeout() if timeout is None else timeout,
        )
    except Exception:
        return None

    content = content or ""
    # Negative labels often contain the positive label as a substring
    # (`not_stop_solicitation`), so they must win.
    if _label_present(content, negative_labels):
        return False
    if _label_present(content, positive_labels):
        return True
    return None


def health_check(
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    post: Callable[[str, dict, float], str] | None = None,
) -> dict:
    """Probe the configured judge endpoint without raising."""
    verdict = boolean_verdict(
        "ready",
        system_prompt="Classify. Answer only ready or broken.",
        positive_labels=("ready",),
        negative_labels=("broken",),
        base_url=base_url,
        model=model,
        timeout=timeout,
        max_tokens=8,
        post=post,
    )
    return {
        "ok": verdict is True,
        "base_url": (base_url or configured_base_url()).rstrip("/"),
        "model": model or configured_model(),
        "reason": "ok" if verdict is True else "unavailable",
    }
