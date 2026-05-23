# Code Mode for mcp-ynab - Working Notebook

> **Status:** MVP implementation in progress.
> **Date:** 2026-05-22
> **Author:** Claude (Opus 4.7) for @klauern
> **Inspired by:** Cloudflare's "Code Mode" series and the official `@cloudflare/codemode` package.

---

## 0. Notebook status

This document started as the pre-implementation research plan. Keep the research
sections below as design context, but treat this top section as the current
working notebook for the branch.

### Implemented in the MVP branch

- Code Mode preferences exist: `code_mode_enabled`,
  `code_mode_mutations_enabled`, `code_mode_replace_tools`,
  `code_mode_timeout_s`, and `code_mode_max_output_chars`.
- `execute` is registered as the single execution tool.
- The runner executes a Python snippet body under an in-process AST audit,
  captures stdout, enforces a timeout, and returns a structured result.
- The tool proxy exposes the current namespace shape: `ynab.read.*` for
  read-only tools and `ynab.write.*` for mutating tools.
- `ynab://code-mode/stubs` generates Python stubs from the current FastMCP
  registry.
- `ynab://code-mode/examples` serves curated examples from
  `docs/code-mode-examples.md`.
- `code_mode_replace_tools=true` filters the external `list_tools` and direct
  `call_tool` surface down to Code Mode plus bootstrap helpers while leaving
  the internal registry intact for the proxy.

### Still open

- Runner hardening: the in-process audit is still convenience gating, not a
  security boundary. Subprocess isolation and OS resource limits remain future
  work.
- Replacement-mode policy: the visible bootstrap tool set may need adjustment
  after real client usage. Current visible tools are `execute`,
  `get_preferences`, `set_preference`, `set_api_key`, `clear_api_key`,
  `set_preferred_budget_id`, `get_budgets`, and `ping`.
- Stub quality: generated return types and model details are useful enough for
  discovery, but not yet a complete SDK reference.
- More examples: add workflow-specific snippets as real usage uncovers the
  best patterns.

### Verification status

- Focused tests cover tool/resource registration, runner behavior, preferences,
  and the examples resource namespace style.
- Latest local verification for the examples/notebook update:
  - `task fmt`
  - `uv run pytest tests/test_server.py::test_resource_and_templates_are_exposed tests/test_server.py::test_code_mode_examples_resource_uses_current_namespaces`
- Latest local verification for runner hardening:
  - `task fmt`
  - `uv run pytest tests/test_code_mode.py`
- Latest full default verification after replacement-mode integration:
  - `task fmt`
  - `task test` (`235 passed, 11 deselected`)

## 1. Executive summary

**Code Mode** is a pattern that swaps a large MCP tool surface for a single `execute` tool plus a typed SDK delivered as a resource. The LLM writes a short async function that calls the SDK; the host sandbox runs it and returns the result. Cloudflare reports **32–99.9% input-token reduction** depending on workload, plus higher reliability for multi-step workflows because the model is writing real code rather than chaining tool calls through its decoder.

For **mcp-ynab** — which currently exposes **33 tools and multiple resources**, many of them composable (`get_transactions` -> `bulk_categorize`, `get_categories` -> `assign_money`, etc.) — Code Mode is a strong fit for the *batch and orchestration* class of YNAB workflows. The MVP keeps Code Mode opt-in, builds on the existing `Preferences` subsystem, and separates read/write namespaces so mutation support can be enabled deliberately.

---

## 2. What Cloudflare Code Mode actually is

Two blog posts plus official docs:

| Source | URL | Year | Focus |
| --- | --- | --- | --- |
| "Code Mode: the better way to use MCP" | <https://blog.cloudflare.com/code-mode/> | 2025-09 | General technique, MCP-server-agnostic |
| "Code Mode: give agents an entire API in 1,000 tokens" | <https://blog.cloudflare.com/code-mode-mcp/> | 2026-02 | Applied to Cloudflare's own 2,500-endpoint API |
| Official docs | <https://developers.cloudflare.com/agents/api-reference/codemode/> | live | `@cloudflare/codemode` API reference |

### 2.1 Core insight

> "Making an LLM perform tasks with tool calling is like putting Shakespeare through a month-long class in Mandarin and then asking him to write a play in it." — Cloudflare

LLMs see vastly more code in pretraining than synthetic tool-call traces. Writing code against a typed namespace is therefore both **more reliable** and **more compact** than emitting multiple JSON tool calls.

### 2.2 Mechanism

1. **Type generation.** Tool schemas become TypeScript declarations on a `codemode` global (e.g. `declare const codemode: { toolName: (input) => Promise<...> }`). Doc comments come from `description` fields.
2. **Surface compression.** Instead of N tool descriptors, the MCP server exposes **one or two tools**:
   - `execute(code: string)` — runs an async arrow function
   - (optional) `search(code: string)` — runs a discovery snippet against a spec object, so the full API surface never enters context
3. **Sandboxed runtime.** Cloudflare uses Dynamic Worker Loader to spin a V8 isolate per call. No filesystem, no env vars, `fetch`/`connect` disabled by default.
4. **RPC dispatch.** Inside the sandbox, `codemode.foo(args)` is a proxy that crosses the RPC boundary to the host. The host holds OAuth tokens / API keys and attaches them to outbound calls — credentials never enter the sandbox.
5. **Output capture.** `console.log` lines plus a return value bubble back to the LLM as a single tool-result message.

### 2.3 The "two tools" pattern (code-mode-mcp post)

```jsonc
[
  { "name": "search",  "inputSchema": { "code": "JavaScript async arrow function to search the OpenAPI spec" } },
  { "name": "execute", "inputSchema": { "code": "JavaScript async arrow function to call the API" } }
]
```

The LLM's `search` snippet narrows 2,500 endpoints to a handful before `execute` ever runs. Net context for the agent: **~1,000 tokens** (vs. ~1.17M tokens to inline every endpoint).

### 2.4 Reported numbers

- **32%** token reduction on a simple single-event task
- **81%** reduction on a 31-event batch calendar workflow
- **99.9%** reduction on the full Cloudflare API surface
- One qualitative win: code-mode used `new Date()` correctly while the tool-call agent fabricated a date a year in the past — composition unlocks primitives the tool-call agent doesn't reach for.

### 2.5 Security model

- Per-call isolate (no shared state between executions by default)
- Network: `globalOutbound: null` blocks egress; explicit `Fetcher` binding required to allow specific destinations
- Tool calls travel by Workers RPC, not HTTP — no token leakage path
- Browser executor variant uses iframe + restrictive CSP + nonce-scoped postMessage
- Documented limitation: tool-approval (`needsApproval`) is **not yet supported**

---

## 3. Why this matters for mcp-ynab specifically

Current tool surface (counted from `src/mcp_ynab/tools/*.py`):

| Module | `@mcp.tool` count |
| --- | --- |
| `budgeting.py` | 17 |
| `transactions.py` | 12 |
| `preferences.py` | 4 |
| **Total** | **33** tools + 7 resources |

The transactions surface in particular is **deeply composable**:

- `get_transactions_needing_attention` → `bulk_categorize` (already a batch tool, but the LLM still has to round-trip the filter step)
- `get_payees` → `merge_payees` (requires N name lookups)
- `get_categories` (with envelopes) → `assign_money` / `move_money`
- `spending_by_payee` → `rename_payee` → `bulk_categorize` (a classic three-step cleanup that costs ~3 round-trips today)

Every multi-step YNAB cleanup the user runs today pays the per-step LLM cost. Code Mode collapses these into one tool call whose body is, in many cases, ~15 lines of straight-line code.

Token math for mcp-ynab (rough): the current 33 tool descriptions + parameter schemas sit at **~6–9k tokens** in any session that talks to this server. A Code Mode build would replace that with a single `execute` tool (~200 tokens) plus a once-fetched stubs resource (the user's client may cache it). Conservative estimate: **70–85%** reduction in this server's contribution to system-prompt cost, plus elimination of intermediate tool-result chatter.

---

## 4. Design space (Python edition)

Cloudflare's implementation depends on the Cloudflare Workers runtime: V8 isolates, Worker Loader, Workers RPC. None of that exists in a vanilla Python MCP. We must reproduce the **contract**, not the implementation. Five design axes follow.

### 4.1 Sandbox runtime

| Option | Isolation | Async-tool DX | Startup | Verdict |
| --- | --- | --- | --- | --- |
| In-process compile + restricted-builtins runner + AST whitelist + `asyncio.wait_for` | **Weak** (relies on whitelist) | Native — tools are coroutines | <1 ms | **Best v1**: ship fast, gate behind opt-in pref |
| Subprocess (`uv run python -c …`) with seccomp/`resource.setrlimit`, IPC for tool proxy | Strong (OS-level) | Awkward — async proxy across IPC | ~50–150 ms | Phase 3 hardening target |
| Pyodide (WASM) via `wasmtime-py` or `pyodide.runPython` | Strong (memory-safe sandbox) | Tricky — bridging async + WASI | ~200–500 ms cold | Niche; ignore unless we run in-browser |
| Embedded V8 (`py-mini-racer`, `STPyV8`) — LLM writes JS | Strong | Forces JSON serialization at boundary | ~10–50 ms | Matches Cloudflare exactly; consider only if LLM accuracy on Python proves worse |
| Firecracker / nsjail | Very strong | Heavy ops cost | seconds | Overkill for a local MCP |

**Recommendation:** ship **Phase 1 with an in-process AST-whitelisted runner** (Python `compile()` followed by invocation under a stripped `__builtins__`). Document it as "trusted-LLM, untrusted-prompt" — i.e., it stops accidental footguns and prompt-injected reads of the filesystem, but it is **not** a true security boundary. Phase 3 swaps in a subprocess sandbox once the API has shaken out.

### 4.2 Code language

| Language | Pros | Cons |
| --- | --- | --- |
| **Python** | Matches the SDK; pydantic models pass through unchanged; no marshalling | Slightly less common in LLM tool-use traces than JS |
| **TypeScript/JS** | Closest to Cloudflare's published technique; rich LLM training distribution; trivially sandboxable in V8 | Forces JSON marshalling at the FFI boundary; adds a JS runtime to a Python project |

**Recommendation:** **Python**. The whole repo is Python; the YNAB SDK models are Pydantic; the LLM-written code can directly destructure `category.name`, `txn.amount`, etc. without a marshalling layer. If we later discover Claude / GPT writes materially better JS than Python for this style of work, we can add a JS executor as a second option.

### 4.3 Surface compression strategy

| Strategy | Description |
| --- | --- |
| **A. Augment** | Code Mode is opt-in; existing 33 tools stay registered. `code_mode_enabled=true` adds `execute`. |
| **B. Gate** | When `code_mode_enabled=true`, the server can hide the 33 underlying tools and expose Code Mode plus a tiny core (`get_budgets`, `ping`). Read-only and mutation tools are gated independently. |
| **C. Replace** | Code Mode is the default interface once the gated path has been tested, with an escape hatch for direct tools while the feature stabilizes. |

**Recommendation:** start at **A** (augment) on a feature branch so the in-process runner can be tested without disrupting the existing tool surface. Design toward **B** as the first supported default: read-only Code Mode can be enabled separately from mutating Code Mode, and mutation calls require an explicit additional opt-in. Once that split is working and tested, plan toward **C** as the long-term replacement path rather than preserving augment-only forever.

### 4.4 Discovery: one tool or two?

Cloudflare uses `search` + `execute`. For mcp-ynab the *type stubs themselves* are small (33 functions ≈ 2–3KB of `.pyi`). We can ship them inline as part of the `execute` tool description, **eliminating the need for a separate discovery `search` tool**. Reconsider only if the surface ever grows past ~100 tools.

Do not collapse permission scope into discovery scope. The implementation should maintain two generated tool namespaces:

- `ynab.read` exposes read-only helpers and is available when `code_mode_enabled=true`.
- `ynab.write` exposes mutating helpers only when `code_mode_mutations_enabled=true`.

That split lets users install or configure the MCP server in a read-only posture by default, then opt in to mutation support with a separate preference or deployment profile. If the separation becomes awkward inside one MCP server, file a separate investigation for a read-only MCP server plus a write-capable MCP server with narrower install scopes.

### 4.5 Type-stub generation

FastMCP already has the tool registry. We can introspect it and emit a `.pyi`:

```python
# Pseudocode for the stub generator
def generate_stubs(mcp):
    lines = ["from typing import Any\n"]
    for tool in mcp._tool_manager._tools.values():
        sig = inspect.signature(tool.fn)
        # Emit `async def name(...) -> ReturnType: ...` with docstring
        lines.append(_format_stub(tool.name, sig, tool.description))
    return "\n".join(lines)
```

Delivered as the MCP resource `ynab://code-mode/stubs` (mime-type `text/x-python`). The client can pin/cache it.

---

## 5. Recommended architecture (MVP)

### 5.1 User-visible surface

| Item | Type | Purpose |
| --- | --- | --- |
| `code_mode_enabled: bool` | preference (epic `6ha`) | Off by default; enables read-only Code Mode |
| `code_mode_mutations_enabled: bool` | preference | Off by default; enables `ynab.write.*` mutating calls inside Code Mode |
| `code_mode_replace_tools: bool` | preference | Off by default for MVP; later hides direct tools after the gated path is tested |
| `code_mode_timeout_s: float` | preference | Default `10.0`, capped at `60.0` |
| `code_mode_max_output_chars: int` | preference | Default `8192`; truncate captured stdout |
| `execute(code: str, timeout: float \| None = None)` | tool | The one tool. `timeout` is per-call and capped by policy. Annotation is conservative whenever mutation mode is enabled. |
| `ynab://code-mode/stubs` | resource | Returns generated `.pyi` for the current tool registry |
| `ynab://code-mode/examples` | resource | Returns a curated in-tree set of worked examples (categorize-by-payee, monthly cleanup, etc.) |

### 5.2 Execution harness (prose, not literal source)

The runner module (proposed `src/mcp_ynab/code_mode/runner.py`) follows this shape:

1. Parse the snippet with `ast.parse(..., mode="exec")`.
2. Run an audit walk over the AST (see §5.4) — any forbidden node short-circuits with a structured error.
3. Build a sandbox-globals dict containing only:
   - `ynab` — the gated tool-proxy namespace (§5.3),
   - `__builtins__` — a small allow-list dict (`len`, `range`, `enumerate`, `zip`, `print`, `sorted`, `min`, `max`, `sum`, `dict`, `list`, `set`, `tuple`, `str`, `int`, `float`, `bool`, `True`, `False`, `None`),
   - a `LIMIT` constant the LLM is encouraged to use for slicing large outputs.
4. Wrap the snippet's body in `async def __main__(): …`, compile it, and bind it into sandbox-globals using Python's standard run-compiled-code primitives.
5. Pull `__main__` back out and await it under `asyncio.wait_for(timeout=…)`.
6. Capture stdout with `contextlib.redirect_stdout(io.StringIO())`.
7. Return a `CodeModeResult(ok, result, logs, error?, traceback?)` (a Pydantic model with structured fields).

### 5.3 Tool proxy

Instead of routing back through the MCP transport (slow, requires an open session), build the proxy by **calling tool callables directly** with their already-validated kwargs. The proxy is generated as separate read and write namespaces, so the default Code Mode surface cannot accidentally call mutating tools:

```python
def _build_ynab_proxy(ctx):
    ns = SimpleNamespace(read=SimpleNamespace(), write=SimpleNamespace())
    for tool in mcp._tool_manager._tools.values():
        target = ns.write if _is_mutating_tool(tool) else ns.read
        target.__dict__[tool.name] = _bind_tool(tool, ctx)
    return ns
```

When `code_mode_mutations_enabled=false`, `ynab.write` should either be absent or raise a clear `CodeModePermissionError` before the underlying tool callable runs. The AST audit should also statically reject `ynab.write.*` calls in read-only mode so permission failures happen before execution when possible.

`_bind_tool` returns an `async` callable that:
1. Validates kwargs against the tool's pydantic schema (FastMCP already does this — reuse it),
2. Injects `ctx` if the underlying tool wants it,
3. Awaits the result,
4. Returns a plain Pydantic model / dict (no MCP wrapper).

### 5.4 AST audit (v1 whitelist)

Reject at parse time:

- `Import`, `ImportFrom` (no escape via library imports)
- `Attribute` access on `__` dunders (no `__class__.__mro__` walks)
- Names: `open`, `compile`, `__import__`, `globals`, `locals`, `vars`, `getattr` with non-literal arg, `delattr`, `setattr`, plus the dynamic-code-execution builtins (`eval` / `exec`)
- `With` blocks targeting unknown context managers (allow `contextlib.suppress`?)

This is **defense in depth, not a security boundary**. Document plainly: "if you don't trust the prompt source, do not enable code mode."

### 5.5 Permission gating

The MVP should use in-process AST validation plus explicit read/write gating:

1. Classify registered tools as read-only or mutating from FastMCP annotations and a local override table for any ambiguous tools.
2. Generate stubs under `ynab.read.*` and `ynab.write.*` rather than one flat namespace.
3. Enable `ynab.read.*` when `code_mode_enabled=true`.
4. Enable `ynab.write.*` only when `code_mode_mutations_enabled=true`.
5. During AST audit, detect direct `ynab.write.<tool>(...)` calls and reject them in read-only mode before execution.

This avoids an "all tools or no tools" switch and supports deployments that want a read-only MCP configuration separate from write-capable budgeting workflows.

### 5.6 Failure modes & observability

| Mode | Handling |
| --- | --- |
| Timeout | Return `{ok:false, error:"timeout"}`, include captured stdout up to that point |
| AST audit failure | Return `{ok:false, error:"forbidden: <name>"}`, no run |
| Mutation disabled | Return `{ok:false, error:"mutations_disabled"}`, no run if detected statically |
| Tool exception inside code | Propagate as Python exception; LLM gets traceback + logs |
| Output too large | Truncate at `code_mode_max_output_chars`, append `[... truncated]` |
| Auth missing | Underlying tool raises `ApiException(401)`; surface via traceback |

Logging mirrors the pattern in `tools/transactions.py` — `logger.info("[code-mode] …")` with PII-stripped argument summaries.

---

## 6. Phased delivery and current state

| Issue | Scope | State |
| --- | --- | --- |
| `cmd.1` Foundations | Add `code_mode_*` preferences, including separate mutation and replacement switches; wire `Preferences` validation; tests | Implemented |
| `cmd.2` Stub generator | Introspect FastMCP tool registry; emit `.pyi`; expose as `ynab://code-mode/stubs`; tests with snapshot of generated stubs | Implemented |
| `cmd.3` Runner + gated tool proxy (in-process) | AST audit, `_build_ynab_proxy`, read/write namespace gating, async run harness, per-call timeout, stdout capture | Implemented for MVP; subprocess hardening remains |
| `cmd.4` `execute` MCP tool | Wire the runner into FastMCP; annotate; integration tests | Implemented |
| `cmd.5` Examples resource | Curated worked examples in `docs/` or `examples/`, exposed through `ynab://code-mode/examples` | Implemented in `docs/code-mode-examples.md` |
| `cmd.6` Sandbox hardening | Move runner to subprocess; `resource.setrlimit`; consider seccomp on Linux; revise threat model in this notebook | Open |
| `cmd.7` Surface gating / replacement | After the gated runner works, hide direct tools behind a `code_mode_replace_tools` pref and plan toward Code Mode as the primary interface | Implemented for MVP |
| `cmd.8` (optional) JS runner | Add `py-mini-racer` based JS runner as an alternative; only if Python-mode quality is insufficient | Deferred |

Each issue should land green tests + ruff clean + a focused commit (matches the project's existing `feat(prefs)` cadence).

---

## 7. Decisions from review

These decisions came out of the review of this plan and should guide the first implementation branch.

1. **Sandbox tier for MVP:** use an in-process AST-whitelisted runner for the first branch, with read/write permission gating layered on top. Subprocess isolation remains a later hardening phase.
2. **Default state:** keep Code Mode opt-in for now via preferences. The MVP should not enable it implicitly from the environment.
3. **Mutation policy:** default to read-only Code Mode. Mutating calls require a separate opt-in through `code_mode_mutations_enabled`; if the AST audit sees `ynab.write.*` while mutations are disabled, reject the snippet before execution.
4. **Timeout policy:** allow a per-call `timeout` argument, capped by the configured maximum.
5. **Surface replacement:** plan toward full replacement after the gated implementation has been tested, using `code_mode_replace_tools` as the transition switch.
6. **Examples resource scope:** keep curated examples in-tree, under `docs/` or `examples/`, and expose them through `ynab://code-mode/examples`.

---

## 8. Risks & non-obvious gotchas

- **Pydantic-model serialization.** The existing tools return either `list[TextContent]` or `Optional[str]` (see `ynab://preferences` vs `ynab://preferences/budget_id` in `6ha.4`). Inside Code Mode the proxy must return **plain models / dicts**, not MCP-wrapped content. The wrapper layer needs to live one level above the proxy.
- **Caching crosstalk.** `YNABResources` holds a category cache (`6ha.3`). Code Mode code that mutates state should invalidate the cache — but since we're calling the same callables the regular tools call, this should "just work" provided the underlying tool functions are the ones doing the invalidation. Worth a regression test.
- **Empty-string clearing.** Per `6ha.4`'s `_coerce_field_value` rule, `set_preference("code_mode_enabled", "")` should *clear* the field, falling back to default `false`. Easy to forget when adding new bool prefs.
- **Tool-result size.** YNAB endpoints like `get_transactions` for a full month can return >1MB. The LLM-written code may inadvertently `return all_txns`, blowing the MCP message size limit. Truncation + a clearly-documented `LIMIT` global may be wise.
- **Idempotency hints.** Code Mode breaks the FastMCP tool annotations contract — the *outer* tool is mutating-and-non-idempotent, even when the *inner* code is read-only. Document this and pick a conservative annotation.
- **AST audit bypasses.** Python is famously hard to sandbox. We are not the first to learn this. Known bypasses include `().__class__.__base__.__subclasses__()`, format-string trickery, async generator `.athrow`, and dunder-name lookups via f-string interpolation. The whitelist should reject `__` substring on any string used in `getattr`/`Attribute`, and forbid f-strings as a v1 simplification (we can relax later).
- **Async cancellation hygiene.** A timeout-cancelled snippet may leave a YNAB request mid-flight. Existing tools all use `async with ApiClient(...)`; that should clean up on cancellation, but verify with a forced-timeout test.

---

## 9. Concrete user-facing example (after Phase 4)

What the LLM would write today (multi-call):
```
get_transactions_needing_attention()
# … LLM reads result, picks 14 entries, decides categories …
bulk_categorize([...])
```

What it would write under Code Mode (single call):
```python
txns = await ynab.read.get_transactions_needing_attention()
plan = []
for txn in txns:
    payee = (txn.payee_name or "").upper()
    if "STARBUCKS" in payee:
        plan.append({"id": txn.id, "category_id": COFFEE_CATEGORY_ID})
    elif "WHOLE FOODS" in payee:
        plan.append({"id": txn.id, "category_id": GROCERIES_CATEGORY_ID})
if plan:
    result = await ynab.write.bulk_categorize(assignments=plan)
    print("categorized", len(plan), "transactions")
    return result
return {"planned": 0}
```

One tool call. One round-trip. The model never sees the full transaction payload unless it `print`s it.

---

## 10. References

- Cloudflare. *"Code Mode: the better way to use MCP."* <https://blog.cloudflare.com/code-mode/>
- Cloudflare. *"Code Mode: give agents an entire API in 1,000 tokens."* <https://blog.cloudflare.com/code-mode-mcp/>
- Cloudflare. *"Scaling MCP adoption: reference architecture …"* <https://blog.cloudflare.com/enterprise-mcp/>
- Cloudflare Agents docs — *Codemode*. <https://developers.cloudflare.com/agents/api-reference/codemode/>
- WorkOS — *"Cloudflare: Code Mode Cuts Token Usage by 81%."* <https://workos.com/blog/cloudflare-code-mode-cuts-token-usage-by-81>
- InfoQ — *"Cloudflare Launches Code Mode MCP Server …"* <https://www.infoq.com/news/2026/04/cloudflare-code-mode-mcp-server/>
- `@cloudflare/codemode` package — reference implementation for AI-SDK + DynamicWorkerLoader + iframe runners.
- CodeAct (the academic inspiration Cloudflare cites): Wang et al., *"Executable Code Actions Elicit Better LLM Agents."* arXiv 2402.01030.
