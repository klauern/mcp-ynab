# Tech Context: MCP YNAB

## Technologies Used
- Python 3.12+
- FastMCP (`mcp[cli]`)
- YNAB Python SDK (`ynab`)
- Pydantic v2
- Pytest + pytest-asyncio
- Ruff

## Development Setup
- Install deps: `uv sync`
- Editable install: `uv pip install -e .`
- Configure `YNAB_API_KEY` via env var or `.env`
- Run dev server: `task dev` or `uv run mcp dev src/mcp_ynab/server.py`

## Technical Constraints
- YNAB SDK is synchronous, so async tool functions wrap sync SDK calls.
- API key must be available at runtime in the process environment.
- Local cache and preference files are limited to JSON-based persistence.

## Dependencies
- Runtime: `mcp[cli]>=1.20.0,<2.0.0`, `pydantic>=2.10.0,<3`, `ynab>=1.0.1`, `python-dotenv>=1.0.0`
- Dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`

## Tool Usage Patterns
- Format and lint via `task fmt` (`ruff format` + `ruff check --fix`).
- Unit tests via `task test` (integration excluded by default marker config).
- Integration tests are explicit (`task test:integration`) and require real YNAB credentials.
