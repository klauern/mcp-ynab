from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

import mcp_ynab.server as server
from mcp_ynab.code_mode import build_spec, generate_stubs, run_code, run_search
from mcp_ynab.state import Preferences


class EchoArgs(BaseModel):
    value: str


class EmptyArgs(BaseModel):
    pass


class FnMetadata:
    def __init__(self, arg_model: type[BaseModel]) -> None:
        self.arg_model = arg_model


def _tool(name: str, fn: Any, arg_model: type[BaseModel], *, read_only: bool) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        fn=fn,
        description=f"{name} description",
        fn_metadata=FnMetadata(arg_model),
        context_kwarg=None,
        annotations=SimpleNamespace(readOnlyHint=read_only),
    )


def _mcp() -> SimpleNamespace:
    async def echo(value: str) -> dict[str, str]:
        return {"value": value}

    async def mutate() -> dict[str, bool]:
        return {"mutated": True}

    tools = {
        "echo": _tool("echo", echo, EchoArgs, read_only=True),
        "mutate": _tool("mutate", mutate, EmptyArgs, read_only=False),
    }
    return SimpleNamespace(_tool_manager=SimpleNamespace(_tools=tools))


@pytest.mark.asyncio
async def test_run_code_can_call_read_namespace() -> None:
    result = await run_code(
        'print("hello")\nreturn await ynab.read.echo(value="ok")',
        mcp=_mcp(),
    )

    assert result.ok is True
    assert result.result == {"value": "ok"}
    assert result.logs == "hello\n"


@pytest.mark.asyncio
async def test_run_code_rejects_write_namespace_when_mutations_disabled() -> None:
    result = await run_code(
        "return await ynab.write.mutate()",
        mcp=_mcp(),
        mutations_enabled=False,
    )

    assert result.ok is False
    assert result.error == "mutations_disabled"


@pytest.mark.asyncio
async def test_run_code_allows_write_namespace_when_mutations_enabled() -> None:
    result = await run_code(
        "return await ynab.write.mutate()",
        mcp=_mcp(),
        mutations_enabled=True,
    )

    assert result.ok is True
    assert result.result == {"mutated": True}


@pytest.mark.asyncio
async def test_run_code_injects_none_ctx_into_proxied_tools() -> None:
    """Code mode must inject ``ctx=None`` into proxied tools, never the live ctx.

    The execute/code-mode session is already blocked awaiting the snippet, so a
    tool that awaits ``ctx.elicit(...)`` would deadlock until the soft timeout.
    All tool ctx uses are None-guarded, so injecting ``None`` makes them take
    their non-interactive fallback path instead of hanging. Regression guard for
    the ``delete_transaction``/``create_transaction`` code-mode hang.
    """
    received: dict[str, Any] = {}

    async def needs_ctx(value: str, ctx: Any = None) -> dict[str, bool]:
        received["ctx_is_none"] = ctx is None
        return {"ctx_is_none": ctx is None}

    tool = _tool("needs_ctx", needs_ctx, EchoArgs, read_only=True)
    tool.context_kwarg = "ctx"  # FastMCP auto-detects a ctx param like this
    mcp = SimpleNamespace(_tool_manager=SimpleNamespace(_tools={"needs_ctx": tool}))

    sentinel = object()  # stand-in for the live, interactive MCP context
    result = await run_code(
        'return await ynab.read.needs_ctx(value="x")',
        mcp=mcp,
        ctx=sentinel,  # even when a real ctx is supplied...
    )

    assert result.ok is True, result.error
    assert result.result == {"ctx_is_none": True}  # ...the tool must receive None
    assert received["ctx_is_none"] is True


@pytest.mark.asyncio
async def test_run_code_rejects_imports_and_dunders() -> None:
    result = await run_code("import os\nreturn None", mcp=_mcp())
    assert result.ok is False
    assert "imports are disabled" in result.error

    result = await run_code("return ().__class__", mcp=_mcp())
    assert result.ok is False
    assert "dunder attribute" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_error"),
    [
        ('with ynab.read.echo(value="ok"):\n    return None', "with blocks are disabled"),
        (
            'async with ynab.read.echo(value="ok"):\n    return None',
            "async with blocks are disabled",
        ),
    ],
)
async def test_run_code_rejects_with_blocks(code: str, expected_error: str) -> None:
    result = await run_code(code, mcp=_mcp())

    assert result.ok is False
    assert result.error == f"forbidden: {expected_error}"


@pytest.mark.asyncio
async def test_run_code_truncates_stdout() -> None:
    result = await run_code('print("abcdef")\nreturn 1', mcp=_mcp(), max_output_chars=5)
    assert result.ok is True
    assert result.truncated is True
    assert result.logs.startswith("abcde")
    assert "[... truncated]" in result.logs


@pytest.mark.asyncio
async def test_run_code_truncates_large_result() -> None:
    result = await run_code(
        "return {'items': list(range(100))}",
        mcp=_mcp(),
        max_output_chars=80,
    )

    assert result.ok is True
    assert result.truncated is True
    assert result.logs == ""
    assert result.result["truncated"] is True
    assert result.result["message"] == "result exceeded 80 characters and was truncated"
    assert result.result["preview"].startswith('{"items": [0, 1, 2')
    assert result.result["preview"].endswith("[... truncated]")
    assert len(result.result["preview"]) == 80


def test_generate_stubs_splits_read_and_write_namespaces() -> None:
    stubs = generate_stubs(_mcp())
    assert "class ReadNamespace" in stubs
    assert "async def echo(self, value: str)" in stubs
    assert "class WriteNamespace" in stubs
    assert "async def mutate(self)" in stubs


def test_build_spec_returns_catalog_entries() -> None:
    entries = build_spec(_mcp())
    assert len(entries) == 2
    names = {e["name"] for e in entries}
    assert names == {"echo", "mutate"}
    echo_entry = next(e for e in entries if e["name"] == "echo")
    assert echo_entry["namespace"] == "read"
    assert "value" in echo_entry["signature"]
    assert echo_entry["returns"] != ""
    mutate_entry = next(e for e in entries if e["name"] == "mutate")
    assert mutate_entry["namespace"] == "write"


def test_build_spec_omits_write_tools_when_mutations_disabled() -> None:
    entries = build_spec(_mcp(), mutations_enabled=False)
    namespaces = {e["namespace"] for e in entries}
    assert "write" not in namespaces


@pytest.mark.asyncio
async def test_run_search_filters_spec_with_snippet() -> None:
    spec = build_spec(_mcp())
    result = await run_search(
        'return [t for t in spec if t["namespace"] == "read"]',
        spec=spec,
    )
    assert result.ok is True
    assert isinstance(result.result, list)
    assert all(e["namespace"] == "read" for e in result.result)


@pytest.mark.asyncio
async def test_run_search_has_no_ynab_access() -> None:
    spec = build_spec(_mcp())
    result = await run_search("return ynab.read.echo(value='x')", spec=spec)
    assert result.ok is False
    assert "ynab" in result.error or result.error is not None


@pytest.mark.asyncio
async def test_run_search_disabled_code_mode_check_is_at_tool_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # run_search itself has no code_mode_enabled gate — gating lives in the MCP tool layer.
    # Verify it runs even with an empty spec.
    result = await run_search("return len(spec)", spec=[])
    assert result.ok is True
    assert result.result == 0


@pytest.mark.asyncio
async def test_execute_requires_preference_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_enabled=False)),
    )

    result = await server.execute("return 1")

    assert result["ok"] is False
    assert result["error"] == "code_mode_disabled"


@pytest.mark.asyncio
async def test_execute_caps_requested_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_code(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(model_dump=lambda mode: {"ok": True, "mode": mode})

    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(
            preferences=Preferences(
                code_mode_enabled=True,
                code_mode_timeout_s=3.0,
                code_mode_max_output_chars=50,
            )
        ),
    )
    monkeypatch.setattr("mcp_ynab.tools.code_mode.run_code", fake_run_code)

    result = await server.execute("return 1", timeout=10.0)

    assert result == {"ok": True, "mode": "json"}
    assert captured["timeout_s"] == 3.0
    assert captured["max_output_chars"] == 50


@pytest.mark.asyncio
async def test_execute_clamps_non_positive_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_code(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(model_dump=lambda mode: {"ok": True})

    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_enabled=True)),
    )
    monkeypatch.setattr("mcp_ynab.tools.code_mode.run_code", fake_run_code)

    result = await server.execute("return 1", timeout=0)

    assert result == {"ok": True}
    assert captured["timeout_s"] == 0.1


# -- Safe builtins expansion (mcp-ynab-22k) ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("return any([False, True, False])", True),
        ("return all([True, True, True])", True),
        ("return all([True, False])", False),
        ("return abs(-42)", 42),
        ("return round(3.7)", 4),
        ("return round(3.141, 2)", 3.14),
        ("return hasattr({'a': 1}, 'keys')", True),
        ("return isinstance(42, int)", True),
        ("return isinstance('hello', str)", True),
        ("return isinstance(3.14, float)", True),
    ],
)
async def test_expanded_safe_builtins_are_available(code: str, expected: object) -> None:
    """Newly added builtins (any, all, abs, round, hasattr, isinstance) work in snippets."""
    result = await run_code(code, mcp=_mcp())
    assert result.ok is True, result.error
    assert result.result == expected


@pytest.mark.asyncio
async def test_hasattr_dunder_string_is_blocked_by_audit() -> None:
    """hasattr with a dunder literal is blocked by the dunder-string-literal audit."""
    result = await run_code(
        'return hasattr({}, "__class__")',
        mcp=_mcp(),
    )
    assert result.ok is False
    assert "dunder string literal" in result.error


@pytest.mark.asyncio
async def test_isinstance_useful_for_type_filtering() -> None:
    """isinstance lets snippets filter heterogeneous lists without type()."""
    result = await run_code(
        "items = [1, 'a', 2, 'b']\nreturn [x for x in items if isinstance(x, int)]",
        mcp=_mcp(),
    )
    assert result.ok is True
    assert result.result == [1, 2]
