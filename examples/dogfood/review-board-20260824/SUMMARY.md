# Dogfood: review-board (Council B - forced impasse, blind-judge path)

- **Date:** 2026-08-24 (run dir `20260824-0615`)
- **Charter:** `examples/review-board/charter.yaml` (core four + custom `security` (R6, votes); quorum 3 of 4 voting; max_rounds 2)
- **Problem:** Should council deliberations be public by default or private by default (t-01)
- **Source:** `governance-charter` (5-clause excerpt, 34-66 words/clause)
- **Outcome:** **PASS** - forced impasse resolved by blind judge ruling `r-001`, closed with `verify` chain ok (17 events)

## Deliberation

| Round | librarian | researcher | contrarian | security | check |
|---|---|---|---|---|---|
| 1 | support 0.72 | support 0.80 | refute 0.85 (f-003) | refute 0.85 (f-004) | continue (2 < quorum 3) |
| 2 | **refute 0.62 (flipped, f-005)** | support 0.70 (f-006, rebuts f-003/f-004) | refute 0.85 (f-007) | refute 0.80 (f-008, rebuts f-006) | **impasse: max_rounds(2) reached, contested [t-01], action: judge** |

Round 2 was a genuine persuasion event: the librarian flipped from support to refute,
weighing the compound of the position-gaming (f-003) and exposure (f-004) refutes
against charter clauses 7.3/7.4. The researcher held support (0.7, down from 0.8)
rebutting both refutes on clause-level grounds.

## Blind judge

- `judge-brief`: **wall clean** (3 corpus docs; evidence stripped to `{source, claim}`;
  no `quote_or_excerpt` in the brief; brief verified free of problem-statement and source text)
- Judge input: ONLY `judge/brief.json` + role card (no problem, no source, no member packets)
- Ruling `r-001` (binding, sealed): **private-by-default as the governing default** -
  raw record under access control, publication limited to the curated recommendation
  carrying its grounds. Four conditions (retention intact + revocation path; recommendation
  carries grounds; bounded need-to-know access with auditable path; bar on reintroducing
  org-wide readability without eliminating the position-gaming and failure-open harms).
- Reasoning grounded only in the brief's arguments (cites f-001..f-008); correctly found
  the support side's "redaction-gate" defense invoked a mechanism the defined public pole
  did not contain, and the position-gaming refute unrebutted.

## Close

- `close --recommendation-file`: exit 0, `rulings: 1` (engine cross-checked
  `rulings_applied ⊇ [r-001]`)
- `verify`: **chain ok, 17 events**
- Dissent preserved: researcher (public-by-default on charter 7.1/7.2/7.5 grounds;
  their preservation concern captured as ruling condition 1)
- Confidence 0.8

## Wall status

| Attempt | Result |
|---|---|
| Run 1 (`20260824-0123`) | `judge-brief` REFUSED (exit 2): 2 members echoed a 10-word problem-statement clause (the problem contained an engine-internals sentence) |
| Run 2 (`20260824-0525`) | `judge-brief` REFUSED (exit 2): 1 member put a verbatim 10-word source run into `evidence[].claim` (source was a single 405-byte dense sentence - pathologically easy to echo) |
| Run 3 (this run) | **PASS** - multi-clause source + verified-clean problem + pre-ingest lint |

## Process fixes made (linked)

1. **PR #11** `dogfood/wall-discipline` - wall discipline made proactive in SKILL.md
   Stage 2 (mandatory line in every member prompt) + all four role cards (librarian and
   contrarian cards were missing it entirely - both of the run-1 leakers).
2. **PR #12** `dogfood/sealed-at-stamp` - engine now stamps `sealed_at` on seal. Found by
   this run: the judge's placeholder was preserved (`payload.get(...) or now_iso()` is
   fill-if-missing), so the ledger carried a fake seal timestamp. The judge card and ruling
   schema both say the engine stamps it. NOTE: this run's ledger still shows the
   placeholder `2000-01-01`-style value as an honest artifact of the pre-fix engine; the
   fix is verified by the regression test in PR #12.
3. **Pre-ingest wall lint** (orchestrator practice, new): each finding's `argument` +
   `evidence[].claim` fields are n-gram-linted against the problem statement and sources
   BEFORE `finding` ingest. In this run it caught 2 leaks (researcher r1 argument,
   librarian r2 claim) that would otherwise have forced whole-council re-runs (the ledger
   is append-only). Single-member rewrites (~7 min each) replaced ~90-min re-runs.
   Candidate for a SKILL.md Stage 2 step in a follow-up content PR.

## Engine bugs found

- `sealed_at` not stamped on seal (fixed, PR #12, regression-tested)
- No other engine defects: schema validation (exit 4) correctly rejected a finding with an
  empty `quote_or_excerpt`; ruling cross-check at close worked; ruled topic frozen
  (`state: ruled`) in post-seal `check`.

## Known limitation

All members and the judge ran the same local model (Qwen3.8-27B via llama.cpp). This
validates the pipeline (schema, wall, consensus, impasse, sealed ruling, hash chain) but
NOT model decorrelation - the Q4 decision (judge model) remains open.
