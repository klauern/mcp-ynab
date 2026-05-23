"""Tests for release version helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_bump_version_module():
    script_path = Path(__file__).parents[1] / ".github" / "scripts" / "bump_version.py"
    spec = importlib.util.spec_from_file_location("bump_version", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bump_version_levels():
    module = load_bump_version_module()

    assert module.bump("2.3.4", "patch") == "2.3.5"
    assert module.bump("2.3.4", "minor") == "2.4.0"
    assert module.bump("2.3.4", "major") == "3.0.0"


def test_bump_pyproject_updates_static_project_version(tmp_path):
    module = load_bump_version_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\nversion = "2.3.4"\n')

    current_version, next_version = module.bump_pyproject(pyproject, "minor")

    assert current_version == "2.3.4"
    assert next_version == "2.4.0"
    assert 'version = "2.4.0"' in pyproject.read_text()
