# LLM evals

These exercise the server the way a real client does: launch `mcp-ynab` over
the MCP **stdio** transport, let **Claude** use the **Code Mode** surface
(`search` + `execute`, with YNAB reached as `ynab.read.*` / `ynab.write.*`
Python), and check the result.

They always run against the **development** server in the current venv (the
editable install on your checked-out branch) — not a globally installed
`mcp-ynab` — so this is how you validate changes before shipping.

## Prerequisites

1. Install dev deps (adds `anthropic`): `task deps` (or `uv sync`).
2. Provide both keys, in your shell or a `.env` at the repo root:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   YNAB_API_KEY=...        # the budget this key points at is what gets read
   ```

Without the keys, the eval tests **skip** (they never fail for missing config).

## Safety

Scope is **read-only + dry-run**. The server defaults to
`code_mode_mutations_enabled=False`, so `ynab.write.*` is blocked by the runner
(AST audit + fail-closed dispatch) and hidden from the Code Mode spec. Nothing
these evals do can change your budget. (To validate a *write* end-to-end you'd
need a separate, explicitly opted-in eval — see issue `mcp-ynab-ala`.)

## Run the eval set

```bash
task test:eval
# or directly:
uv run pytest -m eval
# one case:
uv run pytest -m eval -k "Dining Spend"
```

`task test:integration` runs the regular integration tests and **excludes**
these (they cost API tokens).

## Run a single ad-hoc prompt

```bash
uv run python evals/run_prompt.py "How much did I spend on dining last month?"
uv run python evals/run_prompt.py --model claude-opus-4-8 "Which categories are over budget?"
```

Prints the Code Mode tool calls (including the Python sent to `execute`) and the
final answer.

## Knobs

| Env var            | Default                          | Purpose                                  |
| ------------------ | -------------------------------- | ---------------------------------------- |
| `EVAL_MODEL`       | `claude-sonnet-4-6`              | Model that drives the tools.             |
| `EVAL_JUDGE_MODEL` | `claude-haiku-4-5-20251001`      | Model that scores answers (test suite).  |
| `EVAL_MCP_COMMAND` | `<current python>`               | Server launch command.                   |
| `EVAL_MCP_ARGS`    | `["-m", "mcp_ynab"]`             | Server launch args (JSON list).          |

To eval the **installed** tool instead of the dev tree:

```bash
EVAL_MCP_COMMAND=mcp-ynab EVAL_MCP_ARGS='[]' uv run pytest -m eval
```

## Adding evals

Add a case to `evals.json`. `read` tasks fetch and report data; set
`expected_code_refs` to the `ynab.read.*` op(s) you expect in the executed code
(any one is enough). `mutation` tasks are dry runs scored by the LLM judge only
(write ops are hidden read-only, so there's nothing to assert structurally).
