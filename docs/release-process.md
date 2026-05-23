# Release Process

This repository uses the PR labels `patch`, `minor`, and `major` as the release contract.

## Pull Requests

- PRs that change release-relevant files are automatically labeled `patch`.
- Manually applying `minor` or `major` removes `patch`.
- If both `minor` and `major` are present, `major` wins.
- PRs without one of these labels do not trigger a release when merged.

Release-relevant files are currently:

- `src/**`
- `tests/**`
- `pyproject.toml`
- `uv.lock`
- `Taskfile.yml`

## Merged PRs

When a labeled PR is merged into `main`, the release workflow:

1. Determines the bump level from the merged PR labels.
2. Updates the static project version in `pyproject.toml`.
3. Updates `uv.lock`.
4. Commits the version bump to `main`.
5. Runs linting, formatting checks, docstring coverage, and unit tests.
6. Builds the package.
7. Tags the version as `vX.Y.Z`.
8. Creates a GitHub release with the built distributions attached.
9. Publishes to PyPI with trusted publishing.

PyPI publishing requires a trusted publisher configured for this repository and the
`.github/workflows/release.yml` workflow.
