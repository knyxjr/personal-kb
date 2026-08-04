# Validation

After changing prompt policy, routing, lifecycle, scripts, or durable data:

1. Validate Skill frontmatter and every referenced file.
2. Compile changed Python modules.
3. Run focused retrieval, closeout, session brief, audit, effectiveness, routing, sensitive-data, and quality-gate tests.
4. Run the end-to-end smoke test.
5. Run positive, negative, and boundary eval prompts against the baseline and candidate when trigger policy changes.

Core commands:

```powershell
$SkillRoot = '<absolute path to the installed personal-kb directory>'
$Python = '<absolute path to a Python executable>'
$QuickValidate = '<absolute path to skill-creator/scripts/quick_validate.py>'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPYCACHEPREFIX = Join-Path ([IO.Path]::GetTempPath()) 'personal-kb-pycache'
New-Item -ItemType Directory -Path $env:PYTHONPYCACHEPREFIX -Force | Out-Null
& $Python -m compileall -q "$SkillRoot\scripts" "$SkillRoot\backend"
& $Python "$SkillRoot\scripts\kb_rag_context_test.py"
& $Python "$SkillRoot\scripts\kb_outcome_event_test.py"
& $Python "$SkillRoot\scripts\kb_audit_runtime_value_test.py"
& $Python "$SkillRoot\scripts\kb_audit_codex_sessions_test.py"
& $Python "$SkillRoot\scripts\kb_record_codex_effectiveness_test.py"
& $Python "$SkillRoot\scripts\kb_eval_preflight_test.py"
& $Python "$SkillRoot\scripts\kb_eval_preflight.py" `
  --last-days 2 --strict
& $Python "$SkillRoot\scripts\kb_p0_usage_test.py"
& $Python "$SkillRoot\scripts\kb_session_brief_test.py"
& $Python "$SkillRoot\scripts\kb_sensitive_scan_test.py"
& $Python "$SkillRoot\scripts\kb_quality_gate_test.py"
& $Python "$SkillRoot\scripts\kb_quality_gate.py" --keep-from 2026-07-01 --strict
& $Python "$SkillRoot\scripts\kb_smoke_test.py" "$SkillRoot"
& $Python $QuickValidate "$SkillRoot"
git -C "$SkillRoot" diff --check -- .
if ($LASTEXITCODE -notin @(0, 129)) {
  throw 'git diff --check failed'
}
```

Required regression assertions:

- Rollout identity uses the filename UUID's own `session_meta.id`; root and subagent metrics stay separate.
- New classified adoption flags are parsed and reported.
- Retrieval/closeout pairing is per turn or query, not merely per long session.
- Artifact locators do not fill unrelated diagnostics; session briefs do not match on generic tags alone.
- User-confirmed requirements remain retrievable and current evidence remains authoritative.
- Explicit repo override and safe workspace fallback are silent.
- Successful closeout is silent; locate does not heat; decide/fix/write do; brief IDs never heat.
- Parameter, corruption, lock, unsafe-routing, and write failures remain actionable and non-zero.
- Ordinary subagents do not invoke KB.
- Broad parent retrieval routes to one read-only KB scout; the scout cannot write, heat, or close out; the parent reuses the returned brief without a duplicate RAG.
- Every RAG output has an opaque `retrieval_id`; delegated IDs link only to the declared parent closeout and orphan/duplicate IDs remain visible.
- Every successful RAG persists one strict retrieval receipt before stdout; repeated scope anchors and an optional atomic JSON mirror preserve the same canonical receipt.
- Retrieval and outcome IDs are idempotent, conflicting reuse fails, and an outcome cannot name an entry outside its linked receipt.
- Outcome fields use controlled recurrence/verdict values, retrieval surfaces prior outcome feedback, and runtime-value audit reports acceptance and recurrence without mutating durable records.
- Policy-simulator inputs and frozen gold remain separate; `--strict` runs trusted RAG against a temporary current-KB copy and checks production/snapshot hashes.
- Historical replay is a behavior shadow only. Multi-message turns and possible duplicates without lifecycle state are excluded from call-efficiency comparison; unknown execution status is reported, not guessed as success.
