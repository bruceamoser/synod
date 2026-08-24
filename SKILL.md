---
name: council
description: Convene a Synod multi-council deliberation for high-stakes decisions. Use when a decision needs structured, auditable, multi-perspective review (architecture, proposals, design).
---

# Council — Synod multi-council deliberation

You are the orchestrator. This skill is the runbook; `docs/ARCHITECTURE.md` §4–§5 is the spec it restates. The deterministic engine does everything that can be computed (state, ledger, consensus math, the blind wall); you do everything that requires judgment (running the roles, dispatching subagents, reading the `action` the engine prints). A fresh session with only this file and the engine must be able to convene a council to completion — no mid-run questions, no improvisation.

## Prerequisites

- The skill/repo path (call it `$SKILL`): the directory containing this file. It holds `scripts/council.py`, `references/roles/`, `references/schemas/`, and `templates/charter.yaml`.
- `python3` (3.11) with **PyYAML** and **jsonschema** importable. No other dependencies; no network; no venv.
- The engine is the single file `scripts/council.py`, invoked as `python3 $SKILL/scripts/council.py <command>`. Every engine command in this runbook is one of its subcommands; `python3 $SKILL/scripts/council.py --help` lists them.
- Run state lives under `~/.hermes/councils/<council>/runs/<YYYYMMDD-HHMM>/` (override with `COUNCILS_ROOT` only in tests). The run dir printed by `scaffold` is your `<run>` for every later command.
- You must be the **top-level** agent (Hermes `max_spawn_depth=1`): the council orchestrator is never itself a subagent.

## Stage 1 — Convene

1. Choose the charter. A registered council resolves by name (`python3 $SKILL/scripts/council.py list` / `show <name>`); otherwise use a charter file (start from `templates/charter.yaml`). Check it with `python3 $SKILL/scripts/council.py validate-charter <charter>` and, for bookkeeping, `python3 $SKILL/scripts/council.py register <charter>` (both optional before scaffolding).
2. Scaffold the run: `python3 $SKILL/scripts/council.py scaffold <charter> [--problem-file <problem.txt>]`. This validates the charter, creates the run dir, and seeds the ledger with the charter event.
   - If the problem is composed live by you (the librarian duty) instead of arriving as a file: scaffold without `--problem-file`, draft the problem brief — a bounded, decision-oriented restatement: what decision must be made, by whom, with what constraints, plus the source list — then record it: `python3 $SKILL/scripts/council.py record-brief <run> --file <brief.txt>`.
   - The problem brief is the *only* statement of the problem any member ever receives; the judge never receives it.
3. Add every document the members may cite: `python3 $SKILL/scripts/council.py add-source <run> --file <doc> [--label <label>]` once per source. Raw sources are the Researcher's material and are judge-excluded; they enter the wall corpus.

## Stage 2 — Round loop

For each round `N` (starting at 1), for each **voting member — NOT the judge** (the judge never receives a packet; the engine refuses):

1. Render the packet: `python3 $SKILL/scripts/council.py brief <run> --round N --role R`. The packet (`briefs/round-NN/<role>.json`) carries `problem_brief`, a compressed `ledger_view` (`positions`, `open_refutes`, `sealed_rulings`), and the `task`. Render **every voter's** packet for the round - including later dispatch waves (the 3-concurrent cap means a 4+ voter council dispatches in waves; render each wave's packets before dispatching that wave). A member missing its packet will reconstruct the position map from the ledger instead, which works but is exactly the kind of process debt the dogfood exposed.
2. Dispatch the role as an **isolated subagent** (Hermes `delegate_task`): context = the role card file (`references/roles/<role>.md` for core roles; the charter's `card` path for custom roles) + the brief JSON path. The subagent writes its finding JSON to a file and nothing else.
   - **Wall discipline (mandatory in every member prompt).** The judge brief is built from the ledger and is wall-linted against the problem statement and every source. So each member's prompt must state, verbatim: *"Paraphrase in `argument` and `evidence[].claim`; keep verbatim text ONLY in `evidence[].quote_or_excerpt`. Never copy 10+ consecutive words from the problem statement, the brief, or any source into `argument` or `claim` — the blind judge must stay blind."* A member that leaks a verbatim span forces a `judge-brief` refusal (exit 2) late in the run; because the ledger is append-only, the clean recovery is a whole-council re-run. Stating the rule up front is the cheap path; catching it at Stage 3 is the expensive one.
3. **Pre-ingest wall lint (mandatory before `finding`).** The ledger is
   append-only and hash-chained: a finding that leaks a verbatim span into
   `argument` or `evidence[].claim` cannot be edited out later, and the leak
   is only *confirmed* at `judge-brief` - by which point the whole council
   has already deliberated around it. So lint each member file **before**
   ingesting: normalize `argument` and every `evidence[].claim` (lowercase,
   alphanumeric runs), and check for any 10-word consecutive run shared with
   the problem statement or any source under the run dir's `sources/`. On a
   hit, re-dispatch that **one** member with the exact offending span named
   and the instruction to paraphrase it; ingest the corrected file. The
   dogfood recovered would-be whole-council re-runs as ~7-minute
   single-member rewrites this way. `quote_or_excerpt` is exempt (stripped
   before the judge sees it). The `judge-brief` wall remains the backstop;
   this is the cheap front door.
4. Ingest it: `python3 $SKILL/scripts/council.py finding <run> --file <path> --role R [--model <model>]`. The engine validates the schema (exit 4 on violation) and assigns the finding id; `--model` records provenance (pass the model id if the charter overrides it; the engine refuses a `--model` that contradicts the charter's declared model for the role).

Batching: at most 3 subagents at a time (`delegation.max_concurrent_children=3`). Partition the members into batches of ≤3, run each batch in parallel (one `delegate_task` per role), and merge at batch end. 6 members = 2 batches. Note the batch count in the final report.

After all members have filed for the round:

1. `python3 $SKILL/scripts/council.py note-round <run> --round N`
2. `python3 $SKILL/scripts/council.py check <run>` — it prints the consensus result; branch on `action`:
   - `continue` — topics still contested, rounds remain: go to the next round.
   - `judge` — impasse (max rounds with contested topics, or consecutive no-progress rounds): Stage 3.
   - `recommend` — nothing contested: Stage 4.

Resolved topics freeze: later rounds brief only contested topics (the engine's packet does this for you).

## Stage 3 — Impasse / the blind judge

1. `python3 $SKILL/scripts/council.py judge-brief <run>`. The engine assembles `judge/brief.json` from ledger fields only (field whitelist: stance, argument, confidence, rebutting, evidence as `{source, claim}` — never verbatim excerpts) and wall-lints it against the problem statement, `problem.md`, and every raw source.
   - **Exit 2 means a member leaked a verbatim span.** Identify the offending member(s) from the refusal output (`judge/brief.rejected.json` names the shared spans), re-run those subagents with a stricter instruction ("paraphrase everything; quote only inside `quote_or_excerpt`"), re-ingest with `finding`, then retry `judge-brief`.
2. Dispatch the judge as an **isolated subagent** whose context is **only** `judge/brief.json` — never the problem statement, the sources, the member briefs, or anything else from this run. The wall's access half is your job as orchestrator; the engine enforces the data half. The judge returns one ruling JSON object per contested topic (see `references/roles/judge.md`); write it to a file (an array of objects is fine).
3. Seal it: `python3 $SKILL/scripts/council.py seal-ruling <run> --ruling-file <file>`. The ruling is now a sealed, immutable fact in the ledger; members work within it and no later round may reopen it.
4. Return to Stage 2 for the resolution round (the engine's packet now carries `sealed_rulings`). If the council reaches impasse again on *new* points, repeat Stage 3; if `check` returns `recommend`, go to Stage 4.

## Stage 4 — Synthesize & close

1. Dispatch the librarian (a small, sequential call — the librarian is not a fan-out member) to write the final recommendation JSON per `references/schemas/recommendation.schema.json`: `recommendation`, `rationale`, per-topic `resolved` outcomes, `rulings_applied` listing **every** sealed ruling id in the ledger (the engine cross-checks and refuses the close otherwise), `dissent` recorded verbatim (dissent is preserved, never re-litigated), and `confidence`.
2. `python3 $SKILL/scripts/council.py close <run> --recommendation-file <file>`. This validates the recommendation, appends the `recommendation` and `close` events, and writes `recommendation.md` and `report.md` into the run dir.
3. Deliver `recommendation.md` (the verdict, per-topic outcomes, dissent, rulings applied) and `report.md` (run dir, rounds, finding/ruling counts, batch count, final verdict) to the requester.

## Stage 5 — Audit

1. `python3 $SKILL/scripts/council.py verify <run>` — recomputes the whole hash chain and revalidates every payload; the result must be `"chain": "ok"`. Any other outcome means the run is forensically compromised: do not build on it, open a new run, and report the break.
2. If the council is registered: `python3 $SKILL/scripts/council.py show <council>` to confirm the roster summary still matches the charter on disk.

## Failure modes

| Exit | Meaning | Recovery |
|---|---|---|
| 0 | ok | — |
| 1 | Usage, charter, or registry error (each message names the offending field) | Fix the named input (charter field, missing file, unknown role/name, run already closed) and re-run the same command. The ledger is untouched. |
| 2 | Blind-wall refusal: the assembled judge brief shares a verbatim span with the problem statement or a source | Re-run the offending member(s) with a stricter paraphrase instruction, re-ingest via `finding`, retry `judge-brief`. Never hand-edit the brief. |
| 3 | Ledger hash chain break (tamper or corruption) | Stop. The run is forensically compromised; do not append further. Preserve the run dir for audit and convene a new run. |
| 4 | Schema validation failure (finding / ruling / recommendation / event envelope) | Fix the JSON against the schema named in the message (`references/schemas/`) and re-ingest. Nothing was appended. |

Every non-zero exit prints a JSON `{"error": "..."}` object on stdout; parse that, not the stderr line.

## Deterministic / judgment split

| Concern | Code (`council.py`) | You (orchestrator + role subagents) |
|---|---|---|
| Charter validation, scaffold, registry | ✅ | — |
| Problem formulation, digests | — | ✅ librarian |
| Member briefing packets | ✅ render | — |
| Findings, arguments, evidence | (schema-validated on ingest) | ✅ each role |
| Ledger append + hash chain, verify | ✅ | — |
| Consensus / impasse math | ✅ | — |
| Judge brief assembly (the wall) | ✅ whitelist + n-gram lint | — (you enforce the access half) |
| The ruling | (schema-validated) | ✅ judge |
| Ruling sealing, fact-injection | ✅ | — |
| Recommendation synthesis | (schema-validated) | ✅ librarian |
| Report delivery | ✅ validate + render | ✅ compose |

## Non-goals

- The engine never calls an LLM and never makes a judgment call — it validates, computes, and refuses. Do not ask it to, and do not wrap it in one.
- The orchestrator never hand-writes ledger JSONL, never edits `ledger.jsonl` or any brief in place, and never hand-assembles the judge brief — every event enters through an engine command.
- Councils recommend; they never act. Implementation is a separate, chartered act (see `docs/ARCHITECTURE.md` §12 and the Phase 3 dispatch flag, default off).
- No mid-run steering: a council runs to completion; new input means a new council.
