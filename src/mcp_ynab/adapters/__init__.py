"""Canonical one-to-one ``api_<operation_id>`` YNAB adapters.

The adapter contract deliberately keeps the SDK boundary in one place:
constructing the client and API class, validating write bodies, normalizing
transport errors, retrying reads, and converting SDK models to JSON data.
The API and model names are looked up on :mod:`mcp_ynab.server` at call time
so tests and embedding applications can replace them safely.

Transaction delta records use the same resolved config directory as
:class:`mcp_ynab.state.YNABResources`.  The store currently keeps that path in
its private ``_config_dir`` attribute; the public fallback is
``server._resolve_config_dir()``.  The transaction records therefore live in
``transactions_delta.json`` beside ``knowledge.json``.
"""

from __future__ import annotations

import inspect
from datetime import date
from functools import wraps
from pathlib import Path
from typing import (
    Any,
    Annotated,
    Awaitable,
    Callable,
    Dict,
    Mapping,
    Optional,
    TypeVar,
    get_type_hints,
)

import mcp.types as types
from mcp.server.fastmcp import Context
from pydantic import Field
from pydantic_core import to_jsonable_python

from .. import server as _s
from .. import errors as _errors
from ..state import _load_json_file, merge_delta_into_records

# Body keys that select an entity rather than updating it (SaveTransaction
# path/import selectors).  Accepted for SDK shape fidelity but never counted
# as an update field.
_SELECTOR_FIELDS = frozenset({"id", "import_id"})

# Resolve annotation constants through the server import cycle once.  Explicit
# annotations keep mypy from needing the partially-initialized server module
# when this package is imported from server.py's own body (import cycle: mypy
# cannot determine the RHS type until server.py finishes analyzing).
_READ_ONLY: types.ToolAnnotations = _s.READ_ONLY_TOOL  # type: ignore[has-type]
_MUTATING: types.ToolAnnotations = _s.MUTATING_TOOL  # type: ignore[has-type]


READ_MAX_ATTEMPTS = 3
READ_BASE_DELAY = 0.5
TRANSACTIONS_RESOURCE = "transactions"
TRANSACTIONS_CACHE_FILENAME = "transactions_delta.json"

Body = Callable[..., Awaitable[dict[str, Any]]]
BodyT = TypeVar("BodyT", bound=Body)


def _cache_file() -> Path:
    """Resolve the transaction delta cache path from the shared resource store."""
    resources = _s.ynab_resources
    config_dir = getattr(resources, "config_dir", None)
    if config_dir is None:
        config_dir = getattr(resources, "_config_dir", None)
    if config_dir is None:
        config_dir = _s._resolve_config_dir()
    return Path(config_dir) / TRANSACTIONS_CACHE_FILENAME


def _cached_transaction_records(cache_file: Path, plan_id: str) -> list[dict[str, Any]]:
    """Read the bare cached transaction list for ``plan_id`` defensively."""
    cached = _load_json_file(cache_file)
    records = cached.get(plan_id, []) if isinstance(cached, dict) else []
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _has_cached_transaction_records(cache_file: Path, plan_id: str) -> bool:
    """Return whether a usable cache baseline exists for ``plan_id``.

    A missing file, a corrupt file, a missing ``plan_id`` key, or a list
    containing malformed (non-dict) records means there is no baseline to merge
    a delta against — a delta-only response would silently discard untouched
    cached records, so callers must fall back to a full fetch.
    """
    if not cache_file.exists():
        return False
    cached = _load_json_file(cache_file)
    records = cached.get(plan_id) if isinstance(cached, dict) else None
    if not isinstance(records, list) or not records:
        return False
    return all(isinstance(record, dict) for record in records)


def _persist_transaction_delta(
    plan_id: str, response_data: dict[str, Any], knowledge: Optional[int]
) -> None:
    """Commit fetched transaction records and knowledge after a successful read."""
    records = response_data.get("transactions")
    new_knowledge = response_data.get("server_knowledge")
    if not isinstance(records, list) or not isinstance(new_knowledge, int):
        raise TypeError("YNAB transactions response must include transactions and server_knowledge")

    cache_file = _cache_file()
    if knowledge is None:
        merged_records = [record for record in records if isinstance(record, dict)]
    else:
        existing = _cached_transaction_records(cache_file, plan_id)
        merged_records = merge_delta_into_records(existing, records)

    _s.ynab_resources.commit_knowledge_and_records(
        plan_id,
        TRANSACTIONS_RESOURCE,
        merged_records,
        new_knowledge,
        cache_file=cache_file,
    )


def _serialize_response(response: Any) -> dict[str, Any]:
    """Convert an SDK response into JSON-compatible response-data fields.

    YNAB response envelopes contain a single ``data`` object.  Canonical
    adapters expose that object's complete fields directly, so list responses
    contain top-level ``transactions`` and ``server_knowledge`` while no data
    is discarded.  SDK ``to_dict`` methods may still contain dates, UUIDs, and
    enums; ``to_jsonable_python`` handles those values without changing the
    response shape.
    """
    to_dict = getattr(response, "to_dict", None)
    if callable(to_dict):
        serialized = to_dict()
    elif isinstance(response, dict):
        serialized = dict(response)
    else:
        raise TypeError(
            "YNAB SDK response must provide to_dict() or already be a dict; "
            f"got {type(response).__name__}"
        )

    if not isinstance(serialized, dict):
        raise TypeError(
            f"YNAB SDK response serialization must produce a dict; got {type(serialized).__name__}"
        )
    if set(serialized) == {"data"} and isinstance(serialized["data"], dict):
        serialized = serialized["data"]
    result = to_jsonable_python(serialized)
    if not isinstance(result, dict):  # pragma: no cover - guarded by the input check
        raise TypeError("YNAB SDK response serialization must produce a dict")
    return result


def _construct_body_wrapper(body_wrapper: str, body: dict[str, Any]) -> Any:
    """Validate a body and adapt flat SDK save fields to a put wrapper.

    SDK 4.3's ``PutTransactionWrapper`` has the shape
    ``{"transaction": ExistingTransaction(...)}`` while the useful public
    adapter body is the flat ``SaveTransactionWithIdOrImportId`` field shape.
    Nested ``{"transaction": {...}}`` bodies remain accepted for fidelity to
    the SDK.  ``id``/``import_id`` are accepted as path/import selectors but
    are not update fields.  Unknown keys are rejected (Pydantic would
    otherwise silently ignore them) and a body with nothing to update is
    rejected before any client or network construction.
    """
    wrapper_type = getattr(_s, body_wrapper)
    fields = getattr(wrapper_type, "model_fields", {})
    if len(fields) != 1:
        # Unusual wrapper shape: let the SDK model do its own validation.
        return wrapper_type(**body)
    field_name = next(iter(fields))
    inner_type = getattr(_s, "ExistingTransaction", None)
    if inner_type is None:
        raise ValueError(f"{body_wrapper} wraps an unknown inner model")
    inner_fields = getattr(inner_type, "model_fields", {})
    update_keys = set(inner_fields) | {
        alias for field in inner_fields.values() if (alias := field.alias)
    }
    valid_keys = update_keys | _SELECTOR_FIELDS

    if field_name in body:
        unknown = set(body) - {field_name}
        if unknown:
            raise ValueError(f"Unknown body field(s): {sorted(unknown)}")
        inner_payload = body[field_name]
        if not isinstance(inner_payload, dict):
            raise ValueError(f"body[{field_name!r}] must be a JSON object")
        unknown = set(inner_payload) - valid_keys
        if unknown:
            raise ValueError(f"Unknown body field(s) for {field_name!r}: {sorted(unknown)}")
        payload = {key: value for key, value in inner_payload.items() if key in update_keys}
    else:
        unknown = set(body) - valid_keys
        if unknown:
            raise ValueError(f"Unknown body field(s): {sorted(unknown)}")
        payload = {key: value for key, value in body.items() if key in update_keys}

    inner = inner_type(**payload)
    if not inner.model_dump(exclude_none=True):
        raise ValueError("body must include at least one field to update")
    return wrapper_type(**{field_name: inner})


def _body_parameter_name(method: Callable[..., Any], provided: Mapping[str, Any]) -> str:
    """Resolve the SDK method's request-body parameter name.

    The body parameter is the single non-underscore parameter the adapter body
    did not already supply as a kwarg (SDK 4.3 names it ``data`` on most write
    methods but ``put_scheduled_transaction_wrapper`` on scheduled writes).
    Falling back to common names keeps compatibility with mocks that expose
    ``data``/``body``/``wrapper``.  Resolving from the real signature avoids
    silently passing the body under the wrong kwarg (mock-tested code would
    otherwise pass ``data=...`` to a method that expects another name).
    """
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return "data"
    candidates = [
        name
        for name in parameters
        if not name.startswith("_") and name != "self" and name not in provided
    ]
    if len(candidates) == 1:
        return candidates[0]
    for name in ("data", "body", "wrapper"):
        if name in parameters:
            return name
    return "data"


def api_adapter(
    operation_id: str,
    *,
    api: str,
    method: str,
    body_wrapper: Optional[str] = None,
    annotations: Optional[types.ToolAnnotations] = None,
) -> Callable[[BodyT], BodyT]:
    """Decorate an SDK operation body as the canonical ``api_`` MCP tool.

    The decorated body has the implementation-facing shape
    ``async def body(client, *, ctx=None, **params) -> dict``.  The returned
    callable is the user-facing tool with the client hidden from its schema.
    For writes, ``body`` must be a dict and is validated before the client
    context is constructed.  Read retry policy is controlled by the module
    constants :data:`READ_MAX_ATTEMPTS` and :data:`READ_BASE_DELAY`.
    """
    if not operation_id:
        raise ValueError("operation_id must not be empty")

    def decorator(body: BodyT) -> BodyT:
        resolved_annotations = annotations if annotations is not None else _READ_ONLY
        body_signature = inspect.signature(body)
        body_parameters = list(body_signature.parameters.values())
        if not body_parameters or body_parameters[0].name != "client":
            raise TypeError("adapter body must have client as its first parameter")

        resolved_hints = get_type_hints(body, include_extras=True)
        public_parameters = [
            parameter.replace(annotation=resolved_hints.get(parameter.name, parameter.annotation))
            for parameter in body_parameters[1:]
        ]
        public_signature = body_signature.replace(
            parameters=public_parameters,
            return_annotation=resolved_hints.get("return", body_signature.return_annotation),
        )

        @wraps(body)
        async def tool(**params: Any) -> dict[str, Any]:
            """Invoke the decorated SDK operation through the shared boundary."""
            if body_wrapper is not None:
                if "body" not in params or not isinstance(params["body"], dict):
                    raise ValueError(f"api_{operation_id} requires body to be a JSON object (dict)")
                validated_body = _construct_body_wrapper(body_wrapper, params["body"])
            else:
                validated_body = None

            async with await _s.get_ynab_client() as client:
                api_instance = getattr(_s, api)(client)
                sdk_method = getattr(api_instance, method)
                body_kwargs = await body(client, **params)
                if not isinstance(body_kwargs, dict):
                    raise TypeError(
                        f"api_{operation_id} body must return a dict of SDK method kwargs"
                    )
                call_kwargs = dict(body_kwargs)
                if body_wrapper is not None:
                    call_kwargs[_body_parameter_name(sdk_method, call_kwargs)] = validated_body

                try:
                    if resolved_annotations.readOnlyHint is True:
                        response = await _errors.run_with_retry(
                            lambda: sdk_method(**call_kwargs),
                            idempotent=True,
                            max_attempts=READ_MAX_ATTEMPTS,
                            base_delay=READ_BASE_DELAY,
                        )
                    else:
                        response = sdk_method(**call_kwargs)
                except _s.ApiException as exc:
                    raise _errors.normalize_api_exception(exc) from exc

            serialized = _serialize_response(response)
            if operation_id == "get_transactions":
                _persist_transaction_delta(
                    params["plan_id"],
                    serialized,
                    call_kwargs.get("last_knowledge_of_server"),
                )
            return serialized

        tool.__signature__ = public_signature  # type: ignore[attr-defined]
        tool.__name__ = f"api_{operation_id}"
        tool.__qualname__ = f"api_{operation_id}"
        registered = _s.mcp.tool(
            name=f"api_{operation_id}",
            annotations=resolved_annotations,
            structured_output=True,
        )(tool)
        return registered  # type: ignore[return-value]

    return decorator


@api_adapter(
    "get_transactions",
    api="TransactionsApi",
    method="get_transactions",
    annotations=_READ_ONLY,
)
async def api_get_transactions(
    client: Any,
    *,
    plan_id: str,
    since_date: Optional[date] = None,
    until_date: Optional[date] = None,
    type: Optional[str] = None,
    last_knowledge_of_server: Optional[int] = None,
    ctx: Optional[Context] = None,
) -> dict[str, Any]:
    """Return transactions and round-trip the YNAB transaction knowledge token."""
    del client, ctx
    persisted_knowledge = _s.ynab_resources.get_knowledge(plan_id, TRANSACTIONS_RESOURCE)
    # Delta mode requires a valid cache baseline to merge against; without one,
    # a delta-only response would discard untouched records.  An explicit token
    # is honored only when a baseline exists; otherwise fall back to a full
    # fetch (which replaces the cache and its knowledge).
    has_baseline = _has_cached_transaction_records(_cache_file(), plan_id)
    effective_knowledge = None
    if last_knowledge_of_server is not None and has_baseline:
        effective_knowledge = last_knowledge_of_server
    elif persisted_knowledge is not None and has_baseline:
        effective_knowledge = persisted_knowledge
    return {
        "plan_id": plan_id,
        "since_date": since_date,
        "until_date": until_date,
        "type": type,
        "last_knowledge_of_server": effective_knowledge,
    }


@api_adapter(
    "update_transaction",
    api="TransactionsApi",
    method="update_transaction",
    body_wrapper="PutTransactionWrapper",
    annotations=_MUTATING,
)
async def api_update_transaction(
    client: Any,
    *,
    plan_id: str,
    transaction_id: str,
    body: Annotated[
        Dict[str, Any],
        Field(description="Flat SaveTransactionWithIdOrImportId-compatible request body."),
    ],
    ctx: Optional[Context] = None,
) -> dict[str, Any]:
    """Update one transaction using a flat SaveTransaction-compatible body."""
    del client, body, ctx
    return {"plan_id": plan_id, "transaction_id": transaction_id}


__all__ = [
    "READ_BASE_DELAY",
    "READ_MAX_ATTEMPTS",
    "TRANSACTIONS_CACHE_FILENAME",
    "TRANSACTIONS_RESOURCE",
    "api_adapter",
    "api_get_transactions",
    "api_update_transaction",
]
