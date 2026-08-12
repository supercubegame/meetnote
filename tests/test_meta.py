"""Assertions about the project's own rules.

A rule that only lives in AGENTS.md is advice. A rule with an assertion is a
rule. Everything here exists because it would otherwise rot quietly: the purity
of the core, the frozen response contract, the doc length limit, the check count,
and the CI writeback paths.
"""
import ast
import glob
import json
import os
import pathlib
import unittest

from meetnote import cli as cli_mod
from meetnote import client as client_mod
from meetnote import core, live_check
from tests.expected_counts import EXPECTED_CHECKS

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "meetnote" / "core.py"
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
TEST_FILES = ("test_core.py", "test_client.py", "test_cli_e2e.py", "test_meta.py")

BANNED_ATTR_CALLS = {"now", "today", "monotonic", "time", "random", "uuid4", "getenv", "system", "popen"}
BANNED_NAME_CALLS = {"print", "open", "input", "eval", "exec"}


def core_tree():
    return ast.parse(CORE.read_text(encoding="utf-8"))


class CorePurity(unittest.TestCase):
    def test_core_imports_are_pure(self):
        found = set()
        for node in ast.walk(core_tree()):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertEqual(found, {"__future__", "json", "re", "datetime"})

    def test_core_has_no_clock_or_random_calls(self):
        calls = [n for n in ast.walk(core_tree()) if isinstance(n, ast.Call)]
        # Prove the scan actually found something before trusting its silence.
        self.assertGreater(len(calls), 20)
        offenders = []
        for node in calls:
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in BANNED_ATTR_CALLS:
                offenders.append(func.attr)
            if isinstance(func, ast.Name) and func.id in BANNED_NAME_CALLS:
                offenders.append(func.id)
        self.assertEqual(offenders, [], "core.py must stay pure, found: %s" % offenders)

    def test_core_touches_no_output_sinks(self):
        source = CORE.read_text(encoding="utf-8")
        self.assertGreater(len(source), 2000)
        for forbidden in ("sys.stdout", "sys.stderr", "logging.", "os.environ"):
            self.assertNotIn(forbidden, source)


class FrozenContracts(unittest.TestCase):
    def test_contract_paths_frozen_by_equality(self):
        self.assertEqual(
            set(core.REQUIRED_RESPONSE_PATHS),
            {
                "id",
                "model",
                "choices",
                "choices[0].message.content",
                "choices[0].finish_reason",
                "usage.prompt_tokens",
                "usage.completion_tokens",
            },
        )
        self.assertEqual(len(core.REQUIRED_RESPONSE_PATHS), live_check.EXPECTED_CONTRACT_PATH_COUNT)

    def test_backoff_cap_is_reachable_by_construction(self):
        reachable = core.BACKOFF_BASE_MS * (core.BACKOFF_FACTOR ** (core.MAX_RETRIES - 1))
        self.assertEqual(
            reachable,
            core.BACKOFF_CAP_MS,
            "coupled group: BASE * FACTOR**(MAX_RETRIES-1) must equal BACKOFF_CAP_MS, "
            "otherwise the cap is decoration",
        )
        self.assertEqual(client_mod.backoff_ms(core.MAX_RETRIES), core.BACKOFF_CAP_MS)

    def test_diag_field_names_are_stable(self):
        self.assertEqual(
            set(cli_mod.DIAG_FIELDS),
            {
                "attempts",
                "waits_ms",
                "http_status",
                "endpoint",
                "transport",
                "model",
                "today",
                "contract_paths_checked",
                "retry_budget",
            },
        )


class Docs(unittest.TestCase):
    def test_agent_docs_are_identical(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertGreater(len(agents.strip()), 500)
        self.assertEqual(agents, claude)

    def test_agent_docs_respect_line_limit(self):
        lines = (ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(lines), 20)
        self.assertLessEqual(len(lines), 200, "AGENTS.md 超过 200 行，模型会开始忽略里面的指令")


class Fixtures(unittest.TestCase):
    def _fixture_paths(self):
        paths = sorted(glob.glob(str(ROOT / "tests" / "fixtures" / "*.json")))
        self.assertGreaterEqual(len(paths), 5)
        return paths

    def test_fixture_ids_carry_prefix(self):
        ids = []
        for path in self._fixture_paths():
            data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
            for step in data["responses"]:
                body = step.get("body_json")
                if isinstance(body, dict) and "id" in body:
                    ids.append(body["id"])
        # Prove the scan found ids before asserting anything about them.
        self.assertGreaterEqual(len(ids), 4)
        for value in ids:
            self.assertTrue(
                value.startswith(live_check.FIXTURE_ID_PREFIX),
                "fixture id %r must carry the %r prefix so the live gate can prove a real "
                "response is not a recording" % (value, live_check.FIXTURE_ID_PREFIX),
            )
        self.assertEqual(set(ids), live_check.fixture_response_ids(str(ROOT)))

    def test_fixtures_parse_and_are_non_empty(self):
        for path in self._fixture_paths():
            data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
            self.assertTrue(data["responses"], path)
            for step in data["responses"]:
                self.assertIn("status", step, path)
                self.assertTrue("body" in step or "body_json" in step or "raise" in step, path)


class CheckCount(unittest.TestCase):
    def test_expected_check_count_is_exact(self):
        total = 0
        for name in TEST_FILES:
            tree = ast.parse((ROOT / "tests" / name).read_text(encoding="utf-8"))
            found = [
                n.name
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
            ]
            self.assertGreater(len(found), 0, name)
            total += len(found)
        self.assertEqual(
            total,
            EXPECTED_CHECKS,
            "tests/expected_counts.py 里的数字和实际测试数不一致，改测试时要同一个提交里改这个数",
        )


class Workflow(unittest.TestCase):
    def _text(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreater(len(text), 1000)
        return text

    def _report_job(self, text):
        start = text.index("\n  report:")
        region = text[start:]
        self.assertGreater(len(region), 500, "report job region looks empty; the scan is not working")
        return region

    def test_workflow_has_both_writeback_paths(self):
        text = self._text()
        region = self._report_job(text)
        self.assertIn("meetnote-verify-report", region)
        for needle in (
            "issues.createComment",
            "issues.updateComment",
            "listPullRequestsAssociatedWithCommit",
            "createCommitComment",
            "updateCommitComment",
        ):
            self.assertIn(needle, region, "missing writeback path: %s" % needle)

    def test_workflow_failure_paths_are_fatal(self):
        text = self._text()
        region = self._report_job(text)
        writeback = region[region.index("id: writeback") : region.index("confirm writeback")]
        self.assertGreater(len(writeback), 200)
        self.assertNotIn(
            "continue-on-error",
            writeback,
            "writeback must be able to fail the job, otherwise a broken monitor looks healthy",
        )
        self.assertIn("steps.writeback.outcome != 'success'", region)
        self.assertIn('"$code" = "78"', text)
        self.assertIn("not a pass", text)


if __name__ == "__main__":
    unittest.main()
