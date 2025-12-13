# MCP YNAB Server

An MCP server implementation that provides access to YNAB (You Need A Budget) functionality through the Model Context Protocol.

## Features

- View account balances and transactions
- Create new transactions
- Access YNAB data through standardized MCP resources

## Installation

```bash
uv pip install -e .
```

## Configuration

The server requires a YNAB API key to function. You can obtain one from your [YNAB Developer Settings](https://app.ynab.com/settings/developer).

The API key can be provided through:

1. Environment variable: `YNAB_API_KEY=your_api_key`
2. MCP secret management system
3. `.env` file in project root

## Usage

### Running the Server

```bash
# Development mode with hot reload and browser launch
task dev

# Production install for Claude Desktop, Goose, or any other MCP-supported environment
task install
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
# Install dependencies (uses uv)
task deps

# Run all tests including integration tests (you will need a YNAB API key for this)
task test:all

# Generate coverage report
task coverage

# Format and lint code
task fmt  # Should add this to Taskfile
```

## Project Tasks

This project uses a Taskfile for common operations. Key commands:

```bash
task dev       # Start dev server with auto-reload
task test      # Run unit tests
task coverage  # Generate test coverage report
task install   # Install production build
task deps      # Synchronize dependencies
```

See [Taskfile.yml](Taskfile.yml) for all available tasks.

## Claude Code Skill

This project includes a Claude Code skill for natural language interaction with YNAB transactions. The skill is located in `.claude/skills/ynab-transactions/` and provides:

- **Transaction Management**: Review, categorize, and create transactions
- **Spending Analysis**: Analyze spending patterns and budget performance
- **Account Balances**: Check account balances and reconcile
- **Workflow Automation**: Common workflows like daily review and weekly cleanup

### Using the Skill

When using Claude Code in this project, you can use natural language commands like:

```
"Show me my uncategorized transactions"
"Categorize that Starbucks purchase as Dining Out"
"How much did I spend on groceries this month?"
"Add a $42 transaction to Amazon"
```

See `.claude/skills/ynab-transactions/README.md` for detailed documentation and examples.

### Skill Documentation

- [SKILL.md](.claude/skills/ynab-transactions/SKILL.md) - Main skill definition and workflows
- [EXAMPLES.md](.claude/skills/ynab-transactions/EXAMPLES.md) - Usage examples
- [WORKFLOWS.md](.claude/skills/ynab-transactions/WORKFLOWS.md) - Detailed workflow guides
- [TROUBLESHOOTING.md](.claude/skills/ynab-transactions/TROUBLESHOOTING.md) - Common issues and solutions
