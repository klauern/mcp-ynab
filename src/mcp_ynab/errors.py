"""Error normalization and bounded retries for YNAB SDK calls.

The public helpers in this module keep SDK transport failures consistent at the
MCP boundary. Retries are deliberately bounded, apply only to idempotent reads,
and emit an INFO log entry for every retry attempt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import math
import random
from typing import Any, Callable, Optional

from ynab.rest import ApiException

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503})
# A Retry-After interval larger than this is treated as this cap.  Honoring an
# unbounded or absurd interval would let a misbehaving server stall a read
# indefinitely; the cap still respects the server's intent for every practical
# rate-limit window while keeping retries bounded.
_MAX_RETRY_AFTER_SECONDS = 300.0


class YNABAPIError(Exception):
    """A safe, structured representation of a YNAB API failure.

    ``retryable`` identifies statuses that may be retried for an idempotent
    read. It does not authorize retrying a write; callers must pass
    ``idempotent=True`` explicitly to :func:`run_with_retry`.
    """

    def __init__(
        self,
        *,
        status: Optional[int],
        reason: str,
        error_id: Optional[str],
        error_name: Optional[str],
        error_detail: Optional[str],
        retryable: bool,
        guidance: str,
    ) -> None:
        self.status = status
        self.reason = reason
        self.error_id = error_id
        self.error_name = error_name
        self.error_detail = error_detail
        self.retryable = retryable
        self.guidance = guidance
        super().__init__(self.__str__())

    def to_dict(self) -> dict[str, Any]:
        """Return the stable machine-readable MCP error representation."""
        return {
            "status": self.status,
            "reason": self.reason,
            "error_id": self.error_id,
            "error_name": self.error_name,
            "error_detail": self.error_detail,
            "retryable": self.retryable,
            "guidance": self.guidance,
        }

    def __str__(self) -> str:
        """Return useful context without including the raw response body."""
        status = f"HTTP {self.status}" if self.status is not None else "HTTP status unknown"
        identity_parts = [part for part in (self.error_name, self.error_id) if part]
        identity = "/".join(identity_parts) if identity_parts else "YNAB API failure"
        detail = f": {self.error_detail}" if self.error_detail else ""
        return f"{status} {identity}{detail}. {self.guidance}"


def is_retryable_status(status: Optional[int]) -> bool:
    """Return whether ``status`` is eligible for an idempotent-read retry."""
    return status in _RETRYABLE_STATUSES


def normalize_api_exception(exc: ApiException) -> YNABAPIError:
    """Convert an SDK ``ApiException`` into a safe structured error.

    YNAB responses are parsed defensively because transport failures can omit a
    body, return non-JSON content, or contain a shape other than ``error``.
    This function intentionally never propagates parsing or model-shape errors.
    """
    try:
        status = _safe_status(getattr(exc, "status", None))
        reason = _safe_string(getattr(exc, "reason", "")) or ""
        error_id, error_name, error_detail = _extract_error_fields(exc)
        guidance = _guidance_for_status(status)
        return YNABAPIError(
            status=status,
            reason=reason,
            error_id=error_id,
            error_name=error_name,
            error_detail=error_detail,
            retryable=is_retryable_status(status),
            guidance=guidance,
        )
    except Exception:  # pragma: no cover - final safety net for hostile SDK objects
        return YNABAPIError(
            status=None,
            reason="",
            error_id=None,
            error_name=None,
            error_detail=None,
            retryable=False,
            guidance=_guidance_for_status(None),
        )


async def run_with_retry(
    fn: Callable[[], Any],
    *,
    idempotent: bool,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> Any:
    """Run ``fn`` with bounded backoff for selected idempotent-read failures.

    ``max_attempts`` includes the initial call. Only statuses 429, 500, 502,
    and 503 are retried, and only when ``idempotent`` is true. A ``Retry-After``
    seconds value or HTTP-date takes precedence over exponential backoff. Every
    retry is logged by ``mcp_ynab.errors`` with its attempt number and delay.
    Non-API exceptions propagate unchanged; exhausted API failures become a
    :class:`YNABAPIError`.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if base_delay < 0:
        raise ValueError("base_delay must not be negative")

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except ApiException as exc:
            if not idempotent or not is_retryable_status(
                _safe_status(getattr(exc, "status", None))
            ):
                raise normalize_api_exception(exc) from exc
            if attempt >= max_attempts:
                raise normalize_api_exception(exc) from exc

            exponential_delay = base_delay * (2 ** (attempt - 1))
            jitter = random.uniform(0.0, min(0.1, max(base_delay, 0.01)))
            retry_after = _retry_after_seconds(exc)
            delay = max(exponential_delay + jitter, retry_after or 0.0)
            next_attempt = attempt + 1
            logger.info(
                "Retrying idempotent YNAB read (attempt %d/%d) after %.3f seconds",
                next_attempt,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)

    # The loop either returns or raises from the final attempt.
    raise RuntimeError("retry loop terminated unexpectedly")  # pragma: no cover


def _extract_error_fields(exc: ApiException) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract YNAB error fields from a response body or parsed SDK data."""
    try:
        payload = _decode_payload(getattr(exc, "body", None))
        if payload is None:
            payload = _decode_payload(getattr(exc, "data", None))
        if not isinstance(payload, Mapping):
            return None, None, None
        error = payload.get("error")
        if not isinstance(error, Mapping):
            return None, None, None
        return (
            _optional_string(error.get("id")),
            _optional_string(error.get("name")),
            _optional_string(error.get("detail")),
        )
    except Exception:
        return None, None, None


def _decode_payload(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
        except Exception:
            return None
        return result if isinstance(result, Mapping) else None
    return None


def _safe_status(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _safe_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _optional_string(value: Any) -> Optional[str]:
    result = _safe_string(value)
    return result if result else None


def _guidance_for_status(status: Optional[int]) -> str:
    if status == 401:
        return "Authentication failed; check that YNAB_API_KEY is configured and valid."
    if status == 403:
        return "YNAB denied access; verify the token has permission for this budget."
    if status == 404:
        return "Not found; verify the budget, resource, or other identifier is correct."
    if status in {409, 422}:
        return "Request was rejected; verify the identifiers and request body values."
    if status == 429:
        return (
            "YNAB rate limit reached; wait for the Retry-After interval before attempting "
            "a safe read again, and do not automatically retry writes."
        )
    if status in {500, 502, 503}:
        return (
            "YNAB is temporarily unavailable; retry idempotent reads after a short delay, "
            "and do not automatically retry writes."
        )
    return "Review the YNAB request and response details, then try a safe corrective action."


def _retry_after_seconds(exc: ApiException) -> Optional[float]:
    """Return a finite, capped Retry-After delay from seconds or an HTTP-date.

    Values that are missing, negative, non-finite (``inf``/``NaN``), or
    unparseable yield ``None`` so the caller falls back to exponential backoff.
    Finite values are capped at :data:`_MAX_RETRY_AFTER_SECONDS` so a hostile
    or buggy ``Retry-After`` header can never stall a read indefinitely.
    """
    try:
        headers = getattr(exc, "headers", None)
        if not headers:
            return None
        value: Any = None
        for key, candidate in headers.items():
            if str(key).lower() == "retry-after":
                value = candidate
                break
        if value is None:
            return None
        try:
            seconds = float(str(value).strip())
        except (TypeError, ValueError):
            seconds = None
        if seconds is not None and math.isfinite(seconds) and seconds >= 0:
            return min(seconds, _MAX_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError, AttributeError):
        pass

    try:
        parsed = parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = (parsed - datetime.now(timezone.utc)).total_seconds()
        if not math.isfinite(delta):
            return None
        return min(max(0.0, delta), _MAX_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError, OverflowError):
        return None
