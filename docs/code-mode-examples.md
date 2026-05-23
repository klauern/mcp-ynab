# YNAB Code Mode Examples

These snippets are bodies for `execute`. Code Mode exposes a gated
Python namespace:

- `ynab.read.*` is available when `code_mode_enabled=true`.
- `ynab.write.*` requires `code_mode_mutations_enabled=true`.
- `LIMIT` is available for keeping returned data small (defaults to 100).

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
