#!/usr/bin/env python3
"""council.py -- deterministic engine for the Council Framework.

Architecture: ~/.hermes/plans/2026-08-23_council-framework-architecture.md
Design law 4: code does the deterministic (state, schemas, hash chain,
consensus math, blind wall); LLMs do the judgment (findings, rulings,
synthesis). This file contains NO LLM calls.

Exit codes: 0 ok | 1 usage/charter | 2 wall rejected | 3 chain broken | 4 schema
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import yaml

CORE_ROLES = ("librarian", "judge", "contrarian", "researcher")
GENESIS = "0" * 64
NGRAM_N = 10          # wall: n-gram length
NGRAM_REJECT_AT = 1   # wall: shared n-grams that trigger refusal
COUNCILS_ROOT = Path(os.path.expanduser("~/.hermes/councils"))
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "references" / "schemas"
PAYLOAD_SCHEMA = {"finding": "finding", "ruling": "ruling", "recommendation": "recommendation"}
EVENT_TYPES = ["charter", "problem", "finding", "vote", "ruling", "digest",
               "recommendation", "close"]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def die(code, msg):
    """Emit the error (JSON on stdout for machines, a line on stderr) and exit."""
    print(json.dumps({"error": msg}, sort_keys=True))
    print(f"council: {msg}", file=sys.stderr)
    sys.exit(code)


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


_SCH = {}


def schema(name):
    if name not in _SCH:
        _SCH[name] = json.load(open(SCHEMA_DIR / f"{name}.schema.json"))
    return _SCH[name]


# ----------------------------------------------------------------- ledger

def ledger_path(run):
    return Path(run) / "ledger.jsonl"


def read_events(run):
    p = ledger_path(run)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def validate_payload(etype, payload):
    if etype in PAYLOAD_SCHEMA:
        try:
            jsonschema.validate(payload, schema(PAYLOAD_SCHEMA[etype]))
        except jsonschema.ValidationError as e:
            die(4, f"{etype} payload invalid: {e.message} (at {'/'.join(map(str, e.absolute_path)) or 'root'})")


def append_event(run, etype, payload, role, model=None):
    """Validate, stamp, append one hash-chained event. Returns the event.

    Exits 4 on schema violation of the payload or event envelope; exits 1
    on a non-binding ruling (design law 3).
    """
    p = ledger_path(run)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        events = read_events(run)
        prev = events[-1] if events else None
        seq = (prev["seq"] + 1) if prev else 1
        if etype in ("finding", "ruling"):
            prefix = "f" if etype == "finding" else "r"
            ids = [e["payload"].get("id") or "" for e in events if e["type"] == etype]
            nums = [int(i.split("-")[1]) for i in ids if re.match(rf"^{prefix}-[0-9]+$", i)]
            payload = dict(payload)
            payload["id"] = f"{prefix}-{max(nums or [0]) + 1:03d}"
        if etype == "ruling":
            if payload.get("binding") is not True:
                die(1, "rulings are binding (design law 3); non-binding ruling refused")
            # The seal time is an engine fact, not judge knowledge: always stamp
            # (a judge-supplied value is a placeholder it cannot know).
            payload["sealed_at"] = now_iso()
        validate_payload(etype, payload)
        event = {
            "seq": seq,
            "ts": now_iso(),
            "type": etype,
            "payload": payload,
            "provenance": {"role": role, "model": model},
            "prev_hash": sha256_hex(canonical(prev)) if prev else GENESIS,
        }
        try:
            jsonschema.validate(event, schema("event"))
        except jsonschema.ValidationError as e:
            die(4, f"event envelope invalid: {e.message}")
        f.write(canonical(event) + "\n")
    return event


def verify(run):
    """Recompute the whole chain + every schema. Exits 3 on any break."""
    events = read_events(run)
    if not events:
        die(3, "ledger empty or missing")
    prev = None
    for i, ev in enumerate(events):
        try:
            jsonschema.validate(ev, schema("event"))
        except jsonschema.ValidationError as e:
            die(3, f"event {i + 1}: envelope invalid: {e.message}")
        if ev["seq"] != i + 1:
            die(3, f"event {i + 1}: seq out of order ({ev['seq']} != {i + 1})")
        want = sha256_hex(canonical(prev)) if prev else GENESIS
        if ev["prev_hash"] != want:
            die(3, f"event {i + 1}: hash chain broken")
        t = ev["type"]
        if t in PAYLOAD_SCHEMA:
            try:
                jsonschema.validate(ev["payload"], schema(PAYLOAD_SCHEMA[t]))
            except jsonschema.ValidationError as e:
                die(3, f"event {i + 1}: payload invalid: {e.message}")
        prev = ev
    print(json.dumps({"events": len(events), "chain": "ok"}, sort_keys=True))
    return events


# ------------------------------------------------------------- consensus

def charter_of(run):
    ev = read_events(run)
    for e in ev:
        if e["type"] == "charter":
            return e["payload"]
    die(1, "no charter event in ledger; run scaffold first")


def voting_members(charter):
    out = []
    for m in charter["members"]:
        if m["role"] == "judge":
            continue  # a judge with a position cannot rule blindly
        if m.get("votes", True):
            out.append(m["role"])
    return out


def quorum_of(charter):
    c = charter.get("consensus", {})
    if "quorum" in c and isinstance(c["quorum"], int):
        return c["quorum"]
    return len(voting_members(charter)) // 2 + 1  # strict majority


def topic_positions(events):
    """Latest stance per (role, topic) by ledger seq."""
    pos = {}
    for e in events:
        if e["type"] != "finding":
            continue
        p = e["payload"]
        key = (p["role"], p["topic"])
        pos[key] = p["stance"]
    return pos


def find_findings(events):
    return [e for e in events if e["type"] == "finding"]


def un_rebutted_refutes(findings):
    """Refutes with evidence that no later finding explicitly rebuts."""
    by_id = {f["payload"]["id"]: f for f in findings}
    out = []
    for f in findings:
        p = f["payload"]
        if p["stance"] != "refute" or not p.get("evidence"):
            continue
        rebutted = any(
            p["id"] in (by_id[g["payload"]["id"]]["payload"].get("rebutting") or [])
            and g["seq"] > f["seq"]
            for g in findings if g["payload"]["id"] != p["id"]
        )
        if not rebutted:
            out.append(p)
    return out


def round_findings(events, rnd):
    return [f for f in find_findings(events) if f["payload"]["round"] == rnd]


def positions_at(events, upto_round):
    evs = [e for e in events if e["type"] != "finding"
           or e["payload"]["round"] <= upto_round]
    return topic_positions(evs)


def _check_result(run):
    """Compute the consensus/impasse result without printing. Exits 1 if the
    run has no charter event."""
    charter = charter_of(run)
    events = read_events(run)
    findings = find_findings(events)
    quorum = quorum_of(charter)
    voters = voting_members(charter)
    max_rounds = charter.get("consensus", {}).get("max_rounds", 3)
    np_limit = charter.get("consensus", {}).get("no_progress_limit", 2)
    round_ends = [e["payload"].get("round") for e in events
                  if e["type"] == "digest" and e["payload"].get("kind") == "round-end"]
    current_round = max(
        [f["payload"]["round"] for f in findings] + [r for r in round_ends if r],
        default=0,
    )

    rulings = [e for e in events if e["type"] == "ruling"]
    ruled = {r["payload"]["topic"] for r in rulings}

    topics = sorted({f["payload"]["topic"] for f in findings} | ruled)
    pos = topic_positions(events)
    refutes = un_rebutted_refutes(findings)
    refute_by_topic = {}
    for r in refutes:
        refute_by_topic.setdefault(r["topic"], []).append(r["id"])

    topics_out = {}
    contested = []
    for t in topics:
        support = sum(1 for role in voters if pos.get((role, t)) == "support")
        if t in ruled:
            state = "ruled"
        elif support >= quorum and t not in refute_by_topic:
            state = "resolved"
        else:
            state = "contested"
        topics_out[t] = {
            "state": state, "support": support, "quorum": quorum,
            "un_rebutted_refutes": refute_by_topic.get(t, []),
        }
        if state == "contested":
            contested.append(t)

    # no-progress: no new findings AND no position change vs prior round
    no_progress_streak = 0
    if current_round >= 2:
        for rnd in range(current_round, 0, -1):
            new = round_findings(events, rnd)
            changed = any(
                positions_at(events, rnd).get(k) != positions_at(events, rnd - 1).get(k)
                for k in set(positions_at(events, rnd)) | set(positions_at(events, rnd - 1))
            )
            if not new and not changed:
                no_progress_streak += 1
            else:
                break

    impasse = False
    reason = None
    if contested and current_round >= max_rounds:
        impasse, reason = True, f"max_rounds({max_rounds}) reached with contested {contested}"
    elif no_progress_streak >= np_limit:
        impasse, reason = True, f"{no_progress_streak} consecutive no-progress rounds"

    action = "judge" if impasse else ("recommend" if not contested else "continue")
    result = {
        "round": current_round, "max_rounds": max_rounds,
        "voting_members": voters, "quorum": quorum,
        "topics": topics_out, "contested_topics": contested,
        "no_progress_streak": no_progress_streak,
        "ruled_topics": sorted(ruled),
        "impasse": impasse, "impasse_reason": reason, "action": action,
    }
    return result


def check(run):
    """Deterministic consensus + impasse check. Prints the result as JSON."""
    result = _check_result(run)
    print(json.dumps(result, sort_keys=True, indent=1))
    return result


# --------------------------------------------------------------- blind wall

def ngrams(text, n=NGRAM_N):
    toks = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def wall_corpus(run):
    """Everything the judge must NEVER see: raw statement, brief, sources."""
    run = Path(run)
    corpus = {}
    for e in read_events(run):
        if e["type"] == "problem":
            corpus["problem-statement"] = e["payload"].get("statement", "")
    pm = run / "problem.md"
    if pm.exists():
        corpus["problem.md"] = pm.read_text()
    src = run / "sources"
    if src.is_dir():
        for f in sorted(src.iterdir()):
            if f.is_file():
                try:
                    corpus[f"sources/{f.name}"] = f.read_text()
                except UnicodeDecodeError:
                    corpus[f"sources/{f.name}"] = ""
    return corpus


def wall_lint(brief_text, corpus):
    """Shared word n-grams between the assembled brief and forbidden corpus."""
    brief_grams = ngrams(brief_text)
    hits = {}
    for name, text in corpus.items():
        shared = brief_grams & ngrams(text)
        if shared:
            hits[name] = sorted(shared)[:5]
    return hits


def judge_brief(run):
    """Assemble the judge's brief from ledger fields ONLY (law 1).

    Whitelist: role, stance, argument, confidence, rebutting, evidence
    {source, claim}. Excluded: evidence.quote_or_excerpt (verbatim source
    text), provenance, digests. Then the n-gram lint runs against the full
    forbidden corpus; any shared NGRAM_N-word span refuses the brief (exit 2).
    """
    run = Path(run)
    chk = _check_result(run)
    contested = chk["contested_topics"]
    if not contested:
        die(1, "no contested topics; there is nothing for the judge to rule on")
    events = read_events(run)
    findings = [f for f in find_findings(events)
                if f["payload"]["topic"] in contested]
    pos = topic_positions(events)
    brief = {
        "assembled_at": now_iso(),
        "wall": {"ngram_n": NGRAM_N, "reject_at": NGRAM_REJECT_AT},
        "contested_topics": {},
        "sealed_rulings": [r["payload"] for r in events if r["type"] == "ruling"],
        "instruction": ("You are a blind judge. You see ONLY the arguments as "
                        "presented below. You have no access to, and no "
                        "knowledge of, the underlying problem statement or "
                        "sources. Rule on each contested topic. Output one "
                        "ruling JSON object per topic, exactly matching the "
                        "council ruling schema."),
    }
    for t in contested:
        tf = [f["payload"] for f in findings if f["payload"]["topic"] == t]
        brief["contested_topics"][t] = {
            "positions": {role: stance for (role, topic), stance in pos.items()
                          if topic == t},
            "findings": [
                {
                    "id": f["id"], "round": f["round"], "role": f["role"],
                    "stance": f["stance"], "argument": f["argument"],
                    "confidence": f["confidence"],
                    "rebutting": f.get("rebutting") or [],
                    "evidence": [
                        {"source": ev["source"], "claim": ev["claim"]}
                        for ev in (f.get("evidence") or [])
                    ],
                }
                for f in tf
            ],
        }
    # the wall: lint the assembled brief against everything forbidden
    brief_text = canonical(brief)
    hits = wall_lint(brief_text, wall_corpus(run))
    jdir = run / "judge"
    jdir.mkdir(parents=True, exist_ok=True)
    if len(hits) >= NGRAM_REJECT_AT:
        diag = {"brief": brief, "corpus_hits": hits,
                "note": "wall refused: brief shares verbatim spans with forbidden corpus. "
                        "Re-run the offending member(s) with stricter paraphrase "
                        "instructions; evidence must cite, argument must paraphrase."}
        atomic_write(jdir / "brief.rejected.json", json.dumps(diag, indent=1))
        die(2, f"blind wall REJECTED: shared spans with "
               + "; ".join(f"{k} ({len(v)})" for k, v in hits.items()))
    atomic_write(jdir / "brief.json", json.dumps(brief, indent=1))
    print(json.dumps({"brief": str(jdir / "brief.json"), "topics": contested,
                      "wall": "clean", "corpus_docs": len(wall_corpus(run))},
                     sort_keys=True))
    return brief


def seal_ruling(run, ruling_file, model=None):
    """Schema-validate a judge ruling, append it sealed, record to judge/."""
    run = Path(run)
    payload = load_yaml(ruling_file) if str(ruling_file).endswith((".yaml", ".yml")) else json.load(open(ruling_file))
    if isinstance(payload, list):
        last = None
        for p in payload:
            last = append_event(run, "ruling", p, role="judge", model=model)
    else:
        last = append_event(run, "ruling", payload, role="judge", model=model)
    rulings = [e["payload"] for e in read_events(run) if e["type"] == "ruling"]
    atomic_write(run / "judge" / "rulings.json", json.dumps(rulings, indent=1))
    print(json.dumps({"sealed": [r["id"] for r in rulings]}, sort_keys=True))
    return last


# ------------------------------------------------------------- CLI commands

TASK_CONTRARIAN = ("For each contested topic produce at least one concrete "
                   "counter-example against the leading support position, or "
                   "record explicitly 'tested A, B, C — no counter-example "
                   "found'. Emit findings only.")
TASK_RESEARCHER = ("For each contested topic gather or verify evidence "
                   "supporting or refuting the open positions. Every evidence "
                   "item needs source + claim + verbatim excerpt. Paraphrase "
                   "in argument; never copy the problem brief or source text "
                   "into argument.")
TASK_DEFAULT = ("Assess the contested topics from your role's duties. Emit "
                "findings (stance, evidence, confidence, rebutting where "
                "applicable).")


def slugify(name):
    """Lowercase a council name into a dir-safe alnum+hyphen slug."""
    s = re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


def fail(msg):
    """Exit 1 (usage/charter error) with the standard JSON+stderr contract."""
    die(1, msg)


def require_run_dir(run):
    """Resolve RUN to a directory; exit 1 if missing or not a directory."""
    p = Path(run).expanduser()
    if not p.is_dir():
        fail(f"run dir not found: {run}")
    return p


def read_json_object(path):
    """Load a JSON object from path; exit 4 if it is not a JSON object."""
    try:
        payload = json.load(open(path))
    except json.JSONDecodeError as e:
        die(4, f"{path}: not valid JSON: {e}")
    if not isinstance(payload, dict):
        die(4, f"{path}: expected a JSON object")
    return payload


def require_problem_event(run):
    """The problem statement from the ledger; exit 1 if none was recorded."""
    for e in read_events(run):
        if e["type"] == "problem":
            return e["payload"]["statement"]
    die(1, "no problem recorded; run record-brief first")


def validate_charter(charter):
    """Validate a charter dict; return it unchanged.

    Shared by scaffold and the registry commands. Exits 1 on any violation,
    and every error names the offending charter field.
    """
    if not isinstance(charter, dict):
        die(1, "charter must be a YAML mapping")
    if "name" not in charter:
        die(1, "charter field 'name' is required")
    if "members" not in charter:
        die(1, "charter field 'members' is required")
    name = charter["name"]
    if not isinstance(name, str) or not name.strip():
        die(1, "charter field 'name' must be a non-empty string")
    members = charter["members"]
    if not isinstance(members, list) or not members:
        die(1, "charter field 'members' must be a non-empty list")
    seen = set()
    for i, m in enumerate(members):
        if not isinstance(m, dict) or not isinstance(m.get("role"), str) \
                or not m["role"].strip():
            die(1, f"charter field 'members[{i}].role' must be a non-empty string")
        if m["role"] in seen:
            die(1, f"charter field 'members' has duplicate role '{m['role']}'")
        seen.add(m["role"])
        if "model" in m and m["model"] is not None and not isinstance(m["model"], str):
            die(1, f"charter field 'members[{i}].model' must be a string or null")
        if "votes" in m and not isinstance(m["votes"], bool):
            die(1, f"charter field 'members[{i}].votes' must be a boolean")
    missing = [r for r in CORE_ROLES if r not in seen]
    if missing:
        die(1, "charter missing core role(s): " + ", ".join(missing))
    consensus = charter.get("consensus")
    if consensus is not None and not isinstance(consensus, dict):
        die(1, "charter field 'consensus' must be a mapping")
    consensus = consensus or {}
    if "quorum" in consensus:
        q = consensus["quorum"]
        voters = len(voting_members(charter))
        if not isinstance(q, int) or isinstance(q, bool) or not (2 <= q <= voters):
            die(1, f"charter field 'consensus.quorum' must be an integer in "
                   f"[2, {voters}] (voting members)")
    max_rounds = consensus.get("max_rounds", 3)
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) \
            or max_rounds < 1:
        die(1, "charter field 'consensus.max_rounds' must be an integer >= 1")
    np_limit = consensus.get("no_progress_limit", 2)
    if not isinstance(np_limit, int) or isinstance(np_limit, bool) \
            or np_limit < 1:
        die(1, "charter field 'consensus.no_progress_limit' must be an integer >= 1")
    return charter


def cmd_scaffold(charter_path, problem_file):
    """Validate a charter, create the run dir, seed the ledger.

    Exit 1 on any charter validation error (each names its field); the run
    dir is created only after the charter passes.
    """
    if not Path(charter_path).is_file():
        die(1, f"charter file not found: {charter_path}")
    try:
        charter = load_yaml(charter_path)
    except yaml.YAMLError as e:
        die(1, f"charter is not valid YAML: {e}")
    charter = validate_charter(charter)
    name = charter["name"]

    slug = slugify(name)
    if not slug:
        die(1, "charter field 'name' does not slug to a usable directory name")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    cdir = COUNCILS_ROOT / slug
    run = cdir / "runs" / stamp
    n = 2
    while run.exists():
        run = cdir / "runs" / f"{stamp}-{n}"
        n += 1
    for sub in ("briefs", "judge", "sources"):
        (run / sub).mkdir(parents=True, exist_ok=True)
    append_event(run, "charter", charter, role="engine")
    if problem_file is not None:
        pf = Path(problem_file).expanduser()
        if not pf.is_file():
            die(1, f"problem file not found: {problem_file}")
        text = pf.read_text(encoding="utf-8")
        if not text.strip():
            die(1, f"problem file is empty: {problem_file}")
        atomic_write(run / "problem.md", text)
        append_event(run, "problem", {"statement": text, "sources": []},
                     role="engine")
    events = len(read_events(run))
    print(json.dumps({"run": str(run.resolve()), "charter": name,
                      "events": events}, sort_keys=True))
    return run


def cmd_record_brief(run, file_path):
    """Record the librarian's problem brief (first `problem` event only).

    Exits 1 on empty input or if a problem event already exists.
    """
    run = require_run_dir(run)
    pf = Path(file_path).expanduser()
    if not pf.is_file():
        die(1, f"brief file not found: {file_path}")
    text = pf.read_text(encoding="utf-8")
    if not text.strip():
        die(1, f"brief file is empty: {file_path}")
    if any(e["type"] == "problem" for e in read_events(run)):
        die(1, "problem already recorded; ledger is append-only")
    atomic_write(run / "problem.md", text)
    append_event(run, "problem", {"statement": text, "sources": []},
                 role="librarian")
    print(json.dumps({"problem": str(run / "problem.md")}, sort_keys=True))


def unique_label_path(base_dir, label):
    """A collision-free path for label under base_dir (-2, -3, ... suffixes)."""
    candidate = base_dir / label
    n = 2
    while candidate.exists():
        candidate = base_dir / f"{label}-{n}"
        n += 1
    return candidate


def cmd_add_source(run, file_path, label):
    """Copy a raw source into the run and record a `digest` event.

    Exits 1 if the source file is missing.
    """
    run = require_run_dir(run)
    pf = Path(file_path).expanduser()
    if not pf.is_file():
        die(1, f"source file not found: {file_path}")
    text = pf.read_text(encoding="utf-8")
    label = label or pf.stem
    dest = unique_label_path(run / "sources", label)
    atomic_write(dest, text)
    append_event(run, "digest",
                 {"kind": "source", "label": dest.name,
                  "sha256": sha256_hex(text), "char_count": len(text)},
                 role="librarian")
    print(json.dumps({"source": str(dest), "label": dest.name}, sort_keys=True))


def member_task(role):
    """The role-specific round task string (engine constant, never LLM text)."""
    if role == "contrarian":
        return TASK_CONTRARIAN
    if role == "researcher":
        return TASK_RESEARCHER
    return TASK_DEFAULT


def cmd_brief(run, round_no, role):
    """Render one member's round-N briefing packet.

    Exits 1 if no problem is recorded, the role is not a charter member, or
    the role is the judge (the wall: the judge never receives a packet).
    """
    run = require_run_dir(run)
    if round_no < 1:
        fail("round must be an integer >= 1")
    charter = charter_of(run)
    members = [m["role"] for m in charter["members"]]
    if role not in members:
        fail(f"role '{role}' is not a member of this charter")
    if role == "judge":
        fail("wall: judge never receives a briefing packet")
    statement = require_problem_event(run)
    problem_md = run / "problem.md"
    if problem_md.is_file():
        problem_text = problem_md.read_text(encoding="utf-8")
    else:
        problem_text = statement
    events = read_events(run)
    prior = [e for e in events if e["type"] != "finding"
             or e["payload"]["round"] < round_no]
    pos = {}
    for (r, t), s in topic_positions(prior).items():
        pos.setdefault(t, {})[r] = s
    refutes = un_rebutted_refutes([e for e in prior if e["type"] == "finding"])
    packet = {
        "council": charter.get("name"),
        "role": role,
        "round": round_no,
        "problem_brief": problem_text,
        "ledger_view": {
            "positions": pos,
            "open_refutes": [
                {"id": p["id"], "role": p["role"], "topic": p["topic"],
                 "stance": p["stance"], "argument": p["argument"],
                 "confidence": p["confidence"],
                 "evidence": [{"source": ev["source"], "claim": ev["claim"]}
                              for ev in (p.get("evidence") or [])]}
                for p in refutes
            ],
            "sealed_rulings": [e["payload"] for e in events
                               if e["type"] == "ruling"],
        },
        "task": member_task(role),
    }
    dest = run / "briefs" / f"round-{round_no:02d}" / f"{role}.json"
    atomic_write(dest, json.dumps(packet, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"brief": str(dest), "role": role, "round": round_no},
                     sort_keys=True))
    return packet


def cmd_finding(run, file_path, role, model):
    """Ingest one member finding file into the ledger.

    Exits 4 on schema violation, 1 on role mismatch, a non-member role, or a
    --model that contradicts the charter's declared model for that role
    (provenance is an engine fact, not a member claim).
    """
    run = require_run_dir(run)
    charter = charter_of(run)
    if role not in [m["role"] for m in charter["members"]]:
        die(1, f"role '{role}' is not a member of this charter")
    member = next(m for m in charter["members"] if m["role"] == role)
    declared = member.get("model")
    if declared is not None and model is not None and model != declared:
        die(1, f"provenance mismatch: role '{role}' is chartered to model "
            f"'{declared}' but the finding records '{model}'")
    payload = read_json_object(file_path)
    if payload.get("role") != role:
        die(1, "role mismatch: finding file role "
            f"'{payload.get('role')}' does not match --role '{role}'")
    event = append_event(run, "finding", payload, role=role,
                         model=declared if model is None else model)
    print(json.dumps({"finding": event["payload"]["id"],
                      "round": event["payload"]["round"],
                      "topic": event["payload"]["topic"]}, sort_keys=True))
    return event


def cmd_note_round(run, round_no):
    """Record a round-end digest with the round's finding count."""
    run = require_run_dir(run)
    if round_no < 1:
        fail("round must be an integer >= 1")
    count = len(round_findings(read_events(run), round_no))
    append_event(run, "digest",
                 {"kind": "round-end", "round": round_no,
                  "finding_count": count},
                 role="engine")
    print(json.dumps({"round": round_no, "finding_count": count},
                     sort_keys=True))


def cmd_verify(run):
    """Recompute the ledger chain and schemas; exits 3 on any break."""
    require_run_dir(run)
    verify(run)


def cmd_check(run):
    """Run the deterministic consensus/impasse check and print the result."""
    require_run_dir(run)
    return check(run)


def cmd_judge_brief(run):
    """Assemble the blind judge brief (wall lint; exits 2 on a leak)."""
    require_run_dir(run)
    return judge_brief(run)


def cmd_seal_ruling(run, ruling_file, model=None):
    """Validate and seal a judge ruling (or list of rulings).

    If --model is not given, the charter's declared judge model is used (or
    null for the config default), so the ruling's provenance matches the
    finding behavior and the report's heterogeneity budget is accurate.
    """
    run = require_run_dir(run)
    if model is None:
        judge = next((m for m in charter_of(run)["members"]
                      if m["role"] == "judge"), None)
        model = judge.get("model") if judge else None
    return seal_ruling(run, ruling_file, model=model)


def cmd_close(run, rec_file):
    """Validate the final recommendation, append close, write the reports.

    Exits 4 on schema violation, 1 if any sealed ruling id is missing from
    rulings_applied, or if the run is already closed.
    """
    run = require_run_dir(run)
    if any(e["type"] == "close" for e in read_events(run)):
        die(1, "run already closed; ledger is append-only")
    rec = read_json_object(rec_file)
    validate_payload("recommendation", rec)
    events = read_events(run)
    sealed = [e["payload"]["id"] for e in events if e["type"] == "ruling"]
    applied = set(rec.get("rulings_applied") or [])
    missing = [r for r in sealed if r not in applied]
    if missing:
        die(1, "recommendation missing sealed ruling id(s): " + ", ".join(missing))
    append_event(run, "recommendation", rec, role="librarian")
    chk = _check_result(run)
    close_payload = {
        "status": "closed",
        "topics": sorted(chk["topics"]),
        "ruling_count": len(sealed),
        "round": chk["round"],
    }
    append_event(run, "close", close_payload, role="librarian")

    council_name = charter_of(run).get("name")
    topics_out = chk["topics"]
    rec_lines = [f"# Recommendation — {council_name}", ""]
    rec_lines += [f"**Verdict:** {rec['recommendation']}", "",
                  f"**Confidence:** {rec['confidence']}", "",
                  "## Per-topic outcomes", ""]
    for t in sorted(topics_out):
        info = topics_out[t]
        rec_lines.append(f"- {t}: {info['state']}")
    if rec.get("resolved"):
        rec_lines += ["", "## Resolved (per the librarian)"]
        for r in rec["resolved"]:
            rec_lines.append(f"- {r['topic']}: {r['outcome']}")
    dissent = rec.get("dissent") or []
    rec_lines += ["", "## Dissenting views", ""]
    if dissent:
        for d in dissent:
            rec_lines.append(f"- {d['role']} on {d['topic']}: {d['position']}")
    else:
        rec_lines.append("- none recorded")
    rec_lines += ["", "## Rulings applied", ""]
    if sealed:
        for r in sealed:
            p = next(e["payload"] for e in events
                     if e["type"] == "ruling" and e["payload"]["id"] == r)
            rec_lines.append(f"- {r} ({p['topic']}): {p['ruling']}")
    else:
        rec_lines.append("- none")
    atomic_write(run / "recommendation.md", "\n".join(rec_lines) + "\n")

    findings = find_findings(events)
    # Heterogeneity budget (spec 2.2): which model actually produced each
    # finding, read from the ledger's provenance. null = config default.
    hetero = {}
    for e in events:
        if e["type"] == "finding":
            prov = e.get("provenance") or {}
            hetero.setdefault(prov.get("model"), set()).add(
                e["payload"]["role"])
    judge_model = None
    for e in events:
        if e["type"] == "ruling":
            judge_model = (e.get("provenance") or {}).get("model")
            break
    report_lines = [
        f"# Council report — {council_name}",
        "",
        f"- Run dir: {run}",
        f"- Rounds elapsed: {chk['round']}",
        f"- Findings: {len(findings)}",
        f"- Rulings: {', '.join(sealed) if sealed else 'none'}",
        f"- Final verdict: {rec['recommendation']}",
        "",
        "## Heterogeneity budget",
        "",
    ]
    if judge_model is not None:
        report_lines.append(f"- judge: {judge_model}")
    else:
        report_lines.append("- judge: config default (no charter override)")
    for model in sorted(hetero, key=lambda m: (m is None, m or "")):
        label = model if model is not None else "config default"
        roles = ", ".join(sorted(hetero[model]))
        report_lines.append(f"- {label}: {roles}")
    if len(hetero) > 1 or judge_model is not None:
        report_lines.append(
            "- decorrelation: heterogeneous (charter-explicit overrides active)")
    else:
        report_lines.append(
            "- decorrelation: single model (all roles on the same model; "
            "correlated-error risk untested in this run)")
    report_lines += [
        "",
        "## Summary",
        "",
        rec["recommendation"],
        "",
    ]
    atomic_write(run / "report.md", "\n".join(report_lines) + "\n")
    print(json.dumps({"closed": True, "run": str(run.resolve()),
                      "rulings": len(sealed)}, sort_keys=True))


# ---------------------------------------------------------------- registry

REGISTRY_NAME = "registry.yaml"
COUNCIL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def registry_path():
    """The registry file path under COUNCILS_ROOT (registry.yaml)."""
    return COUNCILS_ROOT / REGISTRY_NAME


def load_registry():
    """The registry's council map; an empty map when the file does not exist.

    The file shape (ARCHITECTURE.md S6.1) is a single top-level ``councils:``
    mapping of name -> {charter, description, status, registered_at}. Exits 1
    on a malformed or non-object registry file.
    """
    p = registry_path()
    if not p.exists():
        return {}
    data = None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        die(1, f"registry is not valid YAML: {e}")
    if data is None:
        return {}
    if not isinstance(data, dict) or "councils" not in data:
        die(1, "registry must be a mapping with a top-level 'councils' key")
    councils = data["councils"]
    if councils is None:
        return {}
    if not isinstance(councils, dict):
        die(1, "registry 'councils' must be a mapping")
    return councils


def save_registry(councils):
    """Write the registry atomically under the ``councils:`` wrapper."""
    text = yaml.safe_dump({"councils": councils}, sort_keys=True,
                          default_flow_style=False, allow_unicode=True)
    atomic_write(registry_path(), text)


def require_name(name):
    """Reduce a council name to its lowercase alnum+hyphen slug (run-dir rule).

    Exits 1 if the name does not slug to a usable council name.
    """
    slug = slugify(name)
    if not slug or not COUNCIL_NAME_RE.match(slug):
        fail(f"council name must be a lowercase alnum+hyphen slug: '{name}'")
    return slug


def cmd_validate_charter(path):
    """Validate a charter YAML file and print its effective summary.

    Prints {"valid": true, "name", "members", "quorum"} on success; exits 1
    with a field-naming error otherwise.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        die(1, f"charter file not found: {path}")
    try:
        charter = load_yaml(p)
    except yaml.YAMLError as e:
        die(1, f"charter is not valid YAML: {e}")
    charter = validate_charter(charter)
    print(json.dumps({
        "valid": True,
        "name": charter["name"],
        "members": [m["role"] for m in charter["members"]],
        "quorum": quorum_of(charter),
    }, sort_keys=True))
    return charter


def cmd_register(path):
    """Validate a charter and record it in the registry under its slug.

    Exits 1 if the file is missing, the name is not a usable slug, or the
    council is already registered and active. A retired council may be
    re-registered (its entry is replaced, status back to ``active``).
    Prints {"registered", "registry"}.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        die(1, f"charter file not found: {path}")
    try:
        charter = load_yaml(p)
    except yaml.YAMLError as e:
        die(1, f"charter is not valid YAML: {e}")
    charter = validate_charter(charter)
    name = require_name(charter["name"])
    reg = load_registry()
    if name in reg and reg[name].get("status") == "active":
        fail(f"council '{name}' is already registered; deregister first")
    reg[name] = {
        "charter": str(p.resolve()),
        "description": charter.get("problem_domain") or "",
        "status": "active",
        "registered_at": now_iso(),
    }
    save_registry(reg)
    print(json.dumps({"registered": name,
                      "registry": str(registry_path())}, sort_keys=True))
    return name


def cmd_deregister(name):
    """Mark a council retired. Exits 1 if the name is unknown.

    Per ARCHITECTURE.md S6.3 the registry is a discovery surface, not a
    garbage collector: deregister marks ``status: retired`` and leaves the
    entry (and any runs) for human retention decisions.
    """
    slug = require_name(name)
    reg = load_registry()
    if slug not in reg:
        fail(f"council '{slug}' is not registered")
    reg[slug]["status"] = "retired"
    save_registry(reg)
    print(json.dumps({"deregistered": slug, "status": "retired"},
                     sort_keys=True))


def cmd_list():
    """List registered councils sorted by name (an empty list is fine).

    Retired councils are listed too, with their status, so the registry
    stays a complete discovery surface.
    """
    reg = load_registry()
    councils = [{"name": n, "charter": e.get("charter"),
                 "description": e.get("description"),
                 "status": e.get("status", "active"),
                 "registered_at": e.get("registered_at")}
                for n, e in sorted(reg.items())]
    print(json.dumps({"councils": councils}, sort_keys=True))
    return councils


def cmd_show(name):
    """Show a registered council's roster summary.

    Exits 1 if the name is unknown, if the registered charter file no longer
    exists (the path is named), or if the charter no longer validates.
    Retired councils are still shown, with their status.
    """
    slug = require_name(name)
    reg = load_registry()
    if slug not in reg:
        fail(f"council '{slug}' is not registered")
    entry = reg[slug]
    charter_path = Path(entry.get("charter") or "")
    if not charter_path.is_file():
        die(1, f"charter file no longer exists: {charter_path}")
    try:
        charter = load_yaml(charter_path)
    except yaml.YAMLError as e:
        die(1, f"charter is not valid YAML: {e}")
    charter = validate_charter(charter)
    print(json.dumps({
        "name": slug,
        "charter": str(charter_path),
        "description": entry.get("description"),
        "status": entry.get("status", "active"),
        "registered_at": entry.get("registered_at"),
        "members": [m["role"] for m in charter["members"]],
        "quorum": quorum_of(charter),
        "core_roles_complete": True,
    }, sort_keys=True))
    return charter


# ------------------------------------------------------------------- CLI

class CouncilArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors follow the die(1, ...) contract.

    argparse's default error() exits 2; the fixed exit-code contract
    (AGENTS.md) reserves 1 for usage errors, so every usage error is routed
    through die(), which emits the JSON {"error": ...} object on stdout.
    """

    def error(self, message):
        """Route an argparse usage error through die() (exit 1 + JSON)."""
        die(1, message)


def build_parser():
    """The argparse tree for the whole command surface."""
    parser = CouncilArgumentParser(
        prog="council.py",
        description="Synod: deterministic multi-council deliberation engine. "
                    "Code does the determinism; the orchestrator does the judgment.")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND",
                                parser_class=CouncilArgumentParser)

    p = sub.add_parser("scaffold",
                       help="validate a charter and create a new run dir")
    p.add_argument("charter", help="path to the charter YAML")
    p.add_argument("--problem-file", help="optional problem statement file")
    p.set_defaults(func=lambda a: cmd_scaffold(a.charter, a.problem_file))

    p = sub.add_parser("record-brief",
                       help="record the librarian's problem brief (first only)")
    p.add_argument("run", help="run directory")
    p.add_argument("--file", required=True, help="path to the brief text file")
    p.set_defaults(func=lambda a: cmd_record_brief(a.run, a.file))

    p = sub.add_parser("add-source",
                       help="copy a raw source into the run and log a digest")
    p.add_argument("run", help="run directory")
    p.add_argument("--file", required=True, help="path to the source file")
    p.add_argument("--label", help="label under sources/ (default: file stem)")
    p.set_defaults(func=lambda a: cmd_add_source(a.run, a.file, a.label))

    p = sub.add_parser("brief",
                       help="render one member's round-N briefing packet")
    p.add_argument("run", help="run directory")
    p.add_argument("--round", type=int, required=True, help="round number")
    p.add_argument("--role", required=True, help="member role slug")
    p.set_defaults(func=lambda a: cmd_brief(a.run, a.round, a.role))

    p = sub.add_parser("finding",
                       help="validate and append one member finding")
    p.add_argument("run", help="run directory")
    p.add_argument("--file", required=True, help="path to the finding JSON")
    p.add_argument("--role", required=True, help="emitting role slug")
    p.add_argument("--model", help="model id for provenance (default null)")
    p.set_defaults(func=lambda a: cmd_finding(a.run, a.file, a.role, a.model))

    p = sub.add_parser("note-round",
                       help="record a round-end digest with finding count")
    p.add_argument("run", help="run directory")
    p.add_argument("--round", type=int, required=True, help="round number")
    p.set_defaults(func=lambda a: cmd_note_round(a.run, a.round))

    p = sub.add_parser("verify",
                       help="recompute the ledger hash chain and schemas")
    p.add_argument("run", help="run directory")
    p.set_defaults(func=lambda a: cmd_verify(a.run))

    p = sub.add_parser("check",
                       help="deterministic consensus and impasse check")
    p.add_argument("run", help="run directory")
    p.set_defaults(func=lambda a: cmd_check(a.run))

    p = sub.add_parser("judge-brief",
                       help="assemble the blind judge brief (wall lint)")
    p.add_argument("run", help="run directory")
    p.set_defaults(func=lambda a: cmd_judge_brief(a.run))

    p = sub.add_parser("seal-ruling",
                       help="validate and seal a judge ruling as fact")
    p.add_argument("run", help="run directory")
    p.add_argument("--ruling-file", required=True,
                   help="path to ruling JSON/YAML (object or array)")
    p.add_argument("--model",
                   help="judge model id for provenance (default: charter's "
                        "judge model, or null)")
    p.set_defaults(func=lambda a: cmd_seal_ruling(a.run, a.ruling_file, a.model))

    p = sub.add_parser("close",
                       help="validate the recommendation and close the run")
    p.add_argument("run", help="run directory")
    p.add_argument("--recommendation-file", required=True,
                   help="path to the recommendation JSON")
    p.set_defaults(func=lambda a: cmd_close(a.run, a.recommendation_file))

    p = sub.add_parser("validate-charter",
                       help="validate a charter YAML and print its summary")
    p.add_argument("path", help="path to the charter YAML")
    p.set_defaults(func=lambda a: cmd_validate_charter(a.path))

    p = sub.add_parser("register",
                       help="validate a charter and register it by name")
    p.add_argument("path", help="path to the charter YAML")
    p.set_defaults(func=lambda a: cmd_register(a.path))

    p = sub.add_parser("deregister",
                       help="remove a council from the registry")
    p.add_argument("name", help="registered council name")
    p.set_defaults(func=lambda a: cmd_deregister(a.name))

    p = sub.add_parser("list",
                       help="list registered councils (sorted by name)")
    p.set_defaults(func=lambda a: cmd_list())

    p = sub.add_parser("show",
                       help="show a registered council's roster summary")
    p.add_argument("name", help="registered council name")
    p.set_defaults(func=lambda a: cmd_show(a.name))

    return parser


def _exit_code(exc):
    """Normalize a SystemExit to an int exit code (None -> 0, str -> 1)."""
    code = exc.code
    if code is None:
        return 0
    return code if isinstance(code, int) else 1


def main(argv=None):
    """CLI entry point; returns the process exit code.

    Every non-zero exit has already emitted a JSON {"error": ...} object on
    stdout (die() does this, and usage errors are routed through die() by
    CouncilArgumentParser.error). main catches the SystemExit those raise
    and converts it to a return code; unexpected exceptions are also
    reported as a JSON error object (exit 1).
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return _exit_code(e)
    try:
        if not getattr(args, "func", None):
            parser.print_help(sys.stderr)
            die(1, "no command given")
        args.func(args)
    except SystemExit as e:
        return _exit_code(e)
    except Exception as e:
        print(json.dumps({"error": f"unexpected: {e}"}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
