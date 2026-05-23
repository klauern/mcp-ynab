# YNAB Code Mode

Code Mode lets an MCP client call one tool, `execute`, with a short
Python snippet instead of chaining many YNAB tools manually. The snippet runs as
the body of an async function and receives a small `ynab` namespace:

- `ynab.read.*` contains read-only YNAB tools.
- `ynab.write.*` contains mutating YNAB tools when mutation mode is enabled.
- `LIMIT` is available as a default cap for small result sets.

Code Mode is opt-in. It is useful for multi-step read, cleanup, and batch
workflows where the agent needs loops, filtering, grouping, or conditional
updates.

## Configure it

Code Mode is enabled by default. If you need to disable it or customize its behavior,
use the `set_preference` MCP tool:

```text
set_preference(name="code_mode_enabled", value="false")
```

To enable read-only snippets (default when enabled): Mutating snippets require a separate opt-in:

```text
set_preference(name="code_mode_mutations_enabled", value="true")
```

The direct tool surface can also be hidden after Code Mode is enabled:

```text
set_preference(name="code_mode_replace_tools", value="true")
```

When `code_mode_replace_tools` is true, the public MCP tool list keeps only the
bootstrap tools plus `search` and `execute`. The internal registry is still present
so Code Mode can call the underlying tools.

Related preferences:

- `code_mode_timeout_s`: maximum execution time, default `10.0`, capped at 60
  seconds by the preferences model.
- `code_mode_max_output_chars`: maximum captured `print()` output and returned
  result preview, default `8192`.

## Discover the API

Clients should read these resources before generating snippets:

- `ynab://code-mode/stubs`: generated Python type stubs for the current
  `ynab.read` and `ynab.write` namespaces.
- `ynab://code-mode/examples`: curated snippets for common workflows.

The stubs are generated from the FastMCP tool registry. If a new tool is added
and registered with the server, it appears automatically in the relevant
namespace unless it is `execute` itself.

## Write snippets

Pass only the function body to `execute`. Use `await` directly and
return JSON-serializable values when possible:

```python
budgets = await ynab.read.get_budgets()

rows = []
for budget in budgets[:LIMIT]:
    rows.append({"id": budget.id, "name": budget.name})

print("found", len(budgets), "budgets")
return rows
```

The tool returns a structured dictionary:

```json
{
  "ok": true,
  "result": [],
  "logs": "",
  "error": null,
  "traceback": null,
  "truncated": false
}
```

If Code Mode is disabled, `execute` returns `ok=false` with
`error="code_mode_disabled"`. If a snippet calls `ynab.write.*` while mutation
mode is disabled, it returns `ok=false` with `error="mutations_disabled"`.

## Wire in a new tool

No Code Mode-specific registration is needed for ordinary tools.

1. Add the tool with the normal `@mcp.tool(...)` decorator.
2. Use `READ_ONLY_TOOL` for read-only tools and a mutating annotation for tools
   that change YNAB state.
3. Import the module from `src/mcp_ynab/server.py` so the decorator runs during
   server startup.
4. Add focused tests for the tool itself.
5. Run `generate_stubs(...)` or read `ynab://code-mode/stubs` to confirm the
   new function appears under `ynab.read` or `ynab.write`.

Code Mode classifies tools by annotation. Tools with `readOnlyHint=True` are
placed under `ynab.read`; all others are placed under `ynab.write`.

## Wire in a client

A client that prefers Code Mode should:

1. Read `ynab://code-mode/stubs` and cache it for the current server version.
2. Optionally read `ynab://code-mode/examples` for local prompting examples.
3. Generate a Python function body that uses only `ynab.read.*` unless the user
   has explicitly approved mutation mode.
4. Call `execute(code=...)`.
5. Inspect `ok`, `error`, `logs`, and `truncated` before acting on `result`.

For write workflows, first do a read-only discovery snippet and show the planned
changes to the user. Then run a second snippet that calls `ynab.write.*` only
after mutation mode and any product-level confirmation flow are enabled.

## Runner limits

The current runner is in-process and intentionally small. It blocks imports,
common dynamic execution escape hatches, dunder access, f-strings, and
`with`/`async with` blocks. It runs with a limited builtins allow-list and a soft `asyncio.wait_for`
timeout. **This timeout is cooperative and cannot interrupt synchronous
blocking or CPU-bound code** (e.g. `time.sleep`, tight loops). Blocking
user code will stall the server until the event loop regains control.
See issue `mcp-ynab-fkv` for the tracked fix.

This is defense in depth, not a Python security boundary. Enable Code Mode only
for trusted MCP clients and trusted prompt sources. Do not treat it as a
sandbox for arbitrary user-supplied Python.
