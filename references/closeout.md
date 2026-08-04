# Closeout and short-term continuity

Run closeout once after the parent integrates one logical task that actually retrieved, adopted, wrote, or updated KB. A task that fully skipped KB needs no closeout. Do not repeat an earlier query in a later aggregate closeout.

When an adopted hit produces an observable result in the current task, record one linked outcome event per adopted entry before closeout. Use a stable event ID for retries, exact current evidence paths, and only these controlled values: `recurrence=observed|not_observed|unknown|not_applicable` and `user_verdict=accepted|rejected|mixed|not_provided`. An outcome is runtime feedback, not permission to rewrite durable KB automatically. A rejection or recurrence triggers the normal evidence-backed add/update/supersede audit.

```powershell
& $Python "$SkillRoot\scripts\kb_outcome_event.py" `
  --event-id '<stable event id>' `
  --retrieval-id '<persisted retrieval id>' `
  --entry-id '<adopted hit id>' `
  --application-target '<actual file, component, decision, or carrier>' `
  --expected-effect '<specific expected effect>' `
  --actual-result '<observed result>' `
  --recurrence 'observed|not_observed|unknown|not_applicable' `
  --user-verdict 'accepted|rejected|mixed|not_provided' `
  --evidence-path '<current evidence path>'
```

Do not emit an outcome when no result was observable. Do not invent a new retrieval merely to attach telemetry, and do not link an event by similar wording or timestamp.

```powershell
$SkillRoot = '<absolute path to the installed personal-kb directory>'
$Python = '<absolute path to a Python executable>'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $Python "$SkillRoot\scripts\kb_closeout.py" `
  --closeout-id '<stable task id>' `
  --linked-retrieval-id '<runtime retrieval id>' `
  --query '<retrieval query>' `
  --rag-calls <count> `
  --hit-count <count> `
  --allowed-hit-id <entry_id> `
  --used-locate <entry_id> `
  --reason "<required when nothing was adopted/written/updated>"
```

Repeat `--linked-retrieval-id` for every RAG call integrated by this parent closeout. For parent-direct retrieval, copy the ID from the parent's RAG output. For delegated retrieval, copy the unchanged IDs from the KB scout brief. Never invent IDs or rerun RAG in the parent only to create telemetry.

Use only classified adoption in the normal path:

- `--used-locate`: record without heat.
- `--used-decide`, `--used-fix`, `--used-write`: record and heat a fresh long-term record.
- No adopted record: provide a concise skipped reason.

Validate adopted IDs against retrieved hit IDs when available. Keep legacy `--used` only for compatibility tests.

Session briefs are short-term context for the last 1-2 days. Require an explicit meaningful `--session-brief-summary`, keep only a small current set per repo/branch, roll off replaced decisions, and never pass brief IDs into long-term heat. A brief must not match a future task solely through generic tags such as `study` or `recent-session`.

Default successful closeout is silent. Use `--verbose` for one minimal summary and `--debug` or `--stdout` for full events. Parameter, storage, lock, corruption, and long-term heat failures must remain actionable and non-zero. Reuse a stable closeout ID for safe retries; inspect `retry_safe` before retrying a partial failure.
