# AGENTS.md — Synod

You are working in the **Synod** repository: a deterministic multi-council deliberation engine for Hermes Agent. This file is your contract. Read it fully before touching anything.

## What this is

`scripts/council.py` is a **single-file Python engine** that implements state, ledger, consensus, and the "blind wall" for councils of LLM subagents. The orchestrating agent (a Hermes agent following `SKILL.md`) runs the *roles*; the engine does everything deterministic. **The engine never calls an LLM and never makes a judgment call** — it validates, computes, and refuses.

The spec is `docs/ARCHITECTURE.md`. **It is normative.** Where this file and the doc disagree on behavior, the doc wins; where this file and the doc disagree on style, this file wins.

## Environment (this machine)

- Python 3.11.15, system `python3`. Dependencies: **stdlib + PyYAML (6.0.3) + jsonschema (4.26.0)** only. Both are installed system-wide. **No venv, no pip installs, no other dependencies, ever.**
- Run tests locally: `python3 -m unittest discover -s tests -v` (stdlib runner; pytest is a CI-only tool, not a repo dependency)
- CI runs the same modules via `pytest tests/ -q` (uv) — the suite must be green under both runners
- Compile check: `python3 -m py_compile scripts/council.py`
- No network access is needed or wanted by the engine or the tests.

## Hard rules (sandbox + safety)

1. **Never run commands that touch anything outside this repository.** In particular: NEVER inspect or read `~/.hermes`, `~/.cargo`, `~/.config`, `~/.opencode`, `/tmp`, or the main Hermes checkout. The sandbox auto-rejects external-directory access and **kills the whole run** — that has happened before and costs an entire dispatch. You do not need to look at anything outside `~/repos/synod`.
2. **Never read, print, or commit secrets** (keys, tokens, `.env` files, keyring).
3. **Never add a dependency**, a service, a container, or network I/O.
4. **Never restructure `scripts/council.py` into a package** (no `synod/` dir, no `__init__.py`). One file, by design, for auditability.
5. **The engine stays deterministic.** No randomness, no network, no user input reads beyond the files passed as arguments. The only clock use is timestamp fields that get stamped into events.
6. **Do not modify `docs/ARCHITECTURE.md`** in feature branches. If the spec is wrong, stop and say so in your final message.
7. **Do not merge or push to `main`.** Work on a branch named `issue/<number>-<short-slug>`, commit atomically, push the branch, stop. Review and merge are done by Winston.

## Code conventions

- Python 3.11, stdlib-first. `argparse` for the CLI. No logging module — `print` JSON to stdout, errors to stderr via `die(code, msg)`.
- **Exit codes (fixed contract):** `0` ok · `1` usage/charter/registry error · `2` blind-wall refusal · `3` ledger chain break · `4` schema validation failure.
- Every public function gets a docstring (one line minimum) stating what it does and any exit-code behavior.
- JSON output: `json.dumps(..., sort_keys=True)`; pretty `indent=1` for files, compact for stdout summaries.
- File writes go through `atomic_write` (tmp + rename). Ledger appends hold `fcntl.flock`.
- Canonical hashing: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` then SHA-256 hex. Genesis `prev_hash` is 64 zeros.
- Schema changes require updating `references/schemas/` **and** the fixture tests in the same commit.
- Tests: **stdlib `unittest` modules** (no pytest import in test code; CI runs them through pytest, which collects unittest modules). One module per concern under `tests/` (`test_ledger.py`, `test_consensus.py`, `test_wall.py`, `test_registry.py`, `test_scaffold.py`, `test_brief.py`, `test_close.py`, `test_cli.py`). Tests build fixtures in `tempfile.TemporaryDirectory` and override `COUNCILS_ROOT` with a temp dir via `patch.object`; they never touch the real `~/.hermes`.
- Style: 4-space indent, no type-annotation ceremony, prefer explicit over clever. A reviewer must be able to audit every line in one sitting — that is the point of one file.

## Definition of done (every issue)

1. `python3 -m py_compile scripts/council.py` clean.
2. `python3 -m unittest discover -s tests -v` green, **including the pre-existing tests** — never weaken or delete an existing test case to make the suite pass. If an existing case contradicts your issue, stop and report.
3. The issue's acceptance criteria verifiable by running the listed commands.
4. Commit message: `issue/<n>: <imperative summary>`; no co-author trailers, no emoji.

## Dispatch etiquette

- You receive ONE issue at a time. Do the issue, verify per the definition of done, push the branch, and report: files changed, test results (paste the final unittest summary line), and any spec questions.
- If the issue is ambiguous, state your interpretation in the final message rather than guessing silently.
- Do not create new issues, labels, or milestones. Do not touch `.github/`.
