"""Integration tests for mutating MCP tools.

These tests are skipped by default. To run them, set
``YNAB_INTEGRATION_ALLOW_WRITES=1`` in the environment. The tests deliberately
do not include destructive scenarios — they exist to prove the write tools wire
up correctly end-to-end against a real budget.

NOTE: Today there are no actual mutation assertions here; we keep this module
as a documented opt-in surface for future careful additions. Add a write test
only when you have a sandbox budget you don't mind affecting.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_writes_opt_in_pattern_documented(require_writes_opt_in: None) -> None:
    """Placeholder verifying the opt-in fixture skips correctly when unset.

    When `YNAB_INTEGRATION_ALLOW_WRITES=1` is set, this test will run as a
    no-op pass — proving the gate works. Real write-through tests should be
    added here only after a sandbox budget is designated.
    """
    assert True
