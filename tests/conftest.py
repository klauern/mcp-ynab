"""Pytest configuration and shared fixtures."""

import os
from typing import Generator

import pytest
from dotenv import load_dotenv
from ynab.api_client import ApiClient
from ynab.configuration import Configuration


def pytest_configure(config):
    """Configure custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test that requires YNAB API access",
    )


@pytest.fixture(scope="session")
def env_setup() -> None:
    """Load environment variables for tests."""
    load_dotenv(verbose=True)
    if not os.getenv("YNAB_API_KEY"):
        pytest.skip("YNAB_API_KEY not set in environment")


@pytest.fixture
def ynab_client(env_setup) -> Generator:
    """Create a YNAB API client for testing."""
    if not os.getenv("YNAB_API_KEY"):
        pytest.skip("YNAB_API_KEY not set in environment")

    configuration = Configuration(access_token=os.getenv("YNAB_API_KEY"))
    with ApiClient(configuration) as client:
        yield client
