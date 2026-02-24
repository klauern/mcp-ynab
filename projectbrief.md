# Project Brief: MCP YNAB

## Overview
MCP YNAB is a Python MCP server that exposes YNAB budgeting data and transaction operations to MCP clients. The project goal is to provide reliable, typed, and auditable tools for reading budget state and performing scoped write actions such as creating and categorizing transactions.

## Core Requirements
- Expose stable MCP resources and tools for YNAB account, category, and transaction workflows.
- Keep authentication and local cache behavior predictable across CLI and MCP host environments.
- Return clear, human-readable outputs while preserving compatibility with MCP tool metadata.
- Maintain deterministic local development with `uv`, `task`, `ruff`, and `pytest`.

## Goals
- Keep all default unit checks green: lint and tests must pass on each merge.
- Minimize accidental destructive behavior by correctly annotating read-only vs mutating tools.
- Ensure recategorization and transaction updates do not accidentally drop existing transaction state.
- Keep project documentation aligned with actual behavior and commands.

## Project Scope
In scope:
- MCP server implementation in `src/mcp_ynab/`.
- Project packaging, developer tooling, and repository docs.
- Unit and integration test harness for YNAB API interactions.

Out of scope:
- Building a separate web UI.
- Replacing the upstream YNAB SDK.
- Non-YNAB finance providers.
