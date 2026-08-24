# Dogfood: architecture-advisory (Council A - known answer, consensus path)

- **Date:** 2026-08-24 (run dir `20260824-0113`)
- **Charter:** `examples/architecture-advisory/charter.yaml` (core four; voting: librarian, contrarian, researcher; judge blind; quorum 2 of 3; max_rounds 3)
- **Problem:** Should the new Clarity ingestion worker be a single long-lived service with in-process queues, or a set of short-lived per-batch jobs? (2-engineer ops team, 200 files/day + 50-file bursts)
- **Known-correct answer:** option (a) - single long-lived service, with a persisted queue
- **Roster:** real isolated subagents (Hermes `delegate_task`, local Qwen3.8-27B-Q8_0), each given only its role card + brief packet. 3 concurrent (one batch per round).

## Outcome: PASS - consensus, known answer reached

| Round | Findings | Engine action |
|---|---|---|
| 1 | f-001 librarian support (0.7) / f-002 contrarian **refute** (0.65) / f-003 researcher support (0.8, 3 cited sources) | `continue` - f-002 unrebutted |
| 2 | f-004 librarian support (0.7, rebuts f-002) / f-005 contrarian **support** (0.6, retires f-002) / f-006 researcher support (0.8, rebuts f-002) | `recommend` - topic resolved |

## What happened (the interesting part)

Round 1: the contrarian produced a genuinely concrete counter-example (09:00 burst queued in memory, 09:05 restart drops in-flight work; weekly-restart tax) and explicitly conditioned their refute on the supporters producing a concrete persistence + recovery design. The support quorum (2) met, but the unrebutted refute correctly held the topic contested - `action: continue`.

Round 2: librarian + researcher answered the demand with a persisted-queue design (tasks table written before dispatch, startup recovery re-dispatching queued/in-progress rows, attempt-capped dead-letter table). The contrarian **read the actual round-2 findings**, ran worked tests (crash replay, restart recovery, poison-file path), and retired the refute: "the persistence design answers the crash-loss counter-example - no further counter-example found", moving to support at lower confidence (0.6) while recording two **conditions** (deterministic chunk IDs for idempotent at-least-once recovery; standing DLQ-watch duty) as dissent, not refutes.

## Verdict

- `check` final: `action: recommend`, t-01 **resolved**, support 3, un_rebutted_refutes []
- `close`: recommendation.md + report.md written; `rulings_applied: []` (consensus path - no judge)
- `verify`: `"chain": "ok"` (15 events)
- **Wall status: clean** (no exit 2 in this run)
- Recommendation is decision-oriented and cites t-01; matches the known-correct answer; dissent preserved (contrarian's two conditions)

## Process fixes made

None for this council. (Council B's wall rejection produced the `dogfood/wall-discipline` content fix, PR #11.)
