"""MCP prompts exposed by the YNAB server.

Prompts are user-invocable templates (slash-commands in MCP clients) that
guide the assistant through repetitive YNAB workflows. Each prompt returns
a static string of instructions; the prompts themselves make no API calls.
The assistant then drives the named tools/resources to do the actual work.

Three prompts (`weekly_review`, `categorize_recent`, `spending_by_payee`)
reference tools that have not yet shipped (`bulk_categorize`,
`approve_transactions`, `spending_by_payee`). Until those land, invoking
those prompts will steer the model toward `tool-not-found` errors — the
prompt text remains useful as a workflow specification.

Following the same `_s.mcp.<X>` attribute-lookup pattern as `tools/` and
`resources.py` so test monkeypatches against `mcp_ynab.server` propagate.
"""

from typing import Literal, Optional

from . import server as _s

_BUDGET_FALLBACK = (
    "If `budget_id` was not provided, first read the "
    "`ynab://preferences/budget_id` resource to discover the preferred "
    "budget. If no preference is set, ask the user which budget to use "
    "(call `get_budgets` to list available budgets)."
)


@_s.mcp.prompt(
    name="weekly_review",
    description="Walk through the weekly transaction-cleanup workflow.",
)
async def weekly_review(
    budget_id: Optional[str] = None,
    days_back: int = 7,
) -> str:
    """Weekly review prompt: triage uncategorized/unapproved transactions.

    NOTE: depends on `bulk_categorize` and `approve_transactions` tools that
    have not yet shipped — until those land, this prompt will surface a
    `tool-not-found` error when the model attempts the bulk steps.
    """
    bid = budget_id or "<resolve from ynab://preferences/budget_id>"
    return (
        f"You are helping the user clean up the last {days_back} days of "
        f"YNAB transactions for budget `{bid}`. Follow this workflow:\n\n"
        f"{_BUDGET_FALLBACK}\n\n"
        "1. Call `get_transactions_needing_attention` with "
        f"`days_back={days_back}` to fetch uncategorized + unapproved "
        "transactions.\n"
        "2. Group the results by payee. For each group, propose a single "
        "category — read the cached categories from "
        "`ynab://categories/{budget_id}` first, then pick the closest "
        "semantic match. Surface uncertainty when the payee is novel.\n"
        "3. Confirm the proposed category-per-payee batch with the user "
        "(one summary table, not one prompt per transaction).\n"
        "4. Apply categories with `bulk_categorize` (one call per "
        "category, listing the transaction ids).\n"
        "5. Approve the now-categorized transactions with "
        "`approve_transactions`.\n\n"
        "Report a final count: N categorized, M approved, K skipped. Do "
        "not invent payees or categories that don't exist in the budget."
    )


@_s.mcp.prompt(
    name="monthly_budget_check",
    description="Mid- or end-of-month sanity check: overspending and reallocation.",
)
async def monthly_budget_check(
    budget_id: Optional[str] = None,
    month: str = "current",
) -> str:
    """Monthly budget-check prompt: surface overspending, propose moves."""
    bid = budget_id or "<resolve from ynab://preferences/budget_id>"
    return (
        f"You are auditing the YNAB budget `{bid}` for month `{month}`. "
        "Goal: surface overspending and propose a remediation plan.\n\n"
        f"{_BUDGET_FALLBACK}\n\n"
        f"1. Call `get_month` with `budget_id` and `month={month!r}` to "
        "fetch the snapshot (Ready to Assign, Age of Money, totals, and "
        "the per-category table).\n"
        "2. Identify categories where `activity` exceeds `budgeted` "
        "(overspending) — flag the magnitude in absolute dollars.\n"
        "3. Identify categories with positive remaining balance that are "
        "candidates to fund the overspending (prefer flexible categories "
        "like dining/entertainment over fixed obligations).\n"
        "4. Surface the Age of Money trend: note whether it has improved "
        "or degraded from prior months.\n"
        "5. Propose specific `move_money` calls (source → destination, "
        "amount) to cover each overspend. Wait for user confirmation "
        "before executing.\n\n"
        "Output: a short markdown report with overspends, suggested "
        "moves, and the AoM headline. Do not call `move_money` until the "
        "user approves."
    )


@_s.mcp.prompt(
    name="move_money_interactive",
    description="Interactive reallocation between two categories.",
)
async def move_money_interactive(
    budget_id: Optional[str] = None,
    month: str = "current",
) -> str:
    """Interactive money-move prompt: source, destination, amount, confirm."""
    bid = budget_id or "<resolve from ynab://preferences/budget_id>"
    return (
        f"The user wants to reallocate money between two categories in "
        f"budget `{bid}` for month `{month}`. Walk them through it.\n\n"
        f"{_BUDGET_FALLBACK}\n\n"
        "1. Read `ynab://categories/{budget_id}` for the category list. "
        f"Call `get_month` with `month={month!r}` to learn current "
        "balances per category.\n"
        "2. Ask the user which category is the **source** "
        "(filter your suggestions to categories with a positive "
        "remaining balance — moving from a negative category just "
        "deepens the hole).\n"
        "3. Ask which category is the **destination** "
        "(prefer one currently overspent, if the user is reallocating "
        "to cover a deficit).\n"
        "4. Ask the **amount** in dollars. If the destination is "
        "overspent, default the suggestion to the absolute overspend so "
        "the destination lands at zero balance.\n"
        "5. Confirm the trio (source, destination, amount) in one short "
        "summary, then call `move_money` with the resolved category ids.\n"
        "6. After the call returns, summarize the new balances by calling "
        "`get_category_for_month` for each side.\n\n"
        "If `move_money` raises a partial-application error, surface the "
        "recovery instructions verbatim — do not silently retry."
    )


@_s.mcp.prompt(
    name="fund_sinking_categories",
    description="Rule 2: allocate to non-monthly true-expense categories.",
)
async def fund_sinking_categories(
    budget_id: Optional[str] = None,
    month: str = "current",
) -> str:
    """Rule-2 prompt: fund underfunded goal-bearing categories from RTA."""
    bid = budget_id or "<resolve from ynab://preferences/budget_id>"
    return (
        "You are helping the user execute YNAB Rule 2 ('embrace your true "
        "expenses') — funding non-monthly categories like insurance, "
        "holidays, car maintenance, and subscriptions from Ready to "
        f"Assign in budget `{bid}` for month `{month}`.\n\n"
        f"{_BUDGET_FALLBACK}\n\n"
        f"1. Call `get_month` with `month={month!r}` to read the current "
        "Ready to Assign balance and the full per-category table.\n"
        "2. Identify categories that have a goal where this month's "
        "funded amount is below the goal target (these are the sinking "
        "funds that need attention).\n"
        "3. Surface the total Ready to Assign so the user knows their "
        "ceiling, then propose a distribution. Sort by urgency: "
        "categories whose goal `goal_target_month` is within 60 days "
        "should fund first.\n"
        "4. Present the proposed distribution as a markdown table "
        "(category | needed | proposed | running RTA after this row). "
        "Confirm with the user before applying.\n"
        "5. Apply approved allocations with `assign_money` "
        "(one call per category, amount in dollars). After each call, "
        "report the new budgeted amount.\n\n"
        "If Ready to Assign is negative, do NOT propose new allocations "
        "— surface the deficit and recommend `monthly_budget_check` to "
        "resolve overspending first."
    )


@_s.mcp.prompt(
    name="categorize_recent",
    description="Batch-categorize recent transactions for an account or budget.",
)
async def categorize_recent(
    budget_id: Optional[str] = None,
    account_id: Optional[str] = None,
    days_back: int = 14,
    auto_apply: bool = False,
) -> str:
    """Batch-categorize prompt with optional auto-apply.

    NOTE: depends on `bulk_categorize` (not yet shipped) — until that lands,
    invoking this prompt will surface a `tool-not-found` error when the
    model attempts the bulk step.
    """
    bid = budget_id or "<resolve from ynab://preferences/budget_id>"
    scope = f"account `{account_id}`" if account_id else "all accounts in the budget"
    apply_clause = (
        "Apply the proposed categories immediately via `bulk_categorize` "
        "without asking — the user opted into auto-apply."
        if auto_apply
        else "Present the proposed categories as a markdown table and "
        "WAIT for user confirmation before calling `bulk_categorize`. "
        "Do not auto-apply."
    )
    return (
        f"Batch-categorize the last {days_back} days of transactions on "
        f"{scope} in budget `{bid}`.\n\n"
        f"{_BUDGET_FALLBACK}\n\n"
        "1. Call `get_transactions_needing_attention` with "
        f"`days_back={days_back}`"
        + (f" and `account_id={account_id!r}`" if account_id else "")
        + ".\n"
        "2. Read `ynab://categories/{budget_id}` for the category list.\n"
        "3. Group transactions by payee. Propose a category per group, "
        "preferring matches that already exist on prior transactions for "
        "the same payee.\n"
        f"4. {apply_clause}\n"
        "5. Report counts: N categorized, M skipped (low-confidence), "
        "K errored.\n\n"
        "Skip transactions where confidence is low rather than guessing."
    )


@_s.mcp.prompt(
    name="spending_by_payee",
    description="Top-N payee spending report with period-over-period delta.",
)
async def spending_by_payee(
    budget_id: Optional[str] = None,
    period: Literal["this_month", "last_month", "last_90d", "ytd"] = "this_month",
) -> str:
    """Spending-by-payee prompt: top-N ranking with period delta.

    NOTE: depends on the `spending_by_payee` tool (not yet shipped) —
    until that lands, invoking this prompt will surface a `tool-not-found`
    error when the model attempts the data fetch.
    """
    bid = budget_id or "<resolve from ynab://preferences/budget_id>"
    return (
        f"Build a 'who got my money' report for budget `{bid}` over "
        f"period `{period}`.\n\n"
        f"{_BUDGET_FALLBACK}\n\n"
        f"1. Call the `spending_by_payee` tool with `period={period!r}` "
        "and a sensible top-N (default 15 — enough to show the long "
        "tail, short enough to scan).\n"
        "2. Compute the period-over-period delta: compare against the "
        "previous equivalent period (e.g. `this_month` → previous "
        "calendar month; `ytd` → same range last year).\n"
        "3. Format a markdown table: rank | payee | total | Δ vs prior "
        "period | Δ %. Sort descending by total. Bold any row where "
        "spending grew more than 50% period-over-period.\n"
        "4. Below the table, surface a one-line headline (e.g. "
        "'Groceries dropped 18% vs last month, dining doubled') based on "
        "the largest movers.\n\n"
        "Round dollar amounts to whole dollars in the table — the user "
        "is scanning for trends, not reconciling cents."
    )
