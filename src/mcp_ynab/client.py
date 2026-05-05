"""YNAB API client wrapper and config-directory resolution.

`_get_client` builds a `Configuration` from the resolved API key and returns
the raw synchronous `ApiClient`. `AsyncYNABClient` is a thin async-context-manager
wrapper around it so callers can use `async with` ergonomics. `_resolve_config_dir`
chooses where file-backed state (preferred budget, category cache) lives, honoring
`XDG_CONFIG_HOME`.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from ynab.api_client import ApiClient
from ynab.configuration import Configuration

logger = logging.getLogger(__name__)

# Keychain identifiers for storing the YNAB personal access token.
KEYRING_SERVICE = "mcp-ynab"
KEYRING_USERNAME = "YNAB_API_KEY"


def _resolve_api_key() -> Optional[str]:
    """Return the YNAB API key, or None if no source has one.

    Resolution order: ``YNAB_API_KEY`` env var, then OS keychain. Env wins so
    CI runs and ad-hoc shell overrides remain easy; the keychain is durable
    storage for desktop users who don't want a shell-profile entry.

    Keyring backend errors (headless Linux without a secret-service daemon,
    locked-down Docker images, etc.) are swallowed and logged at debug — they
    must not break the env-only path.
    """
    env_key = os.getenv("YNAB_API_KEY")
    if env_key:
        return env_key

    try:
        import keyring

        stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if stored:
            return stored
    except Exception as exc:  # noqa: BLE001 — keyring backends raise varied errors
        logger.debug("Keyring lookup failed; falling through: %s", exc)

    return None


def _store_api_key(api_key: str) -> None:
    """Persist ``api_key`` to the OS keychain. Raises on backend failure."""
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)


def _delete_stored_api_key() -> bool:
    """Remove a previously stored key from the keychain. Returns True if removed."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Keyring delete failed: %s", exc)
        return False


async def _get_client() -> ApiClient:
    """Get a configured YNAB API client using the resolved API key."""
    ynab_api_key = _resolve_api_key()
    if not ynab_api_key:
        raise ValueError(
            "YNAB_API_KEY not found. Set the YNAB_API_KEY environment variable "
            "or store it in the OS keychain via the set_api_key tool."
        )
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
