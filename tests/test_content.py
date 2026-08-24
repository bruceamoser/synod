"""Content tests: role cards, SKILL.md, and the commands they cite.

Keeps the content honest: every ```json example in a role card must parse
and validate against the right schema, the skill frontmatter must be valid
YAML with name + description, and every engine command the runbook cites
must be a real subcommand (checked against the live argparse tree and the
--help text). Pure file reading; no run dirs, no COUNCILS_ROOT.
"""
import argparse
import contextlib
import io
import json
import re
import unittest
from pathlib import Path

import jsonschema
import yaml

from _engine import REPO_ROOT, engine

ROLES_DIR = REPO_ROOT / "references" / "roles"
SCHEMA_DIR = REPO_ROOT / "references" / "schemas"
SKILL_PATH = REPO_ROOT / "SKILL.md"

CORE_CARDS = ("librarian", "judge", "contrarian", "researcher")
CARD_SECTIONS = ("Mission", "Input", "Output contract", "Guardrails", "Example")
JSON_BLOCK_RE = re.compile(r"```json\n(.*?)```", re.DOTALL)
CITED_COMMAND_RE = re.compile(r"council\.py\s+([a-z][a-z0-9-]*)")
# The stage-critical commands a complete runbook must cite.
REQUIRED_SKILL_COMMANDS = (
    "scaffold", "record-brief", "add-source", "brief", "finding",
    "note-round", "check", "judge-brief", "seal-ruling", "close",
    "verify", "show",
)


def read_skill_text():
    """The raw text of SKILL.md; fails the suite if the file is missing."""
    assert SKILL_PATH.is_file(), f"missing {SKILL_PATH}"
    return SKILL_PATH.read_text(encoding="utf-8")


def frontmatter_of(text):
    """The YAML frontmatter body of a markdown file (between the fences)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("file must start with a '---' frontmatter fence")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    raise AssertionError("frontmatter fence is never closed")


def subcommand_names():
    """The real subcommand names, taken from the engine's live parser."""
    parser = engine.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("engine parser has no subparsers")


def help_text():
    """The engine's --help output (stdout) and its exit code."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = engine.main(["--help"])
    return out.getvalue(), code


class RoleCardPresenceTest(unittest.TestCase):
    def test_all_four_core_cards_exist(self):
        for name in CORE_CARDS:
            self.assertTrue((ROLES_DIR / f"{name}.md").is_file(),
                            f"missing references/roles/{name}.md")

    def test_each_core_card_has_the_five_sections_in_order(self):
        for name in CORE_CARDS:
            text = (ROLES_DIR / f"{name}.md").read_text(encoding="utf-8")
            with self.subTest(card=name):
                headings = [h for h in
                            re.findall(r"^## (.+)$", text, re.MULTILINE)]
                for section in CARD_SECTIONS:
                    self.assertIn(section, headings)
                positions = [headings.index(s) for s in CARD_SECTIONS]
                self.assertEqual(positions, sorted(positions),
                                 f"{name}: sections out of order")


class RoleCardExampleTest(unittest.TestCase):
    def _schema(self, name):
        return json.loads((SCHEMA_DIR / f"{name}.schema.json")
                          .read_text(encoding="utf-8"))

    def test_every_json_block_in_a_core_card_is_valid(self):
        for card in sorted(ROLES_DIR.glob("*.md")):
            text = card.read_text(encoding="utf-8")
            blocks = JSON_BLOCK_RE.findall(text)
            self.assertTrue(blocks, f"{card.name}: no ```json example block")
            schema_name = "ruling" if card.stem == "judge" else "finding"
            schema = self._schema(schema_name)
            for i, block in enumerate(blocks):
                with self.subTest(card=card.name, block=i):
                    obj = json.loads(block)
                    self.assertIsInstance(obj, dict)
                    self.assertEqual(obj.get("topic"), "t-01",
                                     "examples use the realistic topic t-01")
                    jsonschema.validate(obj, schema)


class SkillFrontmatterTest(unittest.TestCase):
    def test_frontmatter_is_valid_yaml_with_name_and_description(self):
        fm = yaml.safe_load(frontmatter_of(read_skill_text()))
        self.assertIsInstance(fm, dict)
        self.assertEqual(fm.get("name"), "council")
        self.assertIsInstance(fm.get("description"), str)
        self.assertTrue(fm["description"].strip())


class SkillCommandReferenceTest(unittest.TestCase):
    def test_cited_commands_are_real_subcommands(self):
        cited = sorted(set(CITED_COMMAND_RE.findall(read_skill_text())))
        self.assertTrue(cited, "SKILL.md cites no engine commands")
        real = subcommand_names()
        unknown = [c for c in cited if c not in real]
        self.assertEqual(unknown, [],
                         f"SKILL.md cites commands that do not exist: {unknown}")

    def test_cited_commands_appear_in_help_output(self):
        help_out, code = help_text()
        self.assertEqual(code, 0)
        cited = sorted(set(CITED_COMMAND_RE.findall(read_skill_text())))
        for cmd in cited:
            with self.subTest(command=cmd):
                self.assertIn(cmd, help_out)

    def test_runbook_cites_every_stage_critical_command(self):
        cited = set(CITED_COMMAND_RE.findall(read_skill_text()))
        missing = [c for c in REQUIRED_SKILL_COMMANDS if c not in cited]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
