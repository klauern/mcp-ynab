"""FastMCP server entry point and shared state for the YNAB MCP server.

The `mcp` FastMCP instance lives here, along with the shared tool-annotation
constants and the singleton `ynab_resources` store. Module-level imports of
the YNAB SDK API classes (`BudgetsApi`, `AccountsApi`, `CategoriesApi`,
`TransactionsApi`, `ExistingTransaction`, `PutTransactionWrapper`) are kept
at this scope so tests can patch them via
`monkeypatch.setattr(server, "<Name>", ...)`; tool modules access these names
through `server.<Name>` so the patches propagate.

Tool and resource handlers live in `mcp_ynab.tools.*` and
`mcp_ynab.resources` and are imported at the bottom of this module. By that
point `mcp`, `ynab_resources`, and the patched SDK names are all bound, so
the submodules' `@mcp.tool` and `@mcp.resource` decorators can register
against the FastMCP instance and resolve their dependencies.
"""

import logging

import mcp.types as types
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# SDK API classes & models kept at module scope so tests can patch them via
# `monkeypatch.setattr(server, "<Name>", ...)`. Tool modules access them as
# `server.<Name>` so those patches propagate via late attribute lookup.
from ynab.api.accounts_api import AccountsApi  # noqa: F401
from ynab.api.budgets_api import BudgetsApi  # noqa: F401
from ynab.api.categories_api import CategoriesApi  # noqa: F401
from ynab.api.months_api import MonthsApi  # noqa: F401
from ynab.api.transactions_api import TransactionsApi  # noqa: F401
from ynab.models.existing_transaction import ExistingTransaction  # noqa: F401
from ynab.models.patch_month_category_wrapper import PatchMonthCategoryWrapper  # noqa: F401
from ynab.models.put_transaction_wrapper import PutTransactionWrapper  # noqa: F401
from ynab.models.save_month_category import SaveMonthCategory  # noqa: F401
from ynab.rest import ApiException  # noqa: F401

# Helpers re-exported so `mcp_ynab.server.<name>` keeps working for callers
# and tests after the refactor.
from .client import (  # noqa: F401
    AsyncYNABClient,
    _get_client,
    _resolve_config_dir,
    get_ynab_client,
)
from .formatters import (  # noqa: F401
    _build_markdown_table,
    _format_accounts_output,
    _format_dollar_amount,
    _process_category_data,
)
from .state import (
    YNABResources,
    _load_json_file,  # noqa: F401
    _save_json_file,  # noqa: F401
)

load_dotenv(verbose=True)
logger = logging.getLogger(__name__)

mcp = FastMCP("YNAB")
READ_ONLY_TOOL = types.ToolAnnotations(readOnlyHint=True, idempotentHint=True)
MUTATING_TOOL = types.ToolAnnotations(readOnlyHint=False, destructiveHint=True)
IDEMPOTENT_MUTATING_TOOL = types.ToolAnnotations(
    readOnlyHint=False, idempotentHint=True, destructiveHint=False
)

# Singleton resources store. Bound here (not in state.py) so that
# `monkeypatch.setattr(server, "ynab_resources", isolated)` in tests rebinds
# the name that tool/resource modules look up via `server.ynab_resources`.
ynab_resources = YNABResources()

# Trigger registration of all @mcp.tool and @mcp.resource decorators. These
# imports must run after `mcp`, `ynab_resources`, and the SDK class names
# are bound above so the submodules can resolve them via `server.<name>`.
from . import prompts  # noqa: E402, F401
from . import resources  # noqa: E402, F401
from .tools import budgeting, transactions  # noqa: E402, F401

# Re-export tool and resource callables so `server.<tool>(...)` works for
# tests and downstream code. The decorators above are what register the
# tools with `mcp`; these imports just bind the names on the server module.
from .resources import (  # noqa: E402, F401
    get_cached_categories,
    get_current_month_resource,
    get_month_resource,
    get_preferred_budget_id,
)
from .tools.budgeting import (  # noqa: E402, F401
    assign_money,
    cache_categories,
    get_account_balance,
    get_accounts,
    get_budgets,
    get_categories,
    get_category_for_month,
    get_month,
    move_money,
    set_preferred_budget_id,
)
from .tools.transactions import (  # noqa: E402, F401
    _filter_transactions,
    _find_category_id,
    _find_transaction_by_id,
    _get_transaction_row,
    approve_transactions,
    bulk_categorize,
    categorize_transaction,
    create_transaction,
    get_transactions,
    get_transactions_needing_attention,
)
from .prompts import (  # noqa: E402, F401
    categorize_recent,
    fund_sinking_categories,
    monthly_budget_check,
    move_money_interactive,
    spending_by_payee,
    weekly_review,
)
