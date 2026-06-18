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
   ANTHROPIC_API_KEY=sk-ant-api...   # a Console API key
   YNAB_API_KEY=...                  # the budget this key points at is what gets read
   ```

Without the keys, the eval tests **skip** (they never fail for missing config).

> **Heads-up — Claude Code users:** if your shell exports `ANTHROPIC_API_KEY`
> as a Claude Code **OAuth token** (`sk-ant-oat...`), the Messages API rejects
> it with `401 invalid x-api-key`. Don't fight your shell — set a dedicated
> Console key that takes precedence and leaves Claude Code alone:
> ```
> export EVAL_ANTHROPIC_API_KEY=sk-ant-api...
> ```
> `EVAL_ANTHROPIC_API_KEY` wins over `ANTHROPIC_API_KEY` for the evals only.

## Two drivers (`EVAL_DRIVER`)

The eval can be driven two ways:

| `EVAL_DRIVER`            | Auth                                   | Cost                                   |
| ------------------------ | -------------------------------------- | -------------------------------------- |
| `messages-api` (default) | Anthropic **Console API key**          | Bills API tokens per run               |
| `agent-sdk`              | Your **Claude subscription** (Claude Agent SDK) | Draws against your subscription usage; no API balance |

Both run the same `evals.json`, the same structural checks, and the same LLM
judge — only the engine and billing differ.

### Use your Claude subscription (no API credits)

The `agent-sdk` driver uses the **Claude Agent SDK** (the Claude Code engine),
authenticated by your Claude subscription — so it doesn't spend API credits.

```bash
# Make sure you're logged in to Claude Code (one-time):
claude login          # or: export EVAL_CLAUDE_CODE_OAUTH_TOKEN=$(claude setup-token)

export EVAL_DRIVER=agent-sdk
uv run python evals/run_prompt.py "How much did I spend on dining last month?"
task test:eval        # judge runs on the subscription too
```

Notes:
- Needs the `claude` CLI installed and logged in (you already use it). The driver
  blanks out `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `CLAUDE_CODE_USE_BEDROCK`
  for the subprocess so the subscription login is what's used — your shell's
  `ANTHROPIC_API_KEY` (a Claude Code OAuth token) won't get in the way.
- Subscription auth is intended for **interactive/personal** use and draws against
  your plan's usage limits — not a separate API balance. Fine for your own evals;
  don't ship a shared product on it.
- Only `YNAB_API_KEY` is required for this driver (no Anthropic API key).

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

| Env var                       | Default                     | Purpose                                          |
| ----------------------------- | --------------------------- | ------------------------------------------------ |
| `EVAL_DRIVER`                 | `messages-api`              | `messages-api` (API key) or `agent-sdk` (subscription). |
| `EVAL_ANTHROPIC_API_KEY`      | falls back to `ANTHROPIC_API_KEY` | Console key for the `messages-api` driver; wins over the shell var. |
| `EVAL_CLAUDE_CODE_OAUTH_TOKEN`| falls back to `claude` login | `agent-sdk` subscription token (`claude setup-token`). |
| `EVAL_MODEL`                  | `claude-sonnet-4-6`         | Model that drives the tools.                     |
| `EVAL_JUDGE_MODEL`            | `claude-haiku-4-5-20251001` | Model that scores answers (test suite).          |
| `EVAL_MAX_TURNS`              | `12`                        | Agent-SDK driver: max agent turns per prompt.    |
| `EVAL_MCP_COMMAND`            | `<current python>`          | Server launch command.                           |
| `EVAL_MCP_ARGS`               | `["-m", "mcp_ynab"]`        | Server launch args (JSON list).                  |

To eval the **installed** tool instead of the dev tree:

```bash
EVAL_MCP_COMMAND=mcp-ynab EVAL_MCP_ARGS='[]' uv run pytest -m eval
```

## Adding evals

Add a case to `evals.json`. `read` tasks fetch and report data; set
`expected_code_refs` to the `ynab.read.*` op(s) you expect in the executed code
(any one is enough). `mutation` tasks are dry runs scored by the LLM judge only
(write ops are hidden read-only, so there's nothing to assert structurally).
