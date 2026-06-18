"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Generator
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv
from ynab.api_client import ApiClient
from ynab.configuration import Configuration


def pytest_configure(config: pytest.Config) -> None:
    """Configure custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test that requires YNAB API access",
    )


@pytest.fixture(scope="session")
def env_setup() -> None:
    """Load environment variables for integration tests."""
    load_dotenv(verbose=True)
    if not os.getenv("YNAB_API_KEY"):
        pytest.skip("YNAB_API_KEY not set in environment")


@pytest.fixture
def ynab_client(env_setup: None) -> Generator[ApiClient, None, None]:
    """Create a real YNAB API client for integration tests."""
    if not os.getenv("YNAB_API_KEY"):
        pytest.skip("YNAB_API_KEY not set in environment")

    configuration = Configuration(access_token=os.getenv("YNAB_API_KEY"))
    with ApiClient(configuration) as client:
        yield client


@pytest.fixture
def mock_ynab_apis(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch YNAB API constructors and the async client context manager.

    Returns a SimpleNamespace with `budgets`, `accounts`, `categories`, and
    `transactions` MagicMock instances. Tests configure return values on these
    mocks (e.g. `mock_ynab_apis.budgets.get_plans.return_value = ...`) and
    then call the tool function under test directly.
    """
    from mcp_ynab import server

    apis = SimpleNamespace(
        budgets=MagicMock(name="PlansApi"),
        accounts=MagicMock(name="AccountsApi"),
        categories=MagicMock(name="CategoriesApi"),
        transactions=MagicMock(name="TransactionsApi"),
        months=MagicMock(name="MonthsApi"),
        scheduled_transactions=MagicMock(name="ScheduledTransactionsApi"),
        payees=MagicMock(name="PayeesApi"),
        users=MagicMock(name="UserApi"),
    )

    class _DummyClientCtx:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    async def _fake_get_ynab_client() -> _DummyClientCtx:
        return _DummyClientCtx()

    monkeypatch.setattr(server, "get_ynab_client", _fake_get_ynab_client)
    monkeypatch.setattr(server, "PlansApi", lambda client: apis.budgets)
    monkeypatch.setattr(server, "AccountsApi", lambda client: apis.accounts)
    monkeypatch.setattr(server, "CategoriesApi", lambda client: apis.categories)
    monkeypatch.setattr(server, "TransactionsApi", lambda client: apis.transactions)
    monkeypatch.setattr(server, "MonthsApi", lambda client: apis.months)
    monkeypatch.setattr(
        server, "ScheduledTransactionsApi", lambda client: apis.scheduled_transactions
    )
    monkeypatch.setattr(server, "PayeesApi", lambda client: apis.payees)
    monkeypatch.setattr(server, "UserApi", lambda client: apis.users)

    return apis
