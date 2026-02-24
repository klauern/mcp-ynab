# Progress: MCP YNAB

## What Works
- MCP server exposes resources and tools for core YNAB workflows.
- Runtime API key/config resolution supports stable behavior across host environments.
- Tool annotations now distinguish read-only and mutating operations.
- Unit tests validate key server behavior and are passing locally.

## What's Left to Build
- Optional migration of markdown-heavy tool outputs to richer structured output schemas.
- Broader integration test coverage around transaction update edge-cases.
- CI automation for lint/test enforcement on pull requests (if not already configured).

## Known Issues and Limitations
- YNAB integration tests depend on external credentials and API availability.
- Current tool responses are mostly markdown, which may limit fully structured client UX.
- Some repository convention files may still need policy decisions for long-term ownership.

## Evolution of Project Decisions
The project moved from minimal MCP exposure toward stronger protocol hygiene: explicit tool metadata, safer runtime config handling, and tighter tests around mutation paths. Review feedback further reinforced preserving transaction state during recategorization and improving repository onboarding/security documentation.
