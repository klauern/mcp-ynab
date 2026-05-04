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

## Run the server

```bash
mcp-ynab                 # production
task dev                 # dev mode + MCP Inspector in the browser
```

## Tools and resources

The server exposes the following over MCP:

**Read-only tools**

| Tool | Purpose |
| ---- | ------- |
| `get_budgets` | List all budgets in markdown |
| `get_accounts` | List accounts in a budget, grouped by type with summary |
| `get_account_balance` | Return a single account's current balance in dollars |
| `get_transactions` | Recent transactions for an account; optional `since_date` |
| `get_transactions_needing_attention` | Filter for uncategorized / unapproved transactions |
| `get_categories` | All categories in a budget grouped by category group |

**Mutating tools**

| Tool | Purpose |
| ---- | ------- |
| `create_transaction` | Create a new transaction in YNAB |
| `categorize_transaction` | Assign a category to an existing transaction |
| `set_preferred_budget_id` | Cache a preferred budget ID for default-targeted tools |
| `cache_categories` | Cache a budget's category list locally |

**Resources**

- `ynab://preferences/budget_id` — currently preferred budget ID
- `ynab://categories/{budget_id}` — cached categories for a budget

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
