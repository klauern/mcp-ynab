"""YNAB API client wrapper and config-directory resolution.

`_get_client` builds a `Configuration` from `YNAB_API_KEY` and returns the raw
synchronous `ApiClient`. `AsyncYNABClient` is a thin async-context-manager
wrapper around it so callers can use `async with` ergonomics. `_resolve_config_dir`
chooses where file-backed state (preferred budget, category cache) lives, honoring
`XDG_CONFIG_HOME`.
"""

import os
from pathlib import Path
from typing import Optional

from ynab.api_client import ApiClient
from ynab.configuration import Configuration


async def _get_client() -> ApiClient:
    """Get a configured YNAB API client. Reads API key from environment variables."""
    ynab_api_key = os.getenv("YNAB_API_KEY")
    if not ynab_api_key:
        raise ValueError("YNAB_API_KEY not found in environment variables")
    configuration = Configuration(access_token=ynab_api_key)
    return ApiClient(configuration)


def _resolve_config_dir(config_dir: Optional[Path] = None) -> Path:
    """Resolve and create config directory."""
    if config_dir is None:
        xdg_config_home = os.getenv("XDG_CONFIG_HOME")
        if xdg_config_home:
            base_dir = Path(xdg_config_home)
        else:
            base_dir = Path.home() / ".config"
        config_dir = base_dir / "mcp-ynab"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


class AsyncYNABClient:
    """Async context manager wrapping the synchronous YNAB ApiClient."""

    def __init__(self):
        """Initialize with no underlying client; created on `__aenter__`."""
        self.client: Optional[ApiClient] = None

    async def __aenter__(self) -> ApiClient:
        """Create and return the YNAB ApiClient."""
        self.client = await _get_client()
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """No-op exit — see class docstring; ApiClient has no cleanup."""
        # The underlying ynab.ApiClient.__exit__ is itself a no-op — the SDK
        # uses a shared urllib3 pool manager with no per-client cleanup. We
        # keep the async context manager so callers can use `async with` and
        # so we have a hook if a future SDK version introduces real cleanup.
        return None


async def get_ynab_client() -> AsyncYNABClient:
    """Get an async YNAB client context manager."""
    return AsyncYNABClient()
