# Beads Issue Tracking

This repository uses [Beads](https://github.com/gastownhall/beads) for durable issue tracking.

## Storage and Sync

- The local Dolt database is the source of truth.
- `bd dolt push` sends Dolt commits to the configured remote.
- `bd dolt pull` receives Dolt commits from the configured remote.
- `.beads/issues.jsonl` is an export for viewers, migration, and interoperability. It is not the sync source.

If `.beads/issues.jsonl` has a merge conflict, keep the live Dolt database and regenerate the export:

```bash
bd export -o .beads/issues.jsonl
```

Do not use the retired `bd sync` or `bd merge` commands.

## Backup and Recovery

Use a Dolt-native backup for off-machine disaster recovery. A JSONL export can
rehydrate issue records, but it cannot restore the complete Dolt database.

```bash
bd backup init <path>    # Configure a filesystem or DoltHub destination once
bd backup sync           # Push the complete database and history
bd backup status         # Inspect the latest backup result
bd backup restore [path] # Restore from the configured or specified backup
```

`bd backup restore --force` overwrites an existing database. Resolve the exact
backup path and review `bd backup restore --help` before using it.

`bd export -o .beads/issues.jsonl` creates an issue export for migration and
interoperability. It does not preserve Dolt history, branches, working-set
state, or non-issue tables.

## Common Commands

```bash
bd prime                             # Load the current workflow instructions
bd ready                             # Find work that has no blockers
bd show <id>                         # Inspect one issue
bd update <id> --claim               # Claim one issue atomically
bd create "Short title" -t task -p 2 # Create a durable work item
bd close <id> --reason="Completed"   # Close completed work
bd dolt push                         # Push tracker state when authorized
bd dolt pull                         # Pull tracker state when authorized
```

Run `bd <command> --help` for current command details. Run `bd context`, `bd config show`, `bd hooks list`, and `bd ping` to inspect this workspace. `bd doctor` is not available in embedded mode.
