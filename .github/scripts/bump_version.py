#!/usr/bin/env python3
"""Bump the project version in pyproject.toml."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


VERSION_RE = re.compile(
    r'^(version\s*=\s*)"'
    r"(?P<version>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r'"$',
    re.MULTILINE,
)


def bump(version: str, level: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unsupported bump level: {level}")


def bump_pyproject(path: Path, level: str) -> tuple[str, str]:
    content = path.read_text()
    match = VERSION_RE.search(content)
    if match is None:
        raise RuntimeError(f"Could not find a static project version in {path}")

    current_version = ".".join([match.group("version"), match.group("minor"), match.group("patch")])
    next_version = bump(current_version, level)
    updated = VERSION_RE.sub(rf'\g<1>"{next_version}"', content, count=1)
    path.write_text(updated)
    return current_version, next_version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", choices=["patch", "minor", "major"])
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args()

    current_version, next_version = bump_pyproject(args.pyproject, args.level)
    print(f"current_version={current_version}")
    print(f"next_version={next_version}")

    github_output = Path.cwd() / "github_output"
    output_path = Path(__import__("os").environ.get("GITHUB_OUTPUT", github_output))
    with output_path.open("a") as output:
        output.write(f"current_version={current_version}\n")
        output.write(f"next_version={next_version}\n")
        output.write(f"tag=v{next_version}\n")


if __name__ == "__main__":
    main()
