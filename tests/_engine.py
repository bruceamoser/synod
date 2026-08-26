"""Shared helpers: engine loader (importlib) and ledger fixture builders."""
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_PATH = REPO_ROOT / "scripts" / "council.py"


def load_engine():
    """Load scripts/council.py as a module (the file is not a package)."""
    spec = importlib.util.spec_from_file_location("council_engine", ENGINE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["council_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


engine = load_engine()

CORE_CHARTER_MEMBERS = [
    {"role": "librarian", "model": None},
    {"role": "judge", "model": None},
    {"role": "contrarian", "model": None},
    {"role": "researcher", "model": None},
]


def charter_payload(max_rounds=3, no_progress_limit=2, quorum=None,
                    reject_quorum=None, members=None):
    """A locked-charter payload for the 'charter' ledger event."""
    consensus = {"max_rounds": max_rounds, "no_progress_limit": no_progress_limit}
    if quorum is not None:
        consensus["quorum"] = quorum
    if reject_quorum is not None:
        consensus["reject_quorum"] = reject_quorum
    return {
        "name": "test-council",
        "core": ["librarian", "judge", "contrarian", "researcher"],
        "members": [dict(m) for m in (members or CORE_CHARTER_MEMBERS)],
        "consensus": consensus,
    }


def make_run(tmp, charter=None, with_problem=True, problem_text=None, sources=None):
    """Create a run dir under tmp with a charter event appended.

    Also writes problem.md and sources/ when requested (wall corpus).
    Returns the run dir (Path).
    """
    run = Path(tmp) / "run"
    (run / "sources").mkdir(parents=True)
    engine.append_event(run, "charter", charter or charter_payload(), role="engine")
    if with_problem:
        text = problem_text or "Default problem statement for tests."
        (run / "problem.md").write_text(text, encoding="utf-8")
        engine.append_event(
            run, "problem",
            {"statement": text, "sources": sorted(sources or [])},
            role="librarian",
        )
    for name, text in (sources or {}).items():
        (run / "sources" / name).write_text(text, encoding="utf-8")
    return run


def finding(role, topic, stance, round_no=1, argument=None, evidence=None,
            rebutting=None, confidence=0.8):
    """A finding payload without 'id' (the engine assigns it on append)."""
    payload = {
        "round": round_no,
        "role": role,
        "topic": topic,
        "stance": stance,
        "argument": argument or "A sufficiently long argument for the test fixture.",
        "evidence": evidence if evidence is not None else [
            {"source": "reasoning", "claim": "the worked case supports this",
             "quote_or_excerpt": "n/a"},
        ],
        "confidence": confidence,
    }
    if rebutting is not None:
        payload["rebutting"] = rebutting
    return payload


def ruling(topic, point=None, decision=None):
    """A ruling payload without 'id'/'sealed_at' (the engine assigns both)."""
    return {
        "topic": topic,
        "point_of_contention": point or "Which position on the topic must stand?",
        "ruling": decision or "The council must adopt the supported position.",
        "reasoning": "The arguments as presented favor the supported position.",
        "conditions": [],
        "binding": True,
    }


def write_charter(tmp, name="test-council", members=None, consensus=None):
    """Write a charter YAML file under tmp and return its path (Path).

    Defaults to the core four; override members/consensus to exercise
    validation. Uses yaml.safe_dump so the engine's load_yaml reads it back.
    """
    import yaml
    charter = {
        "name": name,
        "members": [dict(m) for m in (members or CORE_CHARTER_MEMBERS)],
        "consensus": (dict(consensus) if consensus is not None
                      else {"max_rounds": 3, "no_progress_limit": 2}),
    }
    path = Path(tmp) / "charter.yaml"
    path.write_text(yaml.safe_dump(charter, sort_keys=True), encoding="utf-8")
    return path


def ruling_event_id(run, topic):
    """The auto-assigned id (r-00N) of the first ruling event for a topic."""
    for e in engine.read_events(run):
        if e["type"] == "ruling" and e["payload"].get("topic") == topic:
            return e["payload"]["id"]
    raise AssertionError(f"no ruling event for topic {topic!r}")


def run_dir_for_subprocess(tmp):
    """A bare run dir (no charter) for subprocess concurrency tests."""
    run = Path(tmp) / "run"
    run.mkdir(parents=True)
    return run
