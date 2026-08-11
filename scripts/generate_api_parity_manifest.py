#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Generate the checked-in YNAB OpenAPI parity manifest.

The official specification is YAML, but this script deliberately avoids a YAML
runtime dependency.  It reads only the stable OpenAPI structure needed for the
manifest: ``info.version`` and each path operation's tag and ``operationId``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.request import urlopen

SPEC_URL = "https://api.ynab.com/papi/open_api_spec.yaml"
SPEC_VERSION = "1.86.0"
EXPECTED_OPERATION_COUNT = 44
DEFAULT_OUTPUT = Path("docs/api-parity-manifest.json")
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
READ_METHODS = frozenset({"get"})
IMPLEMENTATION_STATUS_BY_TOOL = {
    "api_get_transactions": "implemented",
    "api_update_transaction": "implemented",
}

_PATH_RE = re.compile(r"^  (/[^:]*):\s*$")
_METHOD_RE = re.compile(r"^    (get|post|put|patch|delete):\s*$")
_VERSION_RE = re.compile(r"^  version:\s*(.+?)\s*$")
_OPERATION_ID_RE = re.compile(r"^      operationId:\s*(.+?)\s*$")
_TAGS_RE = re.compile(r"^      tags:\s*$")
_TAG_RE = re.compile(r"^        -\s+(.+?)\s*$")


class ManifestError(ValueError):
    """Raised when the source spec cannot produce a trustworthy manifest."""


def _unquote(value: str) -> str:
    """Remove the simple single or double quotes used by YAML scalars."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def camel_to_snake(value: str) -> str:
    """Convert an OpenAPI lower-camel operation ID to the SDK's snake case."""
    with_word_boundaries = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", with_word_boundaries).lower()


def parse_openapi_operations(source: str) -> tuple[str, list[dict[str, str]]]:
    """Extract the spec version and operation metadata from official YAML."""
    version: str | None = None
    path: str | None = None
    method: str | None = None
    operation_id: str | None = None
    tags: list[str] = []
    reading_tags = False
    operations: list[dict[str, str]] = []

    def finish_operation() -> None:
        nonlocal method, operation_id, tags, reading_tags
        if method is None:
            return
        if path is None or operation_id is None:
            raise ManifestError(f"{method.upper()} {path or '<unknown path>'} has no operationId")
        if len(tags) != 1:
            raise ManifestError(
                f"{operation_id} must have exactly one section tag; found {tags or 'none'}"
            )
        operations.append(
            {
                "section": tags[0],
                "method": method.upper(),
                "path": path,
                "operation_id": operation_id,
            }
        )
        method = None
        operation_id = None
        tags = []
        reading_tags = False

    for line in source.splitlines():
        if version is None and (match := _VERSION_RE.match(line)):
            version = _unquote(match.group(1))
            continue
        if match := _PATH_RE.match(line):
            finish_operation()
            path = match.group(1)
            continue
        if path is not None and (match := _METHOD_RE.match(line)):
            finish_operation()
            method = match.group(1)
            if method not in HTTP_METHODS:  # pragma: no cover - constrained by the regex
                raise ManifestError(f"Unsupported HTTP method: {method}")
            continue
        if method is None:
            continue
        if _TAGS_RE.match(line):
            reading_tags = True
            continue
        if reading_tags and (match := _TAG_RE.match(line)):
            tags.append(_unquote(match.group(1)))
            continue
        if match := _OPERATION_ID_RE.match(line):
            operation_id = _unquote(match.group(1))
        if line.startswith("      ") and not line.startswith("        "):
            reading_tags = False

    finish_operation()
    if version is None:
        raise ManifestError("OpenAPI source has no info.version")
    return version, operations


def build_manifest(
    source: str,
    *,
    source_url: str = SPEC_URL,
    expected_version: str = SPEC_VERSION,
    expected_count: int = EXPECTED_OPERATION_COUNT,
    implementation_status_by_tool: Mapping[str, str] = IMPLEMENTATION_STATUS_BY_TOOL,
) -> dict[str, Any]:
    """Build and validate the deterministic manifest data."""
    version, parsed_operations = parse_openapi_operations(source)
    if version != expected_version:
        raise ManifestError(
            f"Expected OpenAPI {expected_version}, but the source reports {version}. "
            "Review the new spec before changing the pinned version."
        )
    if len(parsed_operations) != expected_count:
        raise ManifestError(
            f"Expected {expected_count} OpenAPI operations, found {len(parsed_operations)}"
        )

    operation_ids = [operation["operation_id"] for operation in parsed_operations]
    duplicate_ids = sorted({item for item in operation_ids if operation_ids.count(item) > 1})
    if duplicate_ids:
        raise ManifestError(f"Duplicate operationId values: {duplicate_ids}")

    tool_names = [f"api_{camel_to_snake(item)}" for item in operation_ids]
    duplicate_tools = sorted({item for item in tool_names if tool_names.count(item) > 1})
    if duplicate_tools:
        raise ManifestError(f"Duplicate canonical tool names: {duplicate_tools}")

    unknown_mappings = sorted(set(implementation_status_by_tool) - set(tool_names))
    if unknown_mappings:
        raise ManifestError(f"Unknown implementation tool mappings: {unknown_mappings}")

    operations = []
    for parsed, tool_name in zip(parsed_operations, tool_names, strict=True):
        method = parsed["method"]
        operations.append(
            {
                **parsed,
                "canonical_tool": tool_name,
                "classification": "read" if method.lower() in READ_METHODS else "write",
                "implementation_status": implementation_status_by_tool.get(tool_name, "planned"),
            }
        )
    operations.sort(key=lambda item: item["operation_id"])

    return {
        "schema_version": 1,
        "source": {
            "name": "Official YNAB OpenAPI specification",
            "url": source_url,
            "version": version,
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        },
        "generated_by": "scripts/generate_api_parity_manifest.py",
        "operation_count": len(operations),
        "operations": operations,
    }


def render_manifest(manifest: Mapping[str, Any]) -> str:
    """Render stable, review-friendly JSON with a trailing newline."""
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def load_source(location: str) -> str:
    """Load an OpenAPI source from HTTPS or a local path."""
    if location.startswith(("https://", "http://")):
        with urlopen(location, timeout=30) as response:  # noqa: S310 - explicit CLI source
            return response.read().decode("utf-8")
    return Path(location).read_text(encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SPEC_URL, help="Official spec URL or local YAML path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the checked-in manifest differs instead of rewriting it",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Generate or verify the checked-in manifest."""
    args = parse_args(argv)
    try:
        source = load_source(args.source)
        rendered = render_manifest(build_manifest(source, source_url=args.source))
    except (ManifestError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        try:
            current = args.output.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"error: missing manifest: {args.output}", file=sys.stderr)
            return 1
        if current != rendered:
            print(
                f"error: {args.output} is stale; regenerate with `task parity:manifest`",
                file=sys.stderr,
            )
            return 1
        print(f"Manifest is current: {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output} ({EXPECTED_OPERATION_COUNT} operations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
