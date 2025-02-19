import argparse
import signal
import sys
from typing import NoReturn

from dotenv import load_dotenv

from .server import mcp


def handle_sigint(signum, frame):
    """Handle SIGINT (Ctrl+C) gracefully."""
    print("\nReceived SIGINT. Shutting down...", file=sys.stderr)
    sys.exit(0)


def main() -> NoReturn:
    """Entry point for the YNAB MCP server."""
    parser = argparse.ArgumentParser(description="YNAB (You Need A Budget) API integration for MCP")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
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
