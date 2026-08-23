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
    """Validate, stamp, append one hash-chained event. Returns the event."""
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
            payload["sealed_at"] = payload.get("sealed_at") or now_iso()
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


def check(run):
    """Deterministic consensus + impasse check. Prints JSON; exits 0."""
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
    chk = check(run)
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


def seal_ruling(run, ruling_file):
    """Schema-validate a judge ruling, append it sealed, record to judge/."""
    run = Path(run)
    payload = load_yaml(ruling_file) if str(ruling_file).endswith((".yaml", ".yml")) else json.load(open(ruling_file))
    if isinstance(payload, list):
        last = None
        for p in payload:
            last = append_event(run, "ruling", p, role="judge")
    else:
        last = append_event(run, "ruling", payload, role="judge")
    rulings = [e["payload"] for e in read_events(run) if e["type"] == "ruling"]
    atomic_write(run / "judge" / "rulings.json", json.dumps(rulings, indent=1))
    print(json.dumps({"sealed": [r["id"] for r in rulings]}, sort_keys=True))
    return last
