# Durable record schema

Read this file only for add, update, supersede, import, or evidence-refresh work.

Use one of six kinds: `map`, `issue`, `pitfall`, `experience`, `requirement`, or `implementation`.

Every durable record needs:

- A short stable title and concise reusable conclusion.
- 2-8 aliases and 3-15 trigger terms.
- At least one current evidence pointer.
- No secret, credential, database URL, or raw internal endpoint.

Use `source_paths` only for files that semantically support the conclusion. Use `evidence_refs` for commits or conversation evidence. A fresh snapshot of an unrelated file does not make a record trustworthy. If a user preference is also recorded in a current state file, point to that file.

Records that only locate generated or derived artifacts must use `kind=map` and `artifact_locator=true`. Keep technical facts and user-confirmed decisions in separate records backed by code, config, logs, tests, current documentation, or explicit conversation evidence.

Use PowerShell UTF-8 base64 rather than browser-only `btoa`:

```powershell
$SkillRoot = '<absolute path to the installed personal-kb directory>'
$Python = '<absolute path to a Python executable>'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$entry = @{
    kind = 'requirement'
    title = '<stable title>'
    story = '<verified reusable conclusion>'
    aliases = @('<alias-1>', '<alias-2>')
    trigger_terms = @('<anchor-1>', '<anchor-2>', '<anchor-3>')
    source_paths = @('<current evidence file>')
}
$json = $entry | ConvertTo-Json -Depth 8 -Compress
$payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
& $Python "$SkillRoot\scripts\kb_add.py" --entry-b64 $payload --smart-field-check
```

Prefer update over duplicate add. Use `--expected-rev` for optimistic concurrency. Refresh evidence only after reviewing current files. When replacing a decision, mark the old record `superseded` and link its replacement and reason.
