# Maintenance

Read this file only for explicit Personal KB maintenance.

Treat `personal-kb/repos/**/kb.jsonl` and explicit archives as durable facts that may be versioned in Git. Treat retrieval receipts, outcome events, closeout logs, session briefs, adoption events, locks, backups, indexes, aggregations, and effectiveness logs as runtime or derived state. Do not hand-edit receipt or outcome JSONL; use their CLIs so linkage and ID conflicts are checked under lock.

For archive, migrate, normalize, rebuild, or retention work:

1. Inspect the current corpus and Git state.
2. Run a dry-run when supported.
3. Apply the smallest scoped change.
4. Rebuild derived indexes only after durable records change.
5. Run quality and sensitive-data gates.

An explicit repo or branch override must short-circuit discovery. A safe workspace-parent fallback is valid and silent; record its reason in telemetry. Fail only when routing cannot be made safe.

Common maintenance commands:

```powershell
$SkillRoot = '<absolute path to the installed personal-kb directory>'
$Python = '<absolute path to a Python executable>'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $Python "$SkillRoot\scripts\kb_quality_gate.py" --keep-from 2026-07-01 --strict
& $Python "$SkillRoot\scripts\kb_rebuild_index.py" --apply `
  --root "$SkillRoot\storage\repos"
& $Python "$SkillRoot\scripts\kb_sensitive_scan.py" `
  --root "$SkillRoot\storage" --include-backups --json
```

Do not encode one-time retention permissions or dated migrations in the main Skill. Keep them in project decisions or maintenance configuration.
