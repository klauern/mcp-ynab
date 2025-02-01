# MCP YNAB Server

An MCP server implementation that provides access to YNAB (You Need A Budget) functionality through the Model Context Protocol.

## Features

- View account balances and transactions
- Create new transactions
- Access YNAB data through standardized MCP resources

## Installation

```bash
pip install -e .
```

## Configuration

The server requires a YNAB API key to function. You can obtain one from your [YNAB Developer Settings](https://app.ynab.com/settings/developer).

The API key can be provided in two ways:
1. As an environment variable: `YNAB_API_KEY=your_api_key`
2. Through the MCP secret system when running the server

## Usage

### Running the Server

```bash
# Development mode with the MCP Inspector
mcp dev src/mcp_ynab/server.py

# Install in Claude Desktop
mcp install src/mcp_ynab/server.py
```

### Available Resources

- `ynab://accounts` - List all YNAB accounts
- `ynab://transactions/{account_id}` - Get recent transactions for a specific account

### Available Tools

- `create_transaction` - Create a new transaction
- `get_account_balance` - Get the current balance of an account

## Example Usage

```python
# Create a new transaction
result = await create_transaction(
    account_id="your_account_id",
    amount=42.50,  # in dollars
    payee_name="Coffee Shop",
    category_name="Dining Out",
    memo="Morning coffee"
)

# Get account balance
balance = await get_account_balance("your_account_id")

# List accounts
accounts = await ctx.read_resource("ynab://accounts")

# Get recent transactions
transactions = await ctx.read_resource(f"ynab://transactions/{account_id}")
```

## Development

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black src/
ruff check src/ --fix
```
