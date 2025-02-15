from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ynab.api.accounts_api import AccountsApi
from ynab.api.budgets_api import BudgetsApi
from ynab.api.categories_api import CategoriesApi
from ynab.api.transactions_api import TransactionsApi
from ynab.api_client import ApiClient

from mcp_ynab.server import YNABResources

# Constants for testing
TEST_BUDGET_ID = "test-budget-123"
TEST_ACCOUNT_ID = "test-account-456"
TEST_CATEGORY_ID = "test-category-789"
TEST_TRANSACTION_ID = "test-transaction-012"


@pytest.fixture
def mock_ynab_client():
    """Mock YNAB API client."""
    with patch("mcp_ynab.server._get_client") as mock_get_client:
        client = AsyncMock(spec=ApiClient)
        mock_get_client.return_value = client
        yield client


@pytest.fixture
def mock_budgets_api():
    """Mock YNAB Budgets API."""
    with patch("ynab.api.budgets_api.BudgetsApi") as mock_api:
        api = MagicMock(spec=BudgetsApi)
        mock_api.return_value = api
        yield api


@pytest.fixture
def mock_accounts_api():
    """Mock YNAB Accounts API."""
    with patch("ynab.api.accounts_api.AccountsApi") as mock_api:
        api = MagicMock(spec=AccountsApi)
        mock_api.return_value = api
        yield api


@pytest.fixture
def mock_categories_api():
    """Mock YNAB Categories API."""
    with patch("ynab.api.categories_api.CategoriesApi") as mock_api:
        api = MagicMock(spec=CategoriesApi)
        mock_api.return_value = api
        yield api


@pytest.fixture
def mock_transactions_api():
    """Mock YNAB Transactions API."""
    with patch("ynab.api.transactions_api.TransactionsApi") as mock_api:
        api = MagicMock(spec=TransactionsApi)
        mock_api.return_value = api
        yield api


@pytest.fixture
def mock_xdg_config_home(tmp_path):
    """Mock XDG_CONFIG_HOME directory."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    with patch("mcp_ynab.server.XDG_CONFIG_HOME", str(config_dir)):
        yield config_dir


@pytest.fixture
def ynab_resources(mock_xdg_config_home):
    """Create a YNABResources instance with mocked config directory."""
    return YNABResources()


# Test helper functions
def test_build_markdown_table():
    """Test _build_markdown_table function."""
    # TODO: Test table building with various inputs
    # - Empty rows
    # - Different alignments
    # - Various data types
    # - Edge cases
    pass


def test_format_accounts_output():
    """Test _format_accounts_output function."""
    # TODO: Test account formatting with:
    # - Different account types
    # - Closed/deleted accounts
    # - Various balance scenarios
    # - Edge cases
    pass


def test_load_save_json_file(tmp_path):
    """Test _load_json_file and _save_json_file functions."""
    # TODO: Test JSON file operations:
    # - Save and load valid JSON
    # - Handle missing files
    # - Handle invalid JSON
    # - Edge cases
    pass


# Test YNAB Resources
class TestYNABResources:
    """Test YNABResources class functionality."""

    def test_get_set_preferred_budget_id(self, ynab_resources):
        """Test getting and setting preferred budget ID."""
        # TODO: Test budget ID operations
        pass

    def test_get_cached_categories(self, ynab_resources):
        """Test retrieving cached categories."""
        # TODO: Test category cache retrieval
        pass

    def test_cache_categories(self, ynab_resources):
        """Test caching categories."""
        # TODO: Test category caching
        pass


# Test MCP Tools
@pytest.mark.asyncio
class TestMCPTools:
    """Test all MCP tool functions."""

    async def test_create_transaction(self, mock_ynab_client, mock_transactions_api):
        """Test create_transaction tool."""
        # TODO: Test transaction creation with:
        # - Required fields
        # - Optional fields
        # - Category handling
        # - Error cases
        pass

    async def test_get_account_balance(self, mock_ynab_client, mock_accounts_api):
        """Test get_account_balance tool."""
        # TODO: Test balance retrieval:
        # - Valid account
        # - Invalid account
        # - Various balance formats
        pass

    async def test_get_budgets(self, mock_ynab_client, mock_budgets_api):
        """Test get_budgets tool."""
        # TODO: Test budget listing:
        # - Multiple budgets
        # - No budgets
        # - Error cases
        pass

    async def test_get_accounts(self, mock_ynab_client, mock_accounts_api):
        """Test get_accounts tool."""
        # TODO: Test account listing:
        # - Different account types
        # - Active/closed accounts
        # - Various balance scenarios
        pass

    async def test_get_transactions(self, mock_ynab_client, mock_transactions_api):
        """Test get_transactions tool."""
        # TODO: Test transaction listing:
        # - Date ranges
        # - Transaction types
        # - Formatting
        pass

    async def test_get_uncategorized_transactions(self, mock_ynab_client, mock_transactions_api):
        """Test get_uncategorized_transactions tool."""
        # TODO: Test uncategorized transaction listing:
        # - Mixed categorized/uncategorized
        # - Date filtering
        # - Account filtering
        pass

    async def test_get_transactions_needing_attention(
        self, mock_ynab_client, mock_transactions_api
    ):
        """Test get_transactions_needing_attention tool."""
        # TODO: Test attention-needed transactions:
        # - Uncategorized
        # - Unapproved
        # - Date ranges
        # - Filter combinations
        pass

    async def test_categorize_transaction(self, mock_ynab_client, mock_transactions_api):
        """Test categorize_transaction tool."""
        # TODO: Test transaction categorization:
        # - Different ID types
        # - Valid/invalid categories
        # - Error cases
        pass

    async def test_get_categories(self, mock_ynab_client, mock_categories_api):
        """Test get_categories tool."""
        # TODO: Test category listing:
        # - Category groups
        # - Nested categories
        # - Budget/activity amounts
        pass

    async def test_set_preferred_budget_id(self, ynab_resources):
        """Test set_preferred_budget_id tool."""
        # TODO: Test setting preferred budget:
        # - Valid budget ID
        # - Persistence
        # - Error cases
        pass

    async def test_cache_categories(self, mock_ynab_client, mock_categories_api, ynab_resources):
        """Test cache_categories tool."""
        # TODO: Test category caching:
        # - Valid categories
        # - Cache persistence
        # - Error cases
        pass


# Test API Client
@pytest.mark.asyncio
class TestAPIClient:
    """Test YNAB API client functionality."""

    async def test_get_client(self):
        """Test _get_client function."""
        # TODO: Test client creation:
        # - Valid API key
        # - Missing API key
        # - Configuration
        pass

    async def test_client_context_manager(self):
        """Test AsyncYNABClient context manager."""
        # TODO: Test context manager:
        # - Enter/exit behavior
        # - Resource cleanup
        # - Error handling
        pass
