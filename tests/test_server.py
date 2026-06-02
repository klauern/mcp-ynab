from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace

import pytest

import mcp_ynab.server as server
from mcp_ynab.state import Preferences


def test_format_accounts_output_groups_and_summary() -> None:
    formatted = server._format_accounts_output(
        [
            {
                "id": "a1",
                "name": "Checking",
                "type": "checking",
                "balance": 250_000,
                "closed": False,
                "deleted": False,
            },
            {
                "id": "a2",
                "name": "Credit",
                "type": "creditCard",
                "balance": -100_000,
                "closed": False,
                "deleted": False,
            },
        ]
    )

    assert formatted["summary"]["total_assets"] == "$250.00"
    assert formatted["summary"]["total_liabilities"] == "$100.00"
    assert formatted["summary"]["net_worth"] == "$150.00"


def test_build_markdown_table_empty_rows() -> None:
    table = server._build_markdown_table([], ["A", "B"])
    assert "| A" in table
    assert "| B" in table


@pytest.mark.asyncio
async def test_tools_register_categorize_transaction_not_private_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=False)),
    )
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}

    assert "categorize_transaction" in names
    assert "_find_transaction_by_id" not in names


@pytest.mark.asyncio
async def test_tools_include_annotations_for_read_only_and_mutating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=False)),
    )
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert tools["get_budgets"].annotations.readOnlyHint is True
    assert tools["create_transaction"].annotations.destructiveHint is True
    assert tools["set_preferred_budget_id"].annotations.readOnlyHint is False
    assert tools["set_preferred_budget_id"].annotations.idempotentHint is True
    assert tools["cache_categories"].annotations.readOnlyHint is False
    assert tools["cache_categories"].annotations.idempotentHint is True


@pytest.mark.asyncio
async def test_get_client_reads_api_key_from_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YNAB_API_KEY", raising=False)
    # Patch at the actual call site — _get_client() calls _resolve_api_key from
    # mcp_ynab.client directly, so patching the server re-export is ineffective.
    import mcp_ynab.client as _client_mod

    monkeypatch.setattr(_client_mod, "_resolve_api_key", lambda: None)

    with pytest.raises(ValueError, match="YNAB_API_KEY"):
        await server._get_client()


def test_resolve_api_key_prefers_env_over_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YNAB_API_KEY", "from-env")
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda _s, _u: "from-keychain")

    from mcp_ynab.client import _resolve_api_key

    assert _resolve_api_key() == "from-env"


def test_resolve_api_key_falls_back_to_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YNAB_API_KEY", raising=False)
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda _s, _u: "from-keychain")

    from mcp_ynab.client import _resolve_api_key

    assert _resolve_api_key() == "from-keychain"


def test_resolve_api_key_returns_none_when_both_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YNAB_API_KEY", raising=False)
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda _s, _u: None)

    from mcp_ynab.client import _resolve_api_key

    assert _resolve_api_key() is None


def test_resolve_api_key_swallows_keyring_backend_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YNAB_API_KEY", raising=False)
    import keyring

    def _boom(_s: str, _u: str) -> str:
        raise RuntimeError("no secret-service daemon")

    monkeypatch.setattr(keyring, "get_password", _boom)

    from mcp_ynab.client import _resolve_api_key

    assert _resolve_api_key() is None


def test_resolve_config_dir_respects_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg_base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_base))

    from mcp_ynab.client import _resolve_config_dir

    result = _resolve_config_dir()
    assert result == xdg_base / "mcp-ynab"
    assert result.is_dir()


def test_resolve_config_dir_defaults_to_home_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    from mcp_ynab.client import _resolve_config_dir

    result = _resolve_config_dir()
    assert result == tmp_path / ".config" / "mcp-ynab"
    assert result.is_dir()


@pytest.mark.asyncio
async def test_set_api_key_tool_persists_to_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(server, "_store_api_key", lambda key: captured.setdefault("key", key))

    msg = await server.set_api_key("  abc-123  ")

    assert captured == {"key": "abc-123"}
    assert "stored" in msg.lower()


@pytest.mark.asyncio
async def test_set_api_key_tool_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_store_api_key", lambda key: None)

    with pytest.raises(ValueError, match="non-empty"):
        await server.set_api_key("   ")


@pytest.mark.asyncio
async def test_clear_api_key_tool_reports_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_delete_stored_api_key", lambda: True)
    msg = await server.clear_api_key()
    assert "removed" in msg.lower()

    monkeypatch.setattr(server, "_delete_stored_api_key", lambda: False)
    msg = await server.clear_api_key()
    assert "no" in msg.lower()


@pytest.mark.asyncio
async def test_resource_and_templates_are_exposed() -> None:
    resources = await server.mcp.list_resources()
    templates = await server.mcp.list_resource_templates()

    assert any(str(r.uri) == "ynab://preferences/budget_id" for r in resources)
    assert any(str(r.uri) == "ynab://code-mode/stubs" for r in resources)
    assert any(str(r.uri) == "ynab://code-mode/examples" for r in resources)
    assert any(t.uriTemplate == "ynab://categories/{budget_id}" for t in templates)


@pytest.mark.asyncio
async def test_code_mode_tools_registered() -> None:
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert "execute" in tools
    assert "search" in tools
    assert tools["execute"].annotations.readOnlyHint is False
    assert tools["search"].annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_blank_preferences_default_to_code_mode_public_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences()),
    )

    names = {tool.name for tool in await server.mcp.list_tools()}

    assert names == {
        "search",
        "execute",
        "clear_api_key",
        "get_preferences",
        "ping",
        "set_api_key",
        "set_preference",
        "set_preferred_budget_id",
    }


@pytest.mark.asyncio
async def test_code_mode_replace_tools_filters_protocol_tool_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=True)),
    )

    handler = server.mcp._mcp_server.request_handlers[server.types.ListToolsRequest]
    result = await handler(server.types.ListToolsRequest())
    names = {tool.name for tool in result.root.tools}

    assert names == {
        "search",
        "execute",
        "clear_api_key",
        "get_preferences",
        "ping",
        "set_api_key",
        "set_preference",
        "set_preferred_budget_id",
    }


@pytest.mark.asyncio
async def test_code_mode_replace_tools_filters_instance_list_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=True)),
    )

    names = {tool.name for tool in await server.mcp.list_tools()}

    assert names == {
        "search",
        "execute",
        "clear_api_key",
        "get_preferences",
        "ping",
        "set_api_key",
        "set_preference",
        "set_preferred_budget_id",
    }


@pytest.mark.asyncio
async def test_code_mode_replace_tools_escape_hatch_restores_full_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=False)),
    )

    names = {tool.name for tool in await server.mcp.list_tools()}

    assert "execute" in names
    assert "search" in names
    assert "get_budgets" in names
    assert "get_transactions" in names
    assert "bulk_categorize" in names
    assert len(names) > 10


@pytest.mark.asyncio
async def test_code_mode_replace_tools_rejects_direct_hidden_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=True)),
    )

    with pytest.raises(ValueError, match="code_mode_replace_tools"):
        await server.mcp.call_tool("get_transactions", {})


@pytest.mark.asyncio
async def test_code_mode_replace_tools_rejects_protocol_hidden_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=True)),
    )

    handler = server.mcp._mcp_server.request_handlers[server.types.CallToolRequest]
    result = await handler(
        server.types.CallToolRequest(
            params=server.types.CallToolRequestParams(
                name="get_transactions",
                arguments={},
            )
        )
    )

    assert result.root.isError is True
    assert "code_mode_replace_tools" in result.root.content[0].text


def test_code_mode_examples_resource_uses_current_namespaces() -> None:
    content = server.get_code_mode_examples()
    assert len(content) == 1

    text = content[0].text
    assert len(text.strip()) > 500
    assert "ynab.read.get_budgets" in text
    assert "ynab.read.get_transactions_needing_attention" in text
    assert "ynab.write.bulk_categorize" in text


def test_code_mode_examples_missing_file_raises_specific_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fail the importlib.resources primary path
    def _raise(*a, **kw):
        raise FileNotFoundError("simulated missing package data")

    monkeypatch.setattr(server.resources, "_resource_files", _raise)
    # Fail the filesystem fallback paths
    original_is_file = Path.is_file
    monkeypatch.setattr(server.resources.Path, "cwd", lambda: Path("/missing-cwd"))
    monkeypatch.setattr(
        server.resources.Path,
        "is_file",
        lambda self: False
        if str(self).endswith("code-mode-examples.md")
        else original_is_file(self),
    )

    with pytest.raises(FileNotFoundError, match="Code Mode examples file not found"):
        server.resources._read_code_mode_examples()


def test_find_transaction_by_id_variants() -> None:
    class Txn:
        def __init__(self) -> None:
            self.id = "id-1"
            self.import_id = "YNAB:-100:2026-01-01:1"
            self.transfer_transaction_id = "transfer-1"
            self.matched_transaction_id = "matched-1"

    txn = Txn()
    txns = [txn]

    assert server._find_transaction_by_id(txns, "id-1", "id") is txn
    assert server._find_transaction_by_id(txns, "YNAB:-100:2026-01-01:1", "import_id") is txn
    assert server._find_transaction_by_id(txns, "transfer-1", "transfer_transaction_id") is txn
    assert server._find_transaction_by_id(txns, "matched-1", "matched_transaction_id") is txn
    assert server._find_transaction_by_id(txns, "missing", "id") is None


def test_ynab_resources_can_use_custom_config_dir(tmp_path: Path) -> None:
    resources = server.YNABResources(config_dir=tmp_path)
    resources.set_preferred_budget_id("budget-123")

    reloaded = server.YNABResources(config_dir=tmp_path)
    assert reloaded.get_preferred_budget_id() == "budget-123"


def test_load_json_file_handles_invalid_json(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{invalid", encoding="utf-8")
    assert server._load_json_file(corrupt) == {}


@pytest.mark.asyncio
async def test_categorize_transaction_id_path_skips_fetch_and_patches_only_category(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The "id" path must NOT GET the transaction first; it must only PATCH category_id."""
    captured_kwargs: dict = {}

    def fake_existing_transaction(**kwargs):
        captured_kwargs.update(kwargs)
        return kwargs

    monkeypatch.setattr(server, "ExistingTransaction", fake_existing_transaction)
    monkeypatch.setattr(
        server, "PutTransactionWrapper", lambda transaction: {"transaction": transaction}
    )

    result = await server.categorize_transaction("budget-1", "tx-1", "cat-1", id_type="id")

    assert "categorized as cat-1" in result
    mock_ynab_apis.transactions.get_transaction_by_id.assert_not_called()
    mock_ynab_apis.transactions.get_transactions.assert_not_called()
    call = mock_ynab_apis.transactions.update_transaction.call_args
    assert call.kwargs["budget_id"] == "budget-1"
    assert call.kwargs["transaction_id"] == "tx-1"
    assert captured_kwargs == {"category_id": "cat-1"}


@pytest.mark.asyncio
async def test_categorize_transaction_reraises_non_404_api_exception(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_ynab_apis.transactions.update_transaction.side_effect = server.ApiException(
        status=500, reason="server error"
    )
    monkeypatch.setattr(server, "ExistingTransaction", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        server, "PutTransactionWrapper", lambda transaction: {"transaction": transaction}
    )

    with pytest.raises(server.ApiException):
        await server.categorize_transaction("budget-1", "tx-1", "cat-1", id_type="id")


@pytest.mark.asyncio
async def test_categorize_transaction_returns_not_found_on_404(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 from update_transaction (e.g. id doesn't exist) must surface as 'not found'."""
    mock_ynab_apis.transactions.update_transaction.side_effect = server.ApiException(
        status=404, reason="not found"
    )
    monkeypatch.setattr(server, "ExistingTransaction", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        server, "PutTransactionWrapper", lambda transaction: {"transaction": transaction}
    )

    result = await server.categorize_transaction("budget-1", "tx-1", "cat-1", id_type="id")
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_categorize_transaction_only_ships_category_id_on_patch(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATCH semantics: ExistingTransaction must be constructed with ONLY category_id.

    Regression for the clobber-on-concurrent-edit bug: the old implementation
    fetched the transaction and re-PUT every field. If a user updated memo
    in YNAB between our GET and our PUT we would silently overwrite their
    edit. The fix is to send only category_id and let the server preserve
    every other field. This test would have caught the old behavior because
    it fails if anything other than category_id ends up in the wrapper.
    """
    captured: dict = {}

    def fake_existing_transaction(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(server, "ExistingTransaction", fake_existing_transaction)
    monkeypatch.setattr(
        server, "PutTransactionWrapper", lambda transaction: {"transaction": transaction}
    )

    await server.categorize_transaction("budget-1", "tx-1", "cat-new", id_type="id")

    assert set(captured.keys()) == {"category_id"}
    assert captured["category_id"] == "cat-new"
    for stale_field in ("memo", "cleared", "flag_color", "approved", "subtransactions"):
        assert stale_field not in captured, (
            f"{stale_field} must not be sent — server preserves it on PATCH"
        )


@pytest.mark.asyncio
async def test_categorize_transaction_does_not_clobber_concurrent_memo_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: simulate a concurrent memo edit in YNAB and verify our PATCH
    does not include the (now-stale) memo we would have read.

    Even if some path in the future re-introduces a fetch, the wire payload
    must NEVER include `memo`, so the user's concurrent edit is preserved.
    For the non-"id" id types we still scan to resolve the canonical id
    (the SDK's update_transaction needs the real id), but only category_id
    is shipped.
    """

    class StaleScanTxn:
        # The scan returns what we *would* have seen at GET time — but by
        # the time our PATCH lands the user has already updated memo to
        # something else. We must not echo this stale memo.
        id = "tx-real-id"
        import_id = "YNAB:1000:2026-02-01:1"
        transfer_transaction_id = None
        matched_transaction_id = None
        memo = "stale memo from before user edit"
        cleared = "cleared"
        flag_color = "red"
        approved = True
        account_id = "acct-1"
        var_date = "2026-02-01"
        amount = -1000
        payee_id = "payee-1"
        payee_name = "Coffee Shop"
        subtransactions = []

    class DummyApi:
        def __init__(self) -> None:
            self.update_calls: list[tuple[str, str, object]] = []

        def get_transactions(self, budget_id: str, since_date=None):
            return type(
                "Resp",
                (),
                {"data": type("Data", (), {"transactions": [StaleScanTxn()]})()},
            )()

        def update_transaction(self, budget_id: str, transaction_id: str, data):
            self.update_calls.append((budget_id, transaction_id, data))

    class DummyCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    api = DummyApi()
    captured: dict = {}

    async def fake_get_ynab_client():
        return DummyCtx()

    def fake_existing_transaction(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(server, "get_ynab_client", fake_get_ynab_client)
    monkeypatch.setattr(server, "TransactionsApi", lambda client: api)
    monkeypatch.setattr(server, "ExistingTransaction", fake_existing_transaction)
    monkeypatch.setattr(
        server, "PutTransactionWrapper", lambda transaction: {"transaction": transaction}
    )

    result = await server.categorize_transaction(
        "budget-1", "YNAB:1000:2026-02-01:1", "cat-new", id_type="import_id"
    )

    assert "categorized as cat-new" in result
    # Resolved to the canonical id from the scan.
    assert len(api.update_calls) == 1
    assert api.update_calls[0][1] == "tx-real-id"
    # The critical regression assertion: stale memo must NOT be on the wire.
    assert "memo" not in captured
    assert "stale memo from before user edit" not in str(captured)
    assert captured == {"category_id": "cat-new"}
