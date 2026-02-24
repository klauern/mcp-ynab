# MCP YNAB Server

MCP server for You Need A Budget (YNAB), implemented with Python and FastMCP.

## Requirements

- Python 3.12+
- A YNAB Personal Access Token (`YNAB_API_KEY`)

## Install

```bash
uv sync
uv pip install -e .
```

## Configuration

Set your YNAB token in one of the following ways:

1. Environment variable: `YNAB_API_KEY=...`
2. `.env` file in the repository root
3. MCP client secret management

Runtime cache/preferences are stored in:

- `${XDG_CONFIG_HOME}/mcp-ynab` if `XDG_CONFIG_HOME` is set
- `~/.config/mcp-ynab` otherwise

Files:

- `preferred_budget_id.json`
- `budget_category_cache.json`

## Run

```bash
# Dev mode
uv run mcp dev src/mcp_ynab/server.py

# Or via task
task dev
```

## MCP Resources

- `ynab://preferences/budget_id`
- `ynab://categories/{budget_id}`

## MCP Tools

- `create_transaction`
- `get_account_balance`
- `get_budgets`
- `get_accounts`
- `get_transactions`
- `get_transactions_needing_attention`
- `categorize_transaction`
- `get_categories`
- `set_preferred_budget_id`
- `cache_categories`

## Development

```bash
# Lint + format
task fmt

# Unit tests (default excludes integration)
task test

# Integration tests (requires real YNAB token)
task test:integration

# Coverage
task coverage
```
