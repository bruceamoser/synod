"""Registry tests: validate_charter, the register/deregister/list/show round
trip, and the shipped charter fixtures (template + examples).

Loads scripts/council.py via importlib (the engine is a single file, not a
package) and drives it in-process through engine.main(). COUNCILS_ROOT is
patched to a temp dir so the real ~/.hermes is never touched.
"""
import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from _engine import REPO_ROOT, engine, write_charter

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

CORE_MEMBER_ROLES = ["librarian", "judge", "contrarian", "researcher"]


def cli(argv, root):
    """Run engine.main(argv) with COUNCILS_ROOT -> root.

    Returns (exit_code, parsed_stdout_json_or_None, stderr_text). Every
    non-zero exit carries a JSON {"error": ...} object on stdout.
    """
    out, err = io.StringIO(), io.StringIO()
    with patch.object(engine, "COUNCILS_ROOT", Path(root)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = engine.main(list(argv))
        except SystemExit as e:
            code = e.code
    text = out.getvalue()
    parsed = json.loads(text) if text.strip() else None
    return (code if isinstance(code, int) else 1), parsed, err.getvalue()


def charter_dict(**overrides):
    """A valid charter dict; override fields to exercise validation."""
    base = {
        "name": "unit-council",
        "members": [{"role": r} for r in CORE_MEMBER_ROLES],
        "consensus": {"max_rounds": 3, "no_progress_limit": 2},
    }
    base.update(overrides)
    return base


def load_councils(tmp) -> dict:
    """Parse the registry.yaml under tmp into its council map ({} if absent)."""
    p = Path(tmp) / "registry.yaml"
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    councils = data.get("councils")
    return councils if isinstance(councils, dict) else {}


class ValidateCharterUnitTest(unittest.TestCase):
    """engine.validate_charter(charter_dict) unit tests.

    Each rejection exits 1 and names the offending field; a valid charter
    passes and is returned unchanged.
    """

    def _violation(self, charter):
        """Run validate_charter; return (exit_code, stderr text)."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                engine.validate_charter(charter)
        return cm.exception.code, err.getvalue()

    def test_valid_charter_passes_and_is_returned(self):
        charter = charter_dict()
        self.assertIs(engine.validate_charter(charter), charter)

    def test_valid_charter_with_custom_role_and_explicit_quorum_passes(self):
        charter = charter_dict(
            members=[{"role": r} for r in CORE_MEMBER_ROLES]
            + [{"role": "security", "votes": True}],
            consensus={"quorum": 3, "max_rounds": 2, "no_progress_limit": 2},
        )
        self.assertIs(engine.validate_charter(charter), charter)

    def test_missing_core_role_exits_1_and_names_it(self):
        for role in CORE_MEMBER_ROLES:
            with self.subTest(role=role):
                members = [m for m in charter_dict()["members"]
                           if m["role"] != role]
                code, err = self._violation(charter_dict(members=members))
                self.assertEqual(code, 1)
                self.assertIn(role, err)

    def test_duplicate_member_role_exits_1_and_names_it(self):
        members = charter_dict()["members"] + [{"role": "librarian"}]
        code, err = self._violation(charter_dict(members=members))
        self.assertEqual(code, 1)
        self.assertIn("duplicate", err)
        self.assertIn("librarian", err)

    def test_empty_name_exits_1_and_names_it(self):
        code, err = self._violation(charter_dict(name=""))
        self.assertEqual(code, 1)
        self.assertIn("name", err)

    def test_quorum_out_of_range_exits_1_and_names_it(self):
        for q in (1, 4):  # 3 voting members: quorum must be in [2, 3]
            with self.subTest(quorum=q):
                charter = charter_dict(
                    consensus={"quorum": q, "max_rounds": 3,
                               "no_progress_limit": 2})
                code, err = self._violation(charter)
                self.assertEqual(code, 1)
                self.assertIn("quorum", err)

    def test_quorum_not_an_int_exits_1_and_names_it(self):
        for q in ("majority", True):
            with self.subTest(quorum=q):
                charter = charter_dict(
                    consensus={"quorum": q, "max_rounds": 3,
                               "no_progress_limit": 2})
                code, err = self._violation(charter)
                self.assertEqual(code, 1)
                self.assertIn("quorum", err)


class ValidateCharterCommandTest(unittest.TestCase):
    def test_valid_charter_prints_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp)
            code, info, err = cli(["validate-charter", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(info["valid"], True)
            self.assertEqual(info["name"], "test-council")
            self.assertEqual(info["members"], CORE_MEMBER_ROLES)
            self.assertEqual(info["quorum"], 2)  # strict majority of 3 voters

    def test_invalid_charter_exits_1_and_names_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp, consensus={"quorum": 9})
            code, info, err = cli(["validate-charter", str(charter)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("quorum", info["error"])

    def test_missing_file_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, info, err = cli(
                ["validate-charter", str(Path(tmp) / "nope.yaml")], tmp)
            self.assertEqual(code, 1)
            self.assertIn("not found", info["error"])


class RegistryRoundTripTest(unittest.TestCase):
    def _register(self, tmp, name="test-council"):
        charter = write_charter(tmp, name=name)
        code, info, err = cli(["register", str(charter)], tmp)
        self.assertEqual(code, 0, err)
        return info

    def test_register_list_show_deregister_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = self._register(tmp)
            self.assertEqual(info["registered"], "test-council")
            self.assertTrue(info["registry"].endswith("registry.yaml"))

            # the registry file has the documented shape (ARCHITECTURE S6.1):
            # top-level councils: mapping, name -> {charter, description,
            # status, registered_at}
            reg = load_councils(tmp)
            entry = reg["test-council"]
            self.assertEqual(set(entry),
                             {"charter", "description", "status",
                              "registered_at"})
            self.assertEqual(entry["status"], "active")
            # write_charter emits no problem_domain, so description is ""
            self.assertEqual(entry["description"], "")
            self.assertTrue(Path(entry["charter"]).is_absolute())
            self.assertTrue(Path(entry["charter"]).is_file())
            self.assertRegex(entry["registered_at"], TS_RE)

            # list shows the entry
            code, out, err = cli(["list"], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(out["councils"], [{
                "name": "test-council",
                "charter": entry["charter"],
                "description": entry["description"],
                "status": "active",
                "registered_at": entry["registered_at"],
            }])

            # show reflects the charter
            code, out, err = cli(["show", "test-council"], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(out, {
                "name": "test-council",
                "charter": entry["charter"],
                "description": entry["description"],
                "status": "active",
                "registered_at": entry["registered_at"],
                "members": CORE_MEMBER_ROLES,
                "quorum": 2,
                "core_roles_complete": True,
            })

            # deregister MARKS the entry retired; it is not deleted
            code, out, err = cli(["deregister", "test-council"], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(out, {"deregistered": "test-council",
                                   "status": "retired"})
            reg = load_councils(tmp)
            self.assertEqual(reg["test-council"]["status"], "retired")
            # list still surfaces it, now retired
            code, out, err = cli(["list"], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(out["councils"][0]["name"], "test-council")
            self.assertEqual(out["councils"][0]["status"], "retired")
            # a retired council can be re-registered (status back to active)
            info = self._register(tmp)
            self.assertEqual(info["registered"], "test-council")
            self.assertEqual(load_councils(tmp)["test-council"]["status"],
                             "active")

    def test_double_register_refused_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._register(tmp)
            # a second charter file with the SAME name: refused, and the
            # registry still points at the FIRST charter file
            second = Path(tmp) / "second.yaml"
            second.write_text(yaml.safe_dump(charter_dict(name="test-council")),
                              encoding="utf-8")
            code, info, err = cli(["register", str(second)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("already registered", info["error"])
            self.assertIn("deregister first", info["error"])
            reg = load_councils(tmp)
            self.assertEqual(reg["test-council"]["charter"],
                             str((Path(tmp) / "charter.yaml").resolve()))
            self.assertEqual(reg["test-council"]["status"], "active")

    def test_register_missing_file_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, info, err = cli(
                ["register", str(Path(tmp) / "nope.yaml")], tmp)
            self.assertEqual(code, 1)
            self.assertIn("not found", info["error"])
            self.assertFalse((Path(tmp) / "registry.yaml").exists())

    def test_register_invalid_charter_exits_1_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp, members=[{"role": "librarian"}])
            code, info, err = cli(["register", str(charter)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("error", info)
            self.assertFalse((Path(tmp) / "registry.yaml").exists())

    def test_deregister_unknown_name_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, info, err = cli(["deregister", "ghost-council"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("not registered", info["error"])
            self.assertFalse((Path(tmp) / "registry.yaml").exists())

    def test_show_unknown_name_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, info, err = cli(["show", "ghost-council"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("not registered", info["error"])

    def test_show_with_deleted_charter_file_exits_1_and_names_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._register(tmp)
            reg = load_councils(tmp)
            charter_path = Path(reg["test-council"]["charter"])
            charter_path.unlink()
            code, out, err = cli(["show", "test-council"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("no longer exists", out["error"])
            self.assertIn(str(charter_path), out["error"])

    def test_list_sorts_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("zeta-council", "alpha-council", "mid-council"):
                self._register(tmp, name=name)
            code, out, err = cli(["list"], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual([c["name"] for c in out["councils"]],
                             ["alpha-council", "mid-council", "zeta-council"])

    def test_list_on_empty_registry_exits_0_with_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = cli(["list"], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(out, {"councils": []})

    def test_show_non_slug_name_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, info, err = cli(["show", "Not A Slug!"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("slug", info["error"])


class ScaffoldStillStandaloneTest(unittest.TestCase):
    def test_scaffold_works_from_bare_charter_path_without_registration(self):
        """Registration is optional bookkeeping: scaffold never looks at
        the registry, and the run lands under the council's slug dir."""
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp, name="standalone-council")
            code, info, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            run = Path(info["run"])
            self.assertTrue(run.is_dir())
            self.assertEqual(info["events"], 1)
            # the run sits under COUNCILS_ROOT/<slug>/runs/
            self.assertEqual(run.parent.parent.name, "standalone-council")
            self.assertEqual(run.parent.name, "runs")
            # and nothing was registered as a side effect
            code, out, err = cli(["list"], tmp)
            self.assertEqual(out, {"councils": []})


class ShippedCharterFixtureTest(unittest.TestCase):
    """The template and both examples are test fixtures: they must pass
    validate-charter as committed (read from the repo, not copied)."""

    FIXTURES = [
        REPO_ROOT / "templates" / "charter.yaml",
        REPO_ROOT / "examples" / "architecture-advisory" / "charter.yaml",
        REPO_ROOT / "examples" / "review-board" / "charter.yaml",
    ]

    def test_each_fixture_exists(self):
        for path in self.FIXTURES:
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file(), f"missing fixture: {path}")

    def test_template_and_examples_pass_validate_charter(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path in self.FIXTURES:
                with self.subTest(path=str(path)):
                    code, info, err = cli(["validate-charter", str(path)], tmp)
                    self.assertEqual(code, 0, err)
                    self.assertEqual(info["valid"], True)
                    for role in CORE_MEMBER_ROLES:
                        self.assertIn(role, info["members"],
                                      f"{path.name}: core role {role} missing")
                    self.assertGreaterEqual(info["quorum"], 2)

    def test_architecture_advisory_has_default_quorum_and_core_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = REPO_ROOT / "examples" / "architecture-advisory" / "charter.yaml"
            code, info, err = cli(["validate-charter", str(path)], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(info["name"], "architecture-advisory")
            self.assertEqual(info["members"], CORE_MEMBER_ROLES)
            self.assertEqual(info["quorum"], 2)  # strict majority of 3

    def test_review_board_has_security_role_explicit_quorum_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = REPO_ROOT / "examples" / "review-board" / "charter.yaml"
            code, info, err = cli(["validate-charter", str(path)], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(info["name"], "review-board")
            self.assertIn("security", info["members"])
            self.assertEqual(len(info["members"]), 5)
            self.assertEqual(info["quorum"], 3)

    def test_template_and_examples_register_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path in self.FIXTURES[1:]:  # template name is a placeholder
                slug = path.parent.name
                code, info, err = cli(["register", str(path)], tmp)
                self.assertEqual(code, 0, err)
                self.assertEqual(info["registered"], slug)
            code, out, err = cli(["list"], tmp)
            self.assertEqual([c["name"] for c in out["councils"]],
                             ["architecture-advisory", "review-board"])
            code, out, err = cli(["show", "review-board"], tmp)
            self.assertEqual(code, 0, err)
            self.assertTrue(out["core_roles_complete"])
            self.assertEqual(out["quorum"], 3)
            for slug in ("architecture-advisory", "review-board"):
                code, out, err = cli(["deregister", slug], tmp)
                self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
