# Council report — architecture-advisory

- Run dir: /tmp/dogfood-a/.hermes/councils/architecture-advisory/runs/20260824-0113
- Rounds elapsed: 2
- Findings: 6
- Rulings: none
- Final verdict: Adopt option (a): run document ingestion as a single long-lived service whose in-process task queue is backed by a persisted tasks table (one row per file, written before dispatch), with a startup recovery pass that re-dispatches all queued and in-progress rows and an attempt-capped dead-letter table with alerting. This recommendation carries two conditions: (1) chunk writes must be keyed by deterministic chunk IDs so at-least-once recovery is idempotent and restart re-dispatch cannot duplicate chunks in the vector store; (2) the two-engineer ops team takes on a standing DLQ-watch duty to drain and alert on dead-lettered items.

## Summary

Adopt option (a): run document ingestion as a single long-lived service whose in-process task queue is backed by a persisted tasks table (one row per file, written before dispatch), with a startup recovery pass that re-dispatches all queued and in-progress rows and an attempt-capped dead-letter table with alerting. This recommendation carries two conditions: (1) chunk writes must be keyed by deterministic chunk IDs so at-least-once recovery is idempotent and restart re-dispatch cannot duplicate chunks in the vector store; (2) the two-engineer ops team takes on a standing DLQ-watch duty to drain and alert on dead-lettered items.

