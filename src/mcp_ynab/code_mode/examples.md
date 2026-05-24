# YNAB Code Mode Examples

These snippets are bodies for `execute`. Code Mode exposes a gated
Python namespace:

- `ynab.read.*` is available when `code_mode_enabled=true`.
- `ynab.write.*` requires `code_mode_mutations_enabled=true`.
- `LIMIT` is available for keeping returned data small (defaults to 100).

Use `search` first when you need to discover available operations. `search`
snippets inspect `spec` and cannot access live YNAB data:

```python
return [
    {"name": tool["name"], "description": tool["description"]}
    for tool in spec
    if "transaction" in tool["name"]
]
```

## Read-only budget listing

Use this first to confirm Code Mode can reach YNAB without changing anything.

```python
budgets = await ynab.read.get_budgets()

rows = []
for budget in budgets[:LIMIT]:
    rows.append(
        {
            "id": budget.id,
            "name": budget.name,
            "last_modified_on": str(budget.last_modified_on),
        }
    )

print("found", len(budgets), "budgets")
return rows
```

## Summarize transactions needing attention

This keeps the transaction payload out of the final result and returns a compact
payee summary for uncategorized or otherwise attention-worthy transactions.

```python
transactions = await ynab.read.get_transactions_needing_attention()

summary = {}
for txn in transactions:
    payee = txn.payee_name or "(no payee)"
    item = summary.setdefault(
        payee,
        {"count": 0, "total_milliunits": 0, "sample_ids": []},
    )
    item["count"] += 1
    item["total_milliunits"] += int(txn.amount or 0)
    if len(item["sample_ids"]) < 3:
        item["sample_ids"].append(txn.id)

ranked = sorted(
    summary.items(),
    key=lambda pair: (pair[1]["count"], abs(pair[1]["total_milliunits"])),
    reverse=True,
)

return [
    {
        "payee": payee,
        "count": data["count"],
        "total": data["total_milliunits"] / 1000,
        "sample_ids": data["sample_ids"],
    }
    for payee, data in ranked[:LIMIT]
]
```

## Write-mode categorization by payee

Requires `code_mode_mutations_enabled=true`. Replace the placeholder category
IDs with IDs from `ynab.read.get_categories(...)` before running it.

```python
COFFEE_CATEGORY_ID = "replace-with-coffee-category-id"
GROCERIES_CATEGORY_ID = "replace-with-groceries-category-id"

transactions = await ynab.read.get_transactions_needing_attention()

assignments = []
for txn in transactions:
    payee = (txn.payee_name or "").upper()
    if "STARBUCKS" in payee:
        assignments.append({"id": txn.id, "category_id": COFFEE_CATEGORY_ID})
    elif "WHOLE FOODS" in payee:
        assignments.append({"id": txn.id, "category_id": GROCERIES_CATEGORY_ID})

if not assignments:
    return {"updated": 0, "reason": "no matching transactions"}

result = await ynab.write.bulk_categorize(assignments=assignments)
print("categorized", len(assignments), "transactions")
return result
```

## Bulk approve unreviewed transactions

Approves all unapproved transactions from the attention queue in one round-trip.
Requires `code_mode_mutations_enabled=true`.

```python
transactions = await ynab.read.get_transactions_needing_attention()

unapproved = [txn.id for txn in transactions if not getattr(txn, "approved", False)]

if not unapproved:
    return {"approved": 0, "reason": "all transactions already approved"}

result = await ynab.write.approve_transactions(
    budget_id=transactions[0].budget_id if hasattr(transactions[0], "budget_id") else budget_id,
    transaction_ids=unapproved[:LIMIT],
)
print("approved", len(unapproved[:LIMIT]), "transactions")
return result
```

## Spending by category — current month

Returns a preformatted markdown table of outflows grouped by category.

```python
budgets = await ynab.read.get_budgets()
budget_id = budgets[0].id  # use the first budget, or hard-code your preferred ID

return await ynab.read.spending_by_category(
    budget_id=budget_id,
    period="this_month",
    top_n=20,
)
```

## Transaction triage — group uncategorized by payee

Groups uncategorized transactions by payee so you can spot which payees need
category rules. Read-only — nothing is modified.

```python
transactions = await ynab.read.get_transactions_needing_attention()

uncategorized = [t for t in transactions if not getattr(t, "category_id", None)]

grouped: dict[str, list[str]] = {}
for txn in uncategorized:
    payee = txn.payee_name or "(no payee)"
    grouped.setdefault(payee, []).append(txn.id)

ranked = sorted(grouped.items(), key=lambda p: len(p[1]), reverse=True)
return [
    {"payee": payee, "count": len(ids), "sample_ids": ids[:3]}
    for payee, ids in ranked[:LIMIT]
]
```

## Spending by payee — last 30 days

Top spenders over the last 30 days across all categories, returned as formatted markdown.

```python
budgets = await ynab.read.get_budgets()
budget_id = budgets[0].id

return await ynab.read.spending_by_payee(
    budget_id=budget_id,
    period="last_30d",
    top_n=15,
)
