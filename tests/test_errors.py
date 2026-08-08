"""Tests for YNAB API error normalization and bounded retries."""

from __future__ import annotations

import json
import logging
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import pytest
from ynab.rest import ApiException

from mcp_ynab import errors


def make_api_exception(
    status: int,
    *,
    reason: str = "request failed",
    body: object = None,
    headers: dict[str, str] | None = None,
) -> ApiException:
    encoded_body = None if body is None else json.dumps(body)
    exception = ApiException(status=status, reason=reason, body=encoded_body)
    exception.headers = headers
    return exception


@pytest.mark.parametrize(
    ("status", "retryable", "guidance_fragment"),
    [
        (401, False, "YNAB_API_KEY"),
        (404, False, "id"),
        (429, True, "Retry-After"),
        (500, True, "temporarily unavailable"),
        (503, True, "temporarily unavailable"),
    ],
)
def test_normalize_maps_status_and_safe_guidance(
    status: int, retryable: bool, guidance_fragment: str
) -> None:
    error = errors.normalize_api_exception(
        make_api_exception(status, body={"error": {"id": "E", "name": "n", "detail": "d"}})
    )

    assert error.status == status
    assert error.retryable is retryable
    assert guidance_fragment.lower() in error.guidance.lower()
    assert "api key" not in error.guidance.lower() or status == 401


def test_normalize_extracts_ynab_error_fields_and_serializes() -> None:
    error = errors.normalize_api_exception(
        make_api_exception(
            422,
            reason="unprocessable",
            body={
                "error": {
                    "id": "VALIDATION_ERROR",
                    "name": "validation_error",
                    "detail": "category_id is invalid",
                }
            },
        )
    )

    assert error.error_id == "VALIDATION_ERROR"
    assert error.error_name == "validation_error"
    assert error.error_detail == "category_id is invalid"
    assert error.reason == "unprocessable"
    assert error.to_dict() == {
        "status": 422,
        "reason": "unprocessable",
        "error_id": "VALIDATION_ERROR",
        "error_name": "validation_error",
        "error_detail": "category_id is invalid",
        "retryable": False,
        "guidance": error.guidance,
    }
    assert "VALIDATION_ERROR" in str(error)
    assert "category_id is invalid" in str(error)


@pytest.mark.parametrize("body", [None, "not-json", {"unexpected": "shape"}, {"error": None}])
def test_normalize_degrades_gracefully_for_bad_bodies(body: object) -> None:
    encoded_body = body if isinstance(body, str) else body
    exception = ApiException(
        status=500,
        reason=None,
        body=encoded_body if isinstance(encoded_body, str) else json.dumps(encoded_body),
    )
    if body is None:
        exception.body = None

    error = errors.normalize_api_exception(exception)

    assert error.status == 500
    assert error.reason == ""
    assert error.error_id is None
    assert error.error_name is None
    assert error.error_detail is None


def test_is_retryable_status_only_includes_transient_read_statuses() -> None:
    assert {status for status in (429, 500, 502, 503) if errors.is_retryable_status(status)} == {
        429,
        500,
        502,
        503,
    }
    assert not errors.is_retryable_status(401)
    assert not errors.is_retryable_status(None)


def test_run_with_retry_retries_idempotent_read_then_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failures = [make_api_exception(500)]
    calls = 0
    delays: list[float] = []

    def read() -> str:
        nonlocal calls
        calls += 1
        if failures:
            raise failures.pop()
        return "ok"

    monkeypatch.setattr(errors.time, "sleep", delays.append)
    monkeypatch.setattr(errors.random, "uniform", lambda _start, _end: 0.0)

    assert errors.run_with_retry(read, idempotent=True, base_delay=0.5) == "ok"
    assert calls == 2
    assert delays == [0.5]


def test_run_with_retry_honors_retry_after_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    delays: list[float] = []

    def read() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise make_api_exception(429, headers={"Retry-After": "5"})
        return "ok"

    monkeypatch.setattr(errors.time, "sleep", delays.append)
    monkeypatch.setattr(errors.random, "uniform", lambda _start, _end: 0.0)

    assert errors.run_with_retry(read, idempotent=True, base_delay=0.01) == "ok"
    assert calls == 2
    assert delays[0] >= 5
    assert delays[0] < 6


def test_run_with_retry_honors_retry_after_http_date(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=4)
    calls = 0

    def read() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise make_api_exception(
                503,
                headers={"Retry-After": format_datetime(retry_at, usegmt=True)},
            )
        return "ok"

    monkeypatch.setattr(errors.time, "sleep", delays.append)
    monkeypatch.setattr(errors.random, "uniform", lambda _start, _end: 0.0)

    assert errors.run_with_retry(read, idempotent=True, base_delay=0.01) == "ok"
    assert 3 <= delays[0] <= 5


def test_run_with_retry_bounds_attempts_and_logs_retries(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    delays: list[float] = []

    def read() -> None:
        nonlocal calls
        calls += 1
        raise make_api_exception(500, reason="still down")

    monkeypatch.setattr(errors.time, "sleep", delays.append)
    monkeypatch.setattr(errors.random, "uniform", lambda _start, _end: 0.0)

    with caplog.at_level(logging.INFO, logger="mcp_ynab.errors"):
        with pytest.raises(errors.YNABAPIError) as raised:
            errors.run_with_retry(read, idempotent=True, max_attempts=3, base_delay=0.25)

    assert calls == 3
    assert raised.value.status == 500
    assert len(delays) == 2
    assert "retry" in caplog.text.lower()
    assert "attempt 2/3" in caplog.text
    assert "attempt 3/3" in caplog.text


@pytest.mark.parametrize("status", [429, 500])
def test_run_with_retry_never_retries_non_idempotent_write(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def write() -> None:
        nonlocal calls
        calls += 1
        raise make_api_exception(status)

    with pytest.raises(errors.YNABAPIError) as raised:
        errors.run_with_retry(write, idempotent=False, max_attempts=3)

    assert calls == 1
    assert raised.value.status == status
    assert "retry" not in raised.value.guidance.lower() or "do not automatically retry writes" in (
        raised.value.guidance.lower()
    )


def test_run_with_retry_propagates_non_api_exception_unchanged() -> None:
    original = RuntimeError("local failure")

    def read() -> None:
        raise original

    with pytest.raises(RuntimeError) as raised:
        errors.run_with_retry(read, idempotent=True)

    assert raised.value is original


def test_normalized_guidance_never_echoes_api_key() -> None:
    error = errors.normalize_api_exception(
        make_api_exception(401, reason="secret-token-should-not-leak")
    )

    assert "secret-token-should-not-leak" not in str(error)
    assert "YNAB_API_KEY" in error.guidance
