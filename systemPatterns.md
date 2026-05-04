# System Patterns: MCP YNAB

## System Architecture
The server is a single FastMCP app with function-based resources and tools. YNAB API access is wrapped through lightweight helper/context-manager functions, and small local JSON files are used for preferred budget and category cache persistence.

## Key Technical Decisions
- Use FastMCP decorators for resources/tools rather than custom protocol plumbing.
- Resolve environment and config paths at runtime to avoid import-time staleness.
- Keep helper functions pure where possible (`_build_markdown_table`, `_format_accounts_output`) for testability.
- Annotate tools with MCP `ToolAnnotations` to signal read-only/idempotent/mutating behavior.

## Design Patterns in Use
- Adapter pattern around YNAB SDK calls (`TransactionsApi`, `BudgetsApi`, etc.).
- Small utility functions for output transformation and formatting.
- Stateful resource helper class (`YNABResources`) for simple persisted preferences/cache.

## Component Relationships
- `FastMCP` routes tool/resource calls.
- Tool handlers acquire an API client via `get_ynab_client` and invoke YNAB SDK services.
- `YNABResources` is shared process state backed by local config files.
- Tests monkeypatch YNAB API objects and helpers to validate behavior without live API calls.

## Critical Implementation Paths
- Transaction recategorization path: locate transaction -> preserve state -> update category.
- Runtime configuration path: resolve config directory and API key at call time.
- Resource path: expose preferred budget and category cache as MCP-readable resources.
