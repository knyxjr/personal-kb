---
name: personal-kb
description: Retrieve and maintain verified local personal knowledge when a task depends on prior project history, recurring issues, user corrections, terminology, repo/branch/path mappings, reusable decisions, similar past work, or cross-session continuity. Also use for explicit Personal KB audit or maintenance. Do not use for one-off questions fully answerable from current files, generic code edits, current-state diagnostics with sufficient logs, or unrelated Skill/MCP maintenance.
---

# Personal KB

Use Personal KB as an AI-only historical context layer. Treat every hit as a lead, never as current truth. Current user instructions, files, code, logs, configs, tests, Git state, and current official sources take precedence.

## Installation location

Use the directory containing this `SKILL.md` as the canonical installation. Set these variables to the current installation and an explicit compatible Python executable before running examples:

```powershell
$SkillRoot = '<absolute path to the installed personal-kb directory>'
$Python = '<absolute path to a Python executable>'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
```

The default durable and runtime data root is `$SkillRoot\storage`. Do not set `PERSONAL_KB_ROOT` during ordinary use; reserve it for isolated tests or an explicit migration.

## Decide whether to retrieve

Retrieve only when the answer depends on at least one of these:

- A decision, correction, project state, or unfinished direction from another session.
- A recurring issue, term, path, repo, branch, service, or business-name mapping.
- Similar past implementation or troubleshooting experience.
- An explicit request to retrieve Personal KB records.

Skip ordinary RAG when current evidence fully answers a one-off question, a current log already determines a diagnosis, or the task is unrelated Skill/MCP maintenance. An explicit Personal KB audit or maintenance request triggers this Skill, but runtime audit should read `references/audit.md` and inspect session/runtime evidence directly; it does not require a self-referential long-term RAG query.

A request to remember a newly established rule is maintenance, not by itself a reason for ordinary RAG. Verify the new rule against current evidence, then check for an existing record during the write/update flow so it is updated instead of duplicated. Retrieve first only when the requested rule also depends on an older decision, mapping, incident, or other cross-session fact.

Explicit task constraints win. If a task forbids Personal KB, run no KB command. If it forbids writes but allows KB reading, use only read-only retrieval or audit commands.

## Use one logical-task budget

For one logical task or topic:

1. Read this Skill once.
2. Run at most one initial RAG query through the chosen retrieval owner, using concrete anchors.
3. Reuse the selected hints for follow-up turns and subagents.
4. Before reusing an old retrieval, compare its original query and hits with the current task's concrete anchors. The same asset, story, or repo is not sufficient scope identity. If the task adds states, components, failure families, topology, or deliverables that the old retrieval did not cover, treat that as a new durable anchor and run one focused follow-up retrieval for the gap.
5. Retrieve again only when the user introduces a new durable anchor, changes topic, asks for broader history after a zero-hit query, current evidence contradicts the prior result, the coverage audit finds a material gap, or context was lost and no selected result remains.
6. If this logical task retrieved, adopted, wrote, or updated KB, run one closeout after integration. A task that fully skipped KB needs no closeout. Do not repeat an earlier query in an aggregate closeout.

## Core lifecycle

1. Retrieve a small context set only when historical knowledge is needed.
2. Verify useful hits against current evidence.
3. Apply the verified current evidence, not the KB text itself.
4. When an adopted hit has an observable result, record one exact retrieval/hit-linked outcome event; the next retrieval surfaces that feedback without silently rewriting durable knowledge.
5. When this task used retrieval or KB maintenance, classify actual adoption once and close out; otherwise stop without a KB lifecycle event.
6. Add or update long-term KB only for verified, stable, reusable conclusions.

Retrieve with:

```powershell
& $Python "$SkillRoot\scripts\kb_rag_context.py" `
  '<task plus project, module, path, config, error, table, or durable business anchor>' --limit 5
```

Default retrieval is read-only. Do not update search counts, rebuild indexes, refresh aggregations, or include non-current records during ordinary work. Use `kb_search.py` only for full-record inspection, recall debugging, migration, normalization, or explicit corpus audit.

Every successful `kb_rag_context.py` call persists a `personal-kb.retrieval-receipt/v1` runtime receipt before stdout. Repeat `--scope-anchor` for concrete logical-task anchors. Each `label:value` anchor's `value`, or the whole untyped anchor, must occur explicitly in the query after Unicode, case, and whitespace normalization; an unbound claim fails before receipt persistence. Use `--receipt-output <path>` when a consumer needs the same canonical receipt as one JSON file. Reusing a retrieval ID with different content is an error.

## Apply decisions without redoing them

When a hit represents a user-confirmed `requirement` or `decision_confirmed` record:

1. Check whether the current user message replaces it.
2. Open its current source or Canonical file and verify that it still agrees.
3. If it agrees, state that the choice is already settled and execute it; do not restart full option analysis or dispatch broad comparison agents.
4. If the record is fresh and materially selects the route, classify it as `used-decide`. If it only located the current authority, classify it as `used-locate`.
5. If current evidence disagrees, follow current evidence and update or supersede the stale record during explicit maintenance.

## Classify adoption

Search hits are not automatically adopted. Classify only records that materially affected the completed task:

- `--used-locate`: located current evidence; record without heat.
- `--used-decide`: changed or replayed a verified decision; heat.
- `--used-fix`: changed diagnosis or repair; heat.
- `--used-write`: materially supported final content; heat.

Do not use legacy unclassified `--used` in the recommended flow. Do not heat records that were merely read. Session brief IDs are short-term context and must never enter long-term heat.

When this task retrieved or maintained KB, run closeout once after the parent integrates results. Routine outcome/write/closeout success is silent. Read `references/closeout.md` for observable-outcome fields, closeout fields, brief retention, compatibility, or telemetry debugging.

## Write durable knowledge

Write or update only a verified reusable root cause/fix, mapping, requirement, decision, pitfall, or implementation pattern. Never store guesses, raw chat, temporary state, secrets, credentials, database URLs, or raw internal endpoints. Prefer updating or superseding an existing record over adding a duplicate.

Treat each of these as a mandatory write audit: the user establishes a new reusable correction, the user overturns a prior approved/passed conclusion, or the same failure family recurs twice. First record the correction and current evidence in the owning project. Then, before closeout, decide whether to add, update, or supersede durable KB; do not leave the conclusion only in an iteration log. Track unresolved work as `KB_WRITE_PENDING`. Do not claim the memory loop is complete while that flag remains unresolved.

Read `references/record-schema.md` before adding, updating, superseding, importing, or refreshing evidence.

## Parent and subagent boundary

The parent decides whether retrieval is needed and chooses its executor. Run a narrow, single-anchor, single-pass retrieval directly in the parent, including one concrete similar incident from another project. Use one dedicated read-only KB scout when retrieval explicitly spans multiple projects or multiple historical stages, follows a zero-hit expansion, requires deduplication or conflict analysis, produces a large candidate set, or must serve multiple downstream workers. Do not infer broad scope merely from words such as `history` or `another project`; the parent must identify a broad routing reason.

The parent owns final hint selection, adoption, heating, writes, and closeout. Pass only selected hints to ordinary workers and require current-evidence verification. Ordinary workers must not run Personal KB scripts. If a worker discovers a new history anchor or insufficient/conflicting hints, it asks the parent for a KB follow-up instead of retrieving directly.

Read `references/subagents.md` before dispatching workers or a KB scout.

## Runtime output contract

- Retrieval and search commands return their requested context on stdout; this data output is expected.
- Successful write, update, closeout, and maintenance operations default to no stdout or stderr.
- `--verbose`: one minimal structured summary.
- `--debug`: routing reasons, candidate repos, failure IDs, and full events.
- Safe workspace-parent fallback: silent, with internal routing metadata.
- Parameter, unsafe-routing, corruption, lock, and write failures: concise actionable stderr plus non-zero exit.
- `kb_outcome_event.py` records an observed result only when its retrieval ID exists and its entry ID is one of that receipt's hits; event-ID retries are idempotent and conflicting reuse fails. Later RAG output surfaces prior acceptance, rejection, and recurrence feedback as recheck evidence, never as an automatic durable-record mutation.

An explicit repo or branch override must take precedence over repository discovery.

## Read details only when needed

- `references/closeout.md`: adoption, closeout, session brief, and telemetry fields.
- `references/record-schema.md`: durable add/update/supersede/evidence rules.
- `references/subagents.md`: exact worker and KB-scout prompt contracts.
- `references/audit.md`: runtime effectiveness, Codex session audit, and metric semantics.
- `references/maintenance.md`: archive, migrate, normalize, rebuild, sensitive scan, and Git boundaries.
- `references/validation.md`: required checks after changing Skill, scripts, routing, lifecycle, or KB data.

Keep routine work on retrieve, verify, apply, classified closeout, and durable write. Do not load audit or maintenance details during ordinary project work.
