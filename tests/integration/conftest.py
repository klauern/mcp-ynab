"""Fixtures for integration tests that hit the real YNAB API.

All tests in this directory are marked with `pytest.mark.integration` via the
`pytestmark` module-level marker pattern in each test file. They are excluded
from the default `task test` run; use `task test:integration` to run them.

Requires `YNAB_API_KEY` in the environment (or in a `.env` file at the repo
root). Mutating tests additionally require `YNAB_INTEGRATION_ALLOW_WRITES=1`.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from dotenv import load_dotenv

load_dotenv()


def _require_api_key() -> None:
    if not os.getenv("YNAB_API_KEY"):
        pytest.skip("YNAB_API_KEY not set; skipping integration test")


@pytest.fixture(scope="session")
def integration_first_budget_id() -> str:
    """Discover the first budget id on the account; cache for the session."""
    _require_api_key()
    from ynab.api.budgets_api import BudgetsApi
    from ynab.api_client import ApiClient
    from ynab.configuration import Configuration

    config = Configuration(access_token=os.environ["YNAB_API_KEY"])
    with ApiClient(config) as client:
        budgets = BudgetsApi(client).get_budgets().data.budgets
        if not budgets:
            pytest.skip("YNAB account has no budgets")
        return budgets[0].id


@pytest.fixture(scope="session")
def integration_first_account_id(integration_first_budget_id: str) -> str:
    """Discover the first non-closed, non-deleted account id in the first budget."""
    from ynab.api.accounts_api import AccountsApi
    from ynab.api_client import ApiClient
    from ynab.configuration import Configuration

    config = Configuration(access_token=os.environ["YNAB_API_KEY"])
    with ApiClient(config) as client:
        accounts = AccountsApi(client).get_accounts(integration_first_budget_id).data.accounts
        for acct in accounts:
            if not acct.closed and not acct.deleted:
                return acct.id
        pytest.skip("First budget has no open accounts")
        raise RuntimeError("unreachable")  # for type checker


def writes_enabled() -> bool:
    """Return True when YNAB_INTEGRATION_ALLOW_WRITES is explicitly set to truthy."""
    val = os.getenv("YNAB_INTEGRATION_ALLOW_WRITES", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


@pytest.fixture
def require_writes_opt_in() -> Any:
    """Skip the test unless YNAB_INTEGRATION_ALLOW_WRITES is set."""
    if not writes_enabled():
        pytest.skip("Set YNAB_INTEGRATION_ALLOW_WRITES=1 to opt in to mutating tests")
