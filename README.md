# mcp-ynab

A [Model Context Protocol](https://modelcontextprotocol.io) server for the
[YNAB](https://www.ynab.com) (You Need A Budget) API. Lets MCP clients (Claude
Desktop, Claude Code, custom agents) read your budgets, accounts, and
transactions, and create or recategorize transactions through structured tools.

## Install

The project is managed with [uv](https://docs.astral.sh/uv/) and
[Task](https://taskfile.dev/). All Python commands in this repo go through
`uv run`.

```bash
uv sync                  # install dependencies
task install             # install the mcp-ynab CLI into the venv
```

## Configure

Set your YNAB Personal Access Token in the environment (or in a `.env` file at
the repo root):

```bash
export YNAB_API_KEY=your-personal-access-token
```

Get a token at <https://app.ynab.com/settings/developer>.

## Quickstart

### 1. Set your API key
Code Mode is the default surface — it requires your YNAB API key:
```bash
export YNAB_API_KEY="your-key-here"
```
Or store it in your OS keychain (the server reads it automatically).

### 2. Use Code Mode
By default the server exposes two tools: **`search`** (discover available operations) and **`execute`** (run a snippet against the live YNAB API).

**Discover tools:**
```python
# In the search tool:
return [t for t in spec if "transaction" in t["name"]]
```

**Execute a query:**
```python
# In the execute tool:
return await ynab.read.get_budgets()
```

### 3. Restore the full tool surface (optional)
To access all ~34 direct YNAB tools, set the escape-hatch preference:
```text
set_preference: code_mode_replace_tools = false
```

## Run the server

```bash
mcp-ynab                 # production
task dev                 # dev mode + MCP Inspector in the browser
```

## Tools and resources

By default, the public MCP tool surface is intentionally small:

| Tool | Purpose |
| ---- | ------- |
| `search` | Discover available YNAB operations without live API access |
| `execute` | Run a short Python snippet against the live YNAB API |
| `ping` | Health check the server |
| `get_preferences` | Inspect server preferences |
| `set_preference` | Update preferences such as mutation or escape-hatch settings |
| `set_api_key` | Store a YNAB API key in the OS keychain |
| `clear_api_key` | Remove the stored YNAB API key |
| `set_preferred_budget_id` | Cache a preferred budget ID for default-targeted calls |

Use `search` to discover operations:

```python
return [
    {"name": tool["name"], "description": tool["description"]}
    for tool in spec
    if "category" in tool["name"]
]
```

Then call the operation through `execute`:

```python
categories = await ynab.read.get_categories()
return [
    {"name": category.name, "balance": category.balance}
    for group in categories
    for category in group.categories[:LIMIT]
]
```

Mutating operations live under `ynab.write.*` and require
`code_mode_mutations_enabled=true`:

```python
result = await ynab.write.bulk_categorize(assignments=assignments)
return result
```

The underlying direct tools still exist in the internal FastMCP registry so
Code Mode can dispatch through `ynab.read.*`, generate stubs, and build the
search catalog. They are hidden from the public tool list by default. Set
`code_mode_replace_tools=false` to restore the full direct-tool surface as an
escape hatch.

Representative internal direct tools include:

**Read-only internal tools**

| Tool | Purpose |
| ---- | ------- |
| `get_budgets` | List all budgets in markdown |
| `get_accounts` | List accounts in a budget, grouped by type with summary |
| `get_account_balance` | Return a single account's current balance in dollars |
| `get_transactions` | Recent transactions for an account; optional `since_date` |
| `get_transactions_needing_attention` | Filter for uncategorized / unapproved transactions |
| `get_categories` | All categories in a budget grouped by category group |

**Mutating internal tools**

| Tool | Purpose |
| ---- | ------- |
| `create_transaction` | Create a new transaction in YNAB |
| `categorize_transaction` | Assign a category to an existing transaction |
| `set_preferred_budget_id` | Cache a preferred budget ID for default-targeted tools |
| `cache_categories` | Cache a budget's category list locally |

For account transfers and credit-card payments, use the destination account's
transfer payee ID as `payee_id` when creating or updating a transaction. The
`get_payees` tool lists transfer payees with their `Transfer Account ID`.
Do not pass a `Transfer : ...` value as `payee_name`; YNAB rejects or treats
that as a regular payee instead of creating a linked transfer.

**Resources**

- `ynab://preferences/budget_id` — currently preferred budget ID
- `ynab://categories/{budget_id}` — cached categories for a budget
- `ynab://code-mode/stubs` — generated Python stubs for Code Mode
- `ynab://code-mode/examples` — curated Code Mode snippets

## Code Mode

Code Mode exposes `execute`, a Python execution tool for multi-step YNAB
workflows. It is enabled by default and controlled through the preferences
`code_mode_enabled`, `code_mode_mutations_enabled`, and `code_mode_replace_tools`.

See [src/mcp_ynab/code_mode/README.md](src/mcp_ynab/code_mode/README.md)
for usage, wiring instructions, and runner limits.

## Development

```bash
task fmt                 # ruff format + check --fix
task lint                # ruff check + format --check (no auto-fix)
task typecheck           # mypy
task docstrings          # interrogate (fails under 80%)
task test                # unit tests only (default; integration excluded)
task coverage            # unit tests with coverage report
```

## Integration tests

Integration tests are gated behind `pytest.mark.integration` and excluded from
the default `task test` run. They make real calls to your YNAB account, so they
require `YNAB_API_KEY`.

```bash
YNAB_API_KEY=your-token task test:integration
```

The default integration suite is **read-only**. Tests that mutate data
(create_transaction, categorize_transaction) require an additional opt-in:

```bash
YNAB_API_KEY=your-token YNAB_INTEGRATION_ALLOW_WRITES=1 \
  task test:integration
```

## License

MIT
