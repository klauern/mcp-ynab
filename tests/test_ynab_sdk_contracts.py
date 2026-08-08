"""Contracts against the installed YNAB SDK (pinned ynab>=4.3,<5).

These tests pin the OpenAPI 1.86 surface the server relies on so an SDK
upgrade cannot silently regress date-range semantics, goal modeling, or
bulk-write response decoding.  They are default-suite (no API key) because
they inspect the installed SDK's signatures and deserialization path only.
"""

from __future__ import annotations

import inspect

from ynab.api.categories_api import CategoriesApi
from ynab.api.transactions_api import TransactionsApi
from ynab.api_client import ApiClient
from ynab.models.new_category import NewCategory
from ynab.models.patch_transactions_wrapper import PatchTransactionsWrapper
from ynab.models.save_transaction_with_id_or_import_id import SaveTransactionWithIdOrImportId
from ynab.models.save_transactions_response import SaveTransactionsResponse

TRANSACTION_LIST_METHODS = (
    "get_transactions",
    "get_transactions_by_account",
    "get_transactions_by_category",
    "get_transactions_by_payee",
    "get_transactions_by_month",
)


def test_transaction_list_methods_expose_until_date() -> None:
    """Every transaction-list method accepts an explicit ``until_date`` bound.

    OpenAPI 1.86 added ``until_date`` to all five list endpoints.  The server
    relies on it (plus ``since_date``) to express explicit, closed date ranges
    instead of the API's one-year `since_date` default.
    """
    for method_name in TRANSACTION_LIST_METHODS:
        method = getattr(TransactionsApi, method_name)
        parameters = inspect.signature(method).parameters
        assert "until_date" in parameters, f"{method_name} lost until_date"
        assert "since_date" in parameters, f"{method_name} lost since_date"


def test_new_category_models_goal_frequency() -> None:
    """``goal_frequency`` is modeled with the documented recurring cadence."""
    assert "goal_frequency" in NewCategory.model_fields
    # Valid enum values from the OpenAPI 1.86 spec.
    category = NewCategory(name="Groceries", goal_frequency="monthly")
    assert category.goal_frequency == "monthly"
    for value in ("weekly", "yearly"):
        assert NewCategory(name="x", goal_frequency=value).goal_frequency == value


def test_get_transactions_by_category_owned_by_transactions_api() -> None:
    """Category transaction lookup lives on ``TransactionsApi``.

    ``CategoriesApi`` never exposed ``get_transactions_by_category``; calling
    it there fails at runtime with AttributeError.  The tool must use
    ``TransactionsApi`` and no permissive test mock may attach the method to
    ``CategoriesApi``.
    """
    assert hasattr(TransactionsApi, "get_transactions_by_category")
    assert not hasattr(CategoriesApi, "get_transactions_by_category")


def test_update_transactions_decodes_http_200() -> None:
    """Bulk update decodes an HTTP 200 ``SaveTransactionsResponse``.

    ynab 4.1.0 mapped bulk-write success to HTTP 209; the live API now returns
    200 (OpenAPI 1.86), which 4.1.0 would fail to decode.  This pins the 4.3
    contract end-to-end through the real SDK deserialization path.
    """
    payload = PatchTransactionsWrapper(
        transactions=[
            SaveTransactionWithIdOrImportId(
                id="11111111-1111-1111-1111-111111111111",
                payee_id="22222222-2222-2222-2222-222222222222",
            )
        ]
    )
    body = b'{"data": {"transaction_ids": ["11111111-1111-1111-1111-111111111111"], "server_knowledge": 42}}'

    class _FakeResponse:
        status = 200
        headers = {"content-type": "application/json"}
        data = body

        def read(self) -> bytes:
            return self.data

    client = ApiClient()
    client.call_api = lambda *_args, **_kwargs: _FakeResponse()  # type: ignore[method-assign]

    result = TransactionsApi(client).update_transactions_with_http_info("budget-1", payload)

    assert result.status_code == 200
    assert isinstance(result.data, SaveTransactionsResponse)
    assert result.data.data.transaction_ids == ["11111111-1111-1111-1111-111111111111"]
