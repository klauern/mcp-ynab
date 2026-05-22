from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

import mcp_ynab.server as server
from mcp_ynab.code_mode import generate_stubs, run_code
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
    assert "truncated" in result.logs


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


def test_generate_stubs_splits_read_and_write_namespaces() -> None:
    stubs = generate_stubs(_mcp())
    assert "class ReadNamespace" in stubs
    assert "async def echo" in stubs
    assert "class WriteNamespace" in stubs
    assert "async def mutate" in stubs


@pytest.mark.asyncio
async def test_ynab_code_execute_requires_preference_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_enabled=False)),
    )

    result = await server.ynab_code_execute("return 1")

    assert result["ok"] is False
    assert result["error"] == "code_mode_disabled"


@pytest.mark.asyncio
async def test_ynab_code_execute_caps_requested_timeout(
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

    result = await server.ynab_code_execute("return 1", timeout=10.0)

    assert result == {"ok": True, "mode": "json"}
    assert captured["timeout_s"] == 3.0
    assert captured["max_output_chars"] == 50
