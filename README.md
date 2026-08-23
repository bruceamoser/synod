# Synod

> *"The council speaks as one; the judge speaks for no one."*

**Synod** is a deterministic multi-council deliberation engine for [Hermes Agent](https://hermes-agent.nousresearch.com). Named for the formal assembly that deliberates and issues binding rulings, it implements the *Heterogeneous-Model Council with Synthesis Judge* pattern (Agent Patterns Catalog) as a reusable Hermes skill:

- **Multiple councils, each its own roster** — a council is a *charter* (YAML), registered by name. Core four required in every council: **Librarian, Judge, Contrarian, Researcher**. User-defined roles added per council.
- **The blind wall** — the judge sees *only* the arguments as structured, schema-validated findings. Its brief is assembled by code from a field whitelist, then linted (word n-gram overlap) against everything the judge must never have seen: the raw problem statement, the librarian's brief, and all source documents. A leak **refuses the brief** (exit 2). The wall is enforced in code, not by prompt.
- **Rulings are fact** — a blind ruling is sealed, immutable, and returned to the council as a fact. Members work within it; dissent is recorded, not re-litigated.
- **The record is append-only** — every event (charter, problem, finding, ruling, digest, recommendation, close) is a hash-chained JSONL ledger with schema-validated payloads. Tamper-detectable, replayable, auditable.
- **Local-first** — every role runs on the Hermes config-default model (local) by default. A charter may name a different model per role (decorrelation), which is an explicit Tier-B decision, never a default.

The engine (`scripts/council.py`) is a single stdlib+PyYAML+jsonschema file: **code does the determinism** (state machine, ledger, consensus math, the wall); the **orchestrating agent does the judgment** (runs each role as an isolated subagent per `SKILL.md`).

## Layout

```
council.py                # the deterministic engine (single file, by design)
SKILL.md                  # Hermes skill: the orchestration runbook
references/schemas/       # JSON Schemas: finding, ruling, recommendation, event
references/roles/         # core role cards (librarian, judge, contrarian, researcher)
templates/charter.yaml    # annotated charter template
examples/                 # sample council charters (also test fixtures)
docs/ARCHITECTURE.md      # the architecture document (the spec)
tests/                    # unittest suite (stdlib, zero config)
```

## Quickstart

```bash
SYNOD=~/.hermes/skills/council        # symlinked to this repo (or the repo path)

# 1. Register a council (charter = its roster)
python3 $SYNOD/scripts/council.py register examples/architecture-advisory/charter.yaml

# 2. Scaffold a run from a written problem
python3 $SYNOD/scripts/council.py scaffold examples/architecture-advisory/charter.yaml --problem-file problem.txt
# -> prints the run dir: ~/.hermes/councils/<name>/runs/<YYYYMMDD-HHMM>/

# 3. Convene per SKILL.md: run the member roles as subagents, each
#    output validated into the ledger, rounds checked for consensus:
python3 $SYNOD/scripts/council.py check <run-dir>

# 4. On impasse only: assemble the blind brief (the wall lints it),
#    run the judge subagent, seal its ruling:
python3 $SYNOD/scripts/council.py judge-brief <run-dir>
python3 $SYNOD/scripts/council.py seal-ruling <run-dir> --ruling-file ruling.json

# 5. Close with the librarian's final recommendation
python3 $SYNOD/scripts/council.py close <run-dir> --recommendation-file recommendation.json

# 6. Audit
python3 $SYNOD/scripts/council.py verify <run-dir>
python3 $SYNOD/scripts/council.py show <name>
```

## Status

- **M1 (MVP):** engine core (ledger, consensus, wall) drafted; CLI, registry, briefs, close, and test suite in flight — see open issues.
- **M2 (extensibility):** per-role model overrides, adaptive stopping, evidence verification.
- **M3 (integration):** migration of existing councils (HOL, QuillMD) onto charters, plugin graduation, cron pattern.

See `docs/ARCHITECTURE.md` for the full specification: process flow, division of labor, data model, phases, and risks.

## License

MIT — see [LICENSE](LICENSE).
