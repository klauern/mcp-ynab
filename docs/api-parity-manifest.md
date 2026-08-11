# YNAB API parity manifest

[`api-parity-manifest.json`](api-parity-manifest.json) is the canonical inventory of the
44 operations in the official YNAB OpenAPI 1.86.0 specification. Each entry records its
section, HTTP method, path, OpenAPI operation ID, canonical `api_` tool name, read/write
classification, and current implementation status.

The source is the official specification at
<https://api.ynab.com/papi/open_api_spec.yaml>. The generator pins version `1.86.0` and
the expected operation count, so a new API version or operation cannot silently change the
checked-in inventory.

## Refreshing the manifest

Run:

```console
task parity:manifest
```

Then review the source version, source SHA-256, operation additions/removals, classification,
and implementation statuses in the diff. If YNAB has published a version newer than 1.86.0,
review its API and SDK compatibility before updating the pinned version or expected operation
count in `scripts/generate_api_parity_manifest.py`.

To verify that the checked-in file still matches the live official source without rewriting it:

```console
task parity:manifest:check
```

Generation is deterministic: the artifact contains no timestamp, operations are sorted by
operation ID, and JSON formatting is stable.
