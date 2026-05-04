# Product Context: MCP YNAB

## Problem Statement
Users of MCP-enabled assistants need safe, scriptable access to personal budgeting data in YNAB without manually jumping between tools. They also need lightweight write actions (for example, categorization) that do not mutate unrelated transaction properties.

## User Experience Goals
- A user can discover available budgets, accounts, categories, and transactions through MCP tools in one flow.
- A user can set and reuse a preferred budget with minimal repeated arguments.
- A user can recategorize a transaction without losing memo/approval/cleared/flag state.
- Responses are understandable in plain text for interactive agent sessions.

## Success Metrics
- Unit test suite remains green for critical read and write tool paths.
- Zero known regressions where transaction recategorization clears unrelated fields.
- Documentation accurately reflects actual resources, tools, and setup commands.
