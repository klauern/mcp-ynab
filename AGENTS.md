# Claude Configuration

## Project Overview
This is an MCP (Model Context Protocol) server for YNAB (You Need A Budget) integration, built with Python.

## Development Commands
- **Build**: `task install` (production) or `task dev` (development with browser)
- **Lint/Format**: `task fmt` (runs ruff format and ruff check with --fix)
- **Tests**:
  - All excluding integration: `pytest` or `task test`
  - Single test: `pytest tests/test_server.py::test_name`
  - Integration tests: `pytest -m "integration"` or `task test:integration`
  - With coverage: `task coverage`
- **Dependencies**: `task deps` (uses uv sync)

## Code Style Guidelines
- Python 3.12+
- Line length: 100 characters
- Formatting: ruff format (Black-compatible)
- Linting: ruff check
- Imports: standard library first, third-party second, local modules last
- Types: Use type hints consistently with modern Python typing
- Testing: pytest with pytest-asyncio for async tests
- Error handling: Use proper exception handling with specific exceptions

## Project Structure
- `src/mcp_ynab/` - Main package source code
- `tests/` - Test directory with pytest fixtures in conftest.py
- Task definitions in Taskfile.yml (use `uv` for Python package management)
- MCP server implementation following modelcontextprotocol.io guidelines

## Instructions for Claude
When working on this MCP-YNAB project:
1. Follow the established code style and testing patterns
2. Use the Task commands for build, test, and dependency management
3. Implement MCP server functionality according to the modelcontextprotocol.io guidelines
4. Ensure all async code uses pytest-asyncio compatible patterns
5. Write comprehensive tests for new functionality

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
