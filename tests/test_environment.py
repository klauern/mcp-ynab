"""Test environment setup and configuration."""

import os

import pytest
from ynab.api.plans_api import PlansApi


@pytest.mark.integration
def test_environment_variables():
    """Test that required environment variables are set.

    Marked `integration` because it inherently requires `YNAB_API_KEY`,
    which is intentionally absent from default CI runs.
    """
    assert "YNAB_API_KEY" in os.environ, "YNAB_API_KEY must be set in environment"


@pytest.mark.integration
def test_ynab_api_connection(ynab_client):
    """Test that we can connect to the YNAB API."""
    plans_api = PlansApi(ynab_client)
    budgets_response = plans_api.get_plans()
    assert budgets_response.data.plans is not None
    assert len(budgets_response.data.plans) > 0


def test_preferences_files_exist():
    """Test that the preference file is loaded, and if not, returns None."""
