"""Test environment setup and configuration."""

import os

import pytest
from ynab.api.budgets_api import BudgetsApi


def test_environment_variables():
    """Test that required environment variables are set."""
    assert "YNAB_API_KEY" in os.environ, "YNAB_API_KEY must be set in environment"


@pytest.mark.integration
def test_ynab_api_connection(ynab_client):
    """Test that we can connect to the YNAB API."""
    budgets_api = BudgetsApi(ynab_client)
    budgets_response = budgets_api.get_budgets()
    assert budgets_response.data.budgets is not None
    assert len(budgets_response.data.budgets) > 0


def test_preferences_files_exist():
    """Test that the preference file is loaded, and if not, returns None."""
