"""mcp-ynab — Model Context Protocol server for the YNAB API.

Exposes a CLI entry point (`mcp-ynab`) that runs the FastMCP server defined in
`mcp_ynab.server`.
"""

import argparse
from importlib.metadata import PackageNotFoundError, version
import signal
import sys
from typing import NoReturn

from dotenv import load_dotenv

from .server import mcp

__version__: str
try:
    __version__ = version("mcp-ynab")
except PackageNotFoundError:
    __version__ = "0+unknown"


def handle_sigint(signum, frame):
    """Handle SIGINT (Ctrl+C) gracefully."""
    print("\nReceived SIGINT. Shutting down...", file=sys.stderr)
    sys.exit(0)


def main() -> NoReturn:
    """Entry point for the YNAB MCP server."""
    parser = argparse.ArgumentParser(description="YNAB (You Need A Budget) API integration for MCP")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    # Load environment variables from .env file
    load_dotenv()

    # Set up signal handling
    signal.signal(signal.SIGINT, handle_sigint)

    # Run the MCP server
    try:
        if args.debug:
            print("Starting YNAB MCP server in debug mode...", file=sys.stderr)
        mcp.run()
        sys.exit(0)  # This line will never be reached due to mcp.run() being blocking
    except KeyboardInterrupt:
        print("\nShutting down...", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
