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