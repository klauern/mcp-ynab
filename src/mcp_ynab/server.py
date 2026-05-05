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

from typing import Optional

import mcp.types as types
from dotenv import load_dotenv
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field
from ynab.api_client import ApiClient

# SDK API classes & models kept at module scope so tests can patch them via
# `monkeypatch.setattr(server, "<Name>", ...)`. Tool modules access them as
# `server.<Name>` so those patches propagate via late attribute lookup.
from ynab.api.accounts_api import AccountsApi  # noqa: F401
from ynab.api.budgets_api import BudgetsApi  # noqa: F401
from ynab.api.categories_api import CategoriesApi  # noqa: F401
from ynab.api.months_api import MonthsApi  # noqa: F401
from ynab.api.payees_api import PayeesApi  # noqa: F401
from ynab.api.scheduled_transactions_api import ScheduledTransactionsApi  # noqa: F401
from ynab.api.transactions_api import TransactionsApi  # noqa: F401
from ynab.api.user_api import UserApi  # noqa: F401
from ynab.models.existing_transaction import ExistingTransaction  # noqa: F401
from ynab.models.patch_month_category_wrapper import PatchMonthCategoryWrapper  # noqa: F401
from ynab.models.put_transaction_wrapper import PutTransactionWrapper  # noqa: F401
from ynab.models.save_month_category import SaveMonthCategory  # noqa: F401
from ynab.rest import ApiException  # noqa: F401

# Helpers re-exported so `mcp_ynab.server.<name>` keeps working for callers
# and tests after the refactor.
from .client import (  # noqa: F401
    AsyncYNABClient,
    _delete_stored_api_key,
    _get_client,
    _resolve_api_key,
    _resolve_config_dir,
    _store_api_key,
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


class _BudgetChoice(BaseModel):
    """Elicitation schema for selecting a budget when no preference is set."""

    index: int = Field(description="Number of the budget to use (1-based).")
    set_as_preferred: bool = Field(
        default=False,
        description="Save this budget as the preferred default for future calls.",
    )


async def _resolve_budget_id(client: ApiClient, ctx: Optional[Context]) -> str:
    """Return the budget id to operate on.

    Resolution order:
    1. `ynab_resources.get_preferred_budget_id()` if set.
    2. Single-budget shortcut — return the only budget without prompting.
    3. `ctx.elicit(...)` to ask the user when multiple budgets exist.

    When ``ctx`` is ``None`` and multiple budgets exist, raises ``ValueError``
    rather than silently picking ``budgets[0]`` — the silent fallback was the
    foot-gun this helper exists to remove.
    """
    budget_id = ynab_resources.get_preferred_budget_id()
    if budget_id:
        return budget_id

    budgets_api = BudgetsApi(client)
    budgets = budgets_api.get_budgets().data.budgets
    if not budgets:
        raise ValueError("No YNAB budgets available on this account.")
    if len(budgets) == 1:
        return budgets[0].id

    if ctx is None:
        raise ValueError(
            "Multiple budgets exist but no preferred budget is set and no "
            "MCP Context is available to elicit a choice. Call "
            "set_preferred_budget_id first."
        )

    options = "\n".join(f"{i + 1}. {b.name} (id={b.id})" for i, b in enumerate(budgets))
    result = await ctx.elicit(
        message=f"Multiple YNAB budgets found. Choose one:\n{options}",
        schema=_BudgetChoice,
    )
    if result.action == "accept":
        choice = result.data
        if choice.index < 1 or choice.index > len(budgets):
            raise ValueError(
                f"Selected budget index {choice.index} out of range 1..{len(budgets)}."
            )
        chosen = budgets[choice.index - 1]
        if choice.set_as_preferred:
            ynab_resources.set_preferred_budget_id(chosen.id)
        return chosen.id
    if result.action == "decline":
        raise ValueError("Budget selection declined; cannot proceed.")
    raise ValueError("Budget selection cancelled; cannot proceed.")


# Trigger registration of all @mcp.tool and @mcp.resource decorators. These
# imports must run after `mcp`, `ynab_resources`, and the SDK class names
# are bound above so the submodules can resolve them via `server.<name>`.
from . import prompts  # noqa: E402, F401
from . import resources  # noqa: E402, F401
from .tools import budgeting, preferences, transactions  # noqa: E402, F401

# Re-export tool and resource callables so `server.<tool>(...)` works for
# tests and downstream code. The decorators above are what register the
# tools with `mcp`; these imports just bind the names on the server module.
from .resources import (  # noqa: E402, F401
    get_cached_categories,
    get_current_month_resource,
    get_month_resource,
    get_preferred_budget_id,
    list_accounts_resource,
    list_budgets_resource,
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
    get_payees,
    merge_payees,
    move_money,
    ping,
    rename_payee,
    set_preferred_budget_id,
    spending_by_category,
)
from .tools.budgeting import (  # noqa: E402, F401
    spending_by_payee as spending_by_payee_tool,
)
from .tools.preferences import (  # noqa: E402, F401
    clear_api_key,
    set_api_key,
)
from .tools.transactions import (  # noqa: E402, F401
    _CategoryChoice,
    _PostConfirmation,
    _category_display_name,
    _confirm_create_transaction,
    _filter_transactions,
    _find_category_id,
    _find_transaction_by_id,
    _format_post_confirmation_message,
    _get_transaction_row,
    _resolve_category_id,
    approve_transactions,
    bulk_categorize,
    categorize_transaction,
    create_transaction,
    delete_transaction,
    get_scheduled_transactions,
    get_transactions,
    get_transactions_by_category,
    get_transactions_needing_attention,
    import_transactions,
    split_transaction,
    update_transaction,
)
from .prompts import (  # noqa: E402, F401
    categorize_recent,
    fund_sinking_categories,
    monthly_budget_check,
    move_money_interactive,
    spending_by_payee,
    weekly_review,
)
