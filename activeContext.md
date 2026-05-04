# Active Context: MCP YNAB

## Current Work Focus
Finalize PR feedback resolution for the modernization branch, including performance/safety fixes in transaction categorization and repository documentation quality updates.

## Recent Changes
- Upgraded MCP dependency range and refreshed lockfile.
- Added MCP tool annotations and fixed accidental tool export.
- Refactored runtime config/API-key lookup behavior.
- Replaced placeholder server tests with behavior-oriented tests.

## Next Steps
- Keep PR checks green after review-comment remediations.
- Decide whether memory-bank documents should remain maintained or be moved to templates.
- Optionally split future repository-convention changes from server behavior changes.

## Active Decisions and Considerations
- Preserve transaction state fields during category updates to avoid accidental data loss.
- Prefer direct transaction lookup for ID-based recategorization to reduce API load.
- Keep this repository focused on MCP-YNAB behavior with explicit boundaries.

## Important Patterns and Preferences
- Prefer deterministic unit tests with monkeypatched SDK calls over brittle fixture scaffolding.
- Keep docs synchronized with implemented resources/tools and supported commands.
- Use runtime (not import-time) configuration resolution for better testability and host compatibility.

## Learnings and Project Insights
- Minor API usage choices (bulk scan vs direct fetch) can materially affect correctness and efficiency.
- PRs that mix infra/docs and functional changes benefit from clear scoping notes.
- Tool metadata (annotations) improves downstream client behavior and safety expectations.
