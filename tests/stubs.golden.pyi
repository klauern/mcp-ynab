# fmt: off
from datetime import date
from typing import Any, Literal

class ReadNamespace:
    async def find_account_transaction_subset_matches(
        self,
        budget_id: str,
        account_id: str,
        target_amount: float,
        since_date: date | None = ...,
        tolerance: float = ...,
        max_subset_size: int = ...,
        candidate_limit: int = ...,
    ) -> dict[str, Any]:
        """Find compact transaction subsets whose amounts match a reconciliation difference.

        Args:
            target_amount: Target sum in dollars.
            since_date: ISO date (YYYY-MM-DD) to fetch transactions since. Defaults to all.
            tolerance: Allowed difference in dollars.
            max_subset_size: Maximum transactions per match.
            candidate_limit: Maximum candidates to search.
        """
        ...

    async def get_account_balance(self, account_id: str) -> float:
        """Get the current balance of a YNAB account (in dollars)."""
        ...

    async def get_account_reconciliation_profile(
        self,
        budget_id: str,
        account_id: str,
        since_date: date | None = ...,
        cleared: Literal | None = ...,
        include_transfers: bool = ...,
        limit: int = ...,
    ) -> dict[str, Any]:
        """Return structured account balance and transaction status totals for reconciliation.

        Args:
            since_date: ISO date (YYYY-MM-DD) to fetch transactions since. Defaults to all.
            cleared: Optional cleared status filter.
            limit: Maximum transaction rows to include.
        """
        ...

    async def get_accounts(self, budget_id: str) -> str:
        """List all YNAB accounts in a specific budget in Markdown format."""
        ...

    async def get_budgets(self) -> str:
        """List all YNAB budgets in Markdown format."""
        ...

    async def get_categories(self, budget_id: str) -> str:
        """List all transaction categories for a given YNAB budget in Markdown format."""
        ...

    async def get_category_for_month(self, budget_id: str, category_id: str, month: str = ...) -> str:
        """Return budgeted/activity/balance/goal for a single category in a month."""
        ...

    async def get_month(self, budget_id: str, month: str = ...) -> str:
        """Return a budget month snapshot: RTA, Age of Money, totals, per-group table."""
        ...

    async def get_payees(self, budget_id: str, include_deleted: bool = ...) -> str:
        """List payees for a YNAB budget in Markdown table form."""
        ...

    async def get_preferences(self) -> str:
        """Return the current YNAB MCP preferences as a markdown table."""
        ...

    async def get_scheduled_transactions(self, budget_id: str, within_days: int = ...) -> str:
        """List upcoming scheduled transactions for a YNAB budget.

        Args:
            within_days: Only include scheduled transactions due within this many days.
        """
        ...

    async def get_transactions(self, budget_id: str, account_id: str, since_date: date | None = ...) -> str:
        """Get recent transactions for a specific account in a specific budget.

        Args:
            since_date: ISO date (YYYY-MM-DD) to fetch transactions since. Defaults to the first day of …
        """
        ...

    async def get_transactions_by_category(self, budget_id: str, category_id: str, since_date: str | None = ...) -> str:
        """List transactions assigned to a specific category in a YNAB budget.

        Args:
            since_date: ISO date (YYYY-MM-DD) to filter transactions since.
        """
        ...

    async def get_transactions_needing_attention(
        self,
        budget_id: str,
        filter_type: Literal = ...,
        days_back: int | None = ...,
    ) -> str:
        """List transactions that need attention based on specified filter type in a YNAB budget.

        Args:
            filter_type: Type of transactions to show. One of: 'uncategorized', 'unapproved', 'both'
            days_back: Number of days to look back (default 30, None for all)
        """
        ...

    async def ping(self) -> str:
        """Verify YNAB API auth by fetching the current user's id."""
        ...

    async def spending_by_category(self, budget_id: str, period: Literal, top_n: int = ...) -> str:
        """Aggregate outflow spending by category over a named period."""
        ...

    async def spending_by_payee(
        self,
        budget_id: str,
        period: Literal,
        top_n: int = ...,
        account_id: str | None = ...,
    ) -> str:
        """Aggregate outflow spending by payee over a named period."""
        ...

class WriteNamespace:
    async def approve_transactions(self, budget_id: str, transaction_ids: list[str]) -> str:
        """Approve many transactions in one round-trip via the bulk PATCH endpoint.

        Args:
            transaction_ids: List of transaction IDs to mark as approved. Each ID is set to approved=True via…
        """
        ...

    async def assign_money(self, budget_id: str, category_id: str, amount: float, month: str = ...) -> str:
        """Set the budgeted amount for a category in a month (YNAB Rule 1)."""
        ...

    async def bulk_categorize(self, budget_id: str, assignments: list[dict[str, str]]) -> str:
        """Categorize many transactions in one round-trip via the bulk PATCH endpoint.

        Args:
            assignments: List of {transaction_id, category_id} dicts. Each entry assigns the given catego…
        """
        ...

    async def cache_categories(self, budget_id: str) -> str:
        """Force-fetch and cache categories for a budget id."""
        ...

    async def categorize_transaction(
        self,
        budget_id: str,
        transaction_id: str,
        category_id: str,
        id_type: str = ...,
    ) -> str:
        """Categorize a transaction for a given YNAB budget with the provided category ID."""
        ...

    async def clear_api_key(self) -> str:
        """Remove the stored YNAB API key from the OS keychain (env var unaffected)."""
        ...

    async def create_scheduled_transaction(
        self,
        budget_id: str,
        account_id: str,
        amount: float,
        frequency: Literal = ...,
        start_date: str | None = ...,
        payee_id: str | None = ...,
        payee_name: str | None = ...,
        category_id: str | None = ...,
        memo: str | None = ...,
        flag_color: str | None = ...,
    ) -> str:
        """Create a new scheduled (recurring) transaction in a YNAB budget."""
        ...

    async def create_transaction(
        self,
        account_id: str,
        amount: float,
        payee_name: str | None = ...,
        payee_id: str | None = ...,
        category_name: str | None = ...,
        memo: str | None = ...,
        confirm: bool = ...,
    ) -> dict[str, Any]:
        """Create a new transaction in YNAB.

        Args:
            amount: Amount in dollars. Negative for outflows (expenses, e.g. -42.50), positive for i…
            payee_id: Existing YNAB payee ID. For transfers, use the destination account's transfer pa…
            confirm: When True (default), elicit a yes/no confirmation before posting. Set False to s…
        """
        ...

    async def delete_transaction(self, budget_id: str, transaction_id: str) -> str:
        """Delete a transaction from a YNAB budget."""
        ...

    async def import_transactions(self, budget_id: str) -> list[str]:
        """Trigger YNAB to import transactions for any linked accounts in a budget."""
        ...

    async def merge_payees(
        self,
        budget_id: str,
        source_payee_id: str,
        destination_payee_id: str,
        delete_source: bool = ...,
    ) -> str:
        """Move every transaction from `source_payee_id` to `destination_payee_id`."""
        ...

    async def move_money(
        self,
        budget_id: str,
        from_category_id: str | None = ...,
        to_category_id: str | None = ...,
        amount: float | None = ...,
        month: str = ...,
    ) -> str:
        """Reallocate money from one category to another in a month (YNAB Rule 3)."""
        ...

    async def refresh_categories(self, budget_id: str, force: bool = ...) -> str:
        """Refresh the category cache for ``budget_id`` if stale (or always when ``force=True``)."""
        ...

    async def rename_payee(self, budget_id: str, payee_id: str, new_name: str) -> str:
        """Rename a YNAB payee."""
        ...

    async def set_api_key(self, api_key: str) -> str:
        """Store a YNAB personal access token in the OS keychain."""
        ...

    async def set_preference(self, name: str, value: str) -> str:
        """Set a single preference and persist it to ``preferences.json``."""
        ...

    async def set_preferred_budget_id(self, budget_id: str) -> str:
        """Set the preferred YNAB budget ID."""
        ...

    async def split_transaction(self, budget_id: str, transaction_id: str, splits: list[dict[str, Any]]) -> str:
        """Convert a transaction into a split with the provided subtransactions.

        Args:
            splits: List of split entries. Each entry is a dict with keys: `amount` (float, in dolla…
        """
        ...

    async def update_category(
        self,
        budget_id: str,
        category_id: str,
        name: str | None = ...,
        note: str | None = ...,
        category_group_id: str | None = ...,
    ) -> str:
        """Rename a category, update its note, or move it to a different category group."""
        ...

    async def update_transaction(
        self,
        budget_id: str,
        transaction_id: str,
        memo: str | None = ...,
        payee_name: str | None = ...,
        payee_id: str | None = ...,
        amount: float | None = ...,
        txn_date: str | None = ...,
        flag_color: str | None = ...,
        cleared: str | None = ...,
        approved: bool | None = ...,
        category_id: str | None = ...,
    ) -> str:
        """Partially update a single transaction (PATCH-style).

        Args:
            payee_id: Existing YNAB payee ID. For transfers, use the destination account's transfer pa…
            amount: Amount in dollars; converted to milliunits internally.
            txn_date: ISO date YYYY-MM-DD
            flag_color: One of: red, orange, yellow, green, blue, purple
            cleared: One of: cleared, uncleared, reconciled
        """
        ...

class YNABNamespace:
    read: ReadNamespace
    write: WriteNamespace

ynab: YNABNamespace
LIMIT: int
