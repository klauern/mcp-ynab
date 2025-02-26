from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ynab.api.accounts_api import AccountsApi
from ynab.api.budgets_api import BudgetsApi
from ynab.api.categories_api import CategoriesApi
from ynab.api.transactions_api import TransactionsApi
from ynab.api_client import ApiClient
from ynab.models.account import Account
from ynab.models.budget_summary import BudgetSummary
from ynab.models.category import Category
from ynab.models.category_group_with_categories import CategoryGroupWithCategories
from ynab.models.transaction_detail import TransactionDetail

from mcp_ynab.server import YNABResources

# Test constants
TEST_BUDGET_ID = "test-budget-123"
TEST_ACCOUNT_ID = "test-account-456"
TEST_CATEGORY_ID = "test-category-789"
TEST_TRANSACTION_ID = "test-transaction-012"

# Common test data
SAMPLE_ACCOUNT = {
    "id": TEST_ACCOUNT_ID,
    "name": "Test Account",
    "type": "checking",
    "balance": 100000,  # $100 in milliunits
    "closed": False,
    "deleted": False
}

SAMPLE_TRANSACTION = {
    "id": TEST_TRANSACTION_ID,
    "date": date.today().isoformat(),
    "amount": -50000,  # -$50 in milliunits
    "payee_name": "Test Payee",
    "category_id": TEST_CATEGORY_ID,
    "memo": "Test transaction",
    "cleared": True,
    "approved": True,
    "account_id": TEST_ACCOUNT_ID
}

SAMPLE_CATEGORY = {
    "id": TEST_CATEGORY_ID,
    "name": "Test Category",
    "budgeted": 200000,  # $200 in milliunits
    "activity": -50000,  # -$50 in milliunits
    "balance": 150000  # $150 in milliunits
}

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

@pytest.fixture
def sample_budget_summary():
    """Create a sample BudgetSummary."""
    return BudgetSummary(
        id=TEST_BUDGET_ID,
        name="Test Budget",
        last_modified_on=datetime.now()
    )

@pytest.fixture
def sample_account():
    """Create a sample Account."""
    return Account(**SAMPLE_ACCOUNT)

@pytest.fixture
def sample_transaction():
    """Create a sample TransactionDetail."""
    return TransactionDetail(**SAMPLE_TRANSACTION)

@pytest.fixture
def sample_category():
    """Create a sample Category."""
    return Category(**SAMPLE_CATEGORY)

@pytest.fixture
def sample_category_group():
    """Create a sample CategoryGroupWithCategories."""
    return CategoryGroupWithCategories(
        id="test-group-123",
        name="Test Group",
        categories=[sample_category()]
    )

# Test helper functions
class TestHelperFunctions:
    def test_build_markdown_table(self):
        """Test _build_markdown_table function."""
        headers = ["Name", "Value"]
        rows = [
            ["Test1", "100"],
            ["Test2", "200"]
        ]
        alignments = ["left", "right"]
        # TODO: Test table generation with various inputs
        # TODO: Test empty rows
        # TODO: Test different alignments
        # TODO: Test edge cases with special characters

    def test_format_accounts_output(self):
        """Test _format_accounts_output function."""
        # TODO: Test formatting different account types
        # TODO: Test closed/deleted accounts
        # TODO: Test negative balances
        # TODO: Test grouping by account type
        # TODO: Test summary calculations

    def test_load_save_json_file(self, tmp_path):
        """Test _load_json_file and _save_json_file functions."""
        # TODO: Test saving and loading valid JSON
        # TODO: Test loading non-existent file
        # TODO: Test saving to non-existent directory
        # TODO: Test with invalid JSON data

# Test YNAB Resources
class TestYNABResources:
    def test_init_loads_data(self, ynab_resources, mock_xdg_config_home):
        """Test YNABResources initialization loads data correctly."""
        # TODO: Test initialization with existing files
        # TODO: Test initialization with missing files

    def test_get_set_preferred_budget_id(self, ynab_resources):
        """Test getting and setting preferred budget ID."""
        # TODO: Test setting new budget ID
        # TODO: Test getting existing budget ID
        # TODO: Test persistence across instances

    def test_get_cached_categories(self, ynab_resources):
        """Test retrieving cached categories."""
        # TODO: Test with existing cached categories
        # TODO: Test with empty cache
        # TODO: Test with invalid cache data

    def test_cache_categories(self, ynab_resources):
        """Test caching categories."""
        # TODO: Test caching new categories
        # TODO: Test updating existing cache
        # TODO: Test with invalid category data

# Test MCP Tools
@pytest.mark.asyncio
class TestMCPTools:
    async def test_create_transaction(self, mock_ynab_client, mock_transactions_api, sample_transaction):
        """Test create_transaction tool."""
        # TODO: Test creating with minimum required fields
        # TODO: Test with optional fields
        # TODO: Test with category
        # TODO: Test with invalid data
        pass

    async def test_get_account_balance(self, mock_ynab_client, mock_accounts_api, sample_account):
        """Test get_account_balance tool."""
        # TODO: Test getting balance for valid account
        # TODO: Test with non-existent account
        # TODO: Test with closed account
        # TODO: Test with various balance formats

    async def test_get_budgets(self, mock_ynab_client, mock_budgets_api, sample_budget_summary):
        """Test get_budgets tool."""
        # TODO: Test listing multiple budgets
        # TODO: Test with no budgets
        # TODO: Test markdown formatting
        # TODO: Test error handling

    async def test_get_accounts(self, mock_ynab_client, mock_accounts_api, sample_account):
        """Test get_accounts tool."""
        # TODO: Test listing different account types
        # TODO: Test with closed accounts
        # TODO: Test markdown formatting
        # TODO: Test summary calculations

    async def test_get_transactions(
        self, mock_ynab_client, mock_transactions_api, sample_transaction
    ):
        """Test get_transactions tool."""
        # TODO: Test with date range
        # TODO: Test with specific account
        # TODO: Test markdown formatting
        # TODO: Test pagination handling

    async def test_get_transactions_needing_attention(
        self, mock_ynab_client, mock_transactions_api, sample_transaction
    ):
        """Test get_transactions_needing_attention tool."""
        # TODO: Test uncategorized filter
        # TODO: Test unapproved filter
        # TODO: Test both filters
        # TODO: Test with different date ranges
        # TODO: Test markdown output formatting

    async def test_categorize_transaction(
        self, mock_ynab_client, mock_transactions_api, sample_transaction
    ):
        """Test categorize_transaction tool."""
        # TODO: Test with valid transaction and category
        # TODO: Test with different ID types
        # TODO: Test with non-existent transaction
        # TODO: Test with invalid category

    async def test_get_categories(
        self, mock_ynab_client, mock_categories_api, sample_category_group
    ):
        """Test get_categories tool."""
        # TODO: Test listing all categories
        # TODO: Test nested category groups
        # TODO: Test markdown formatting
        # TODO: Test budget/activity calculations

    async def test_set_preferred_budget_id(self, ynab_resources):
        """Test set_preferred_budget_id tool."""
        # TODO: Test setting new budget ID
        # TODO: Test persistence
        # TODO: Test validation
        # TODO: Test error cases

    async def test_cache_categories(
        self, mock_ynab_client, mock_categories_api, ynab_resources, sample_category_group
    ):
        """Test cache_categories tool."""
        # TODO: Test caching new categories
        # TODO: Test updating existing cache
        # TODO: Test cache format
        # TODO: Test error handling

# Test API Client
@pytest.mark.asyncio
class TestAPIClient:
    async def test_get_client(self):
        """Test _get_client function."""
        # TODO: Test with valid API key
        # TODO: Test without API key
        # TODO: Test configuration options
        # TODO: Test error handling

    async def test_client_context_manager(self, mock_ynab_client):
        """Test AsyncYNABClient context manager."""
        # TODO: Test normal usage
        # TODO: Test error handling
        # TODO: Test resource cleanup
        # TODO: Test multiple context manager usage
