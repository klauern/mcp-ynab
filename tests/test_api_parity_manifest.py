"""Tests for the checked-in OpenAPI 1.86 parity manifest."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT_PATH = ROOT / "scripts" / "generate_api_parity_manifest.py"
MANIFEST_PATH = ROOT / "docs" / "api-parity-manifest.json"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_api_parity_manifest", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()


def _source(*operations: tuple[str, str, str, str], version: str = "1.86.0") -> str:
    lines = ["openapi: 3.1.1", "info:", f"  version: {version}", "paths:"]
    for path, method, section, operation_id in operations:
        lines.extend(
            [
                f"  {path}:",
                f"    {method}:",
                "      tags:",
                f"        - {section}",
                f"      operationId: {operation_id}",
                "      responses: {}",
            ]
        )
    return "\n".join(lines) + "\n"


def test_checked_in_manifest_accounts_for_openapi_186() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    operations = manifest["operations"]

    assert manifest["source"]["url"] == generator.SPEC_URL
    assert manifest["source"]["version"] == "1.86.0"
    assert manifest["operation_count"] == 44
    assert len(operations) == 44
    assert len({item["operation_id"] for item in operations}) == 44
    assert len({item["canonical_tool"] for item in operations}) == 44
    assert operations == sorted(operations, key=lambda item: item["operation_id"])
    assert {item["classification"] for item in operations} == {"read", "write"}

    implemented = {
        item["canonical_tool"]
        for item in operations
        if item["implementation_status"] == "implemented"
    }
    assert implemented == set(generator.IMPLEMENTATION_STATUS_BY_TOOL)
    assert {item["implementation_status"] for item in operations} == {
        "implemented",
        "planned",
    }


def test_generation_is_deterministic() -> None:
    source = _source(
        ("/plans", "get", "Plans", "getPlans"),
        ("/plans/{plan_id}", "get", "Plans", "getPlanById"),
    )
    kwargs = {
        "expected_count": 2,
        "implementation_status_by_tool": {"api_get_plans": "implemented"},
    }

    first = generator.render_manifest(generator.build_manifest(source, **kwargs))
    second = generator.render_manifest(generator.build_manifest(source, **kwargs))

    assert first == second
    assert first.endswith("\n")


def test_duplicate_operation_ids_are_rejected() -> None:
    source = _source(
        ("/plans", "get", "Plans", "getPlans"),
        ("/other-plans", "get", "Plans", "getPlans"),
    )

    with pytest.raises(generator.ManifestError, match="Duplicate operationId"):
        generator.build_manifest(
            source,
            expected_count=2,
            implementation_status_by_tool={},
        )


def test_duplicate_canonical_tool_names_are_rejected() -> None:
    source = _source(
        ("/one", "get", "User", "getABC"),
        ("/two", "get", "User", "getAbc"),
    )

    with pytest.raises(generator.ManifestError, match="Duplicate canonical tool names"):
        generator.build_manifest(
            source,
            expected_count=2,
            implementation_status_by_tool={},
        )


def test_unknown_implementation_mapping_is_rejected() -> None:
    source = _source(("/plans", "get", "Plans", "getPlans"))

    with pytest.raises(generator.ManifestError, match="Unknown implementation tool mappings"):
        generator.build_manifest(
            source,
            expected_count=1,
            implementation_status_by_tool={"api_invented_operation": "implemented"},
        )


def test_spec_version_drift_is_rejected() -> None:
    source = _source(("/plans", "get", "Plans", "getPlans"), version="1.87.0")

    with pytest.raises(generator.ManifestError, match="Expected OpenAPI 1.86.0"):
        generator.build_manifest(
            source,
            expected_count=1,
            implementation_status_by_tool={},
        )
