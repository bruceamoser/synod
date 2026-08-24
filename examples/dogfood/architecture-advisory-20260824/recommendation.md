# Recommendation — architecture-advisory

**Verdict:** Adopt option (a): run document ingestion as a single long-lived service whose in-process task queue is backed by a persisted tasks table (one row per file, written before dispatch), with a startup recovery pass that re-dispatches all queued and in-progress rows and an attempt-capped dead-letter table with alerting. This recommendation carries two conditions: (1) chunk writes must be keyed by deterministic chunk IDs so at-least-once recovery is idempotent and restart re-dispatch cannot duplicate chunks in the vector store; (2) the two-engineer ops team takes on a standing DLQ-watch duty to drain and alert on dead-lettered items.

**Confidence:** 0.8

## Per-topic outcomes

- t-01: resolved

## Resolved (per the librarian)
- t-01: resolved by consensus in round 2 - persisted-queue design retired the crash-loss refute; conditions: deterministic chunk IDs for idempotent recovery, standing DLQ watch

## Dissenting views

- contrarian on t-01: Supports option (a) at lower confidence (0.6) after retiring f-002: the persisted-queue design passed the crash-replay, restart-recovery, and poison-file tests, but recovery is at-least-once, so a crash between the vector-store write and the done-mark re-dispatches an already-written file; the supporters' idempotency claim was asserted without measured evidence, so the design must key chunk writes with deterministic chunk IDs (or dedupe) or weekly restart replay will silently duplicate chunks and degrade retrieval quality with no alert. Additionally records a standing DLQ-watch duty: the dead-letter table needs ongoing human attention, and frequent DLQ traffic is a signal to revisit the architecture.

## Rulings applied

- none
