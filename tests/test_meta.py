"""Assertions about the project's own rules.

A rule that only lives in AGENTS.md is advice. A rule with an assertion is a
rule. Everything here exists because it would otherwise rot quietly: the purity
of the core, the frozen response contract, the doc length limit, the check count,
and the CI trigger policy and writeback paths.
"""
import ast
import glob
import json
import pathlib
import unittest

from meetnote import cli as cli_mod
from meetnote import client as client_mod
from meetnote import core, live_check
from tests.expected_counts import EXPECTED_CHECKS

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "meetnote" / "core.py"
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
TEST_FILES = (
    "test_core.py",
    "test_client.py",
    "test_cli_e2e.py",
    "test_live_gate.py",
    "test_meta.py",
)

# The one path an outside auditor is told to read. Pinned here so it cannot move
# without the docs moving with it.
HEARTBEAT_PATH = ".github/live-heartbeat.json"

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

    def test_heartbeat_path_is_documented(self):
        """An outside auditor is told to read this exact path every week. If the path
        moves and the docs do not, that audit reads nothing and reports healthy,
        which is the same failure shape it was added to cover."""
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(HEARTBEAT_PATH, agents)
        self.assertTrue((ROOT / HEARTBEAT_PATH).exists(), "%s is missing" % HEARTBEAT_PATH)
        seed = json.loads((ROOT / HEARTBEAT_PATH).read_text(encoding="utf-8"))
        self.assertIn("checked_at", seed)
        self.assertIn("status", seed)


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
    """Static checks over the CI definition.

    These are pattern matches over YAML, so each one first proves it actually
    parsed a non-empty region. A scan that silently matches nothing is the same
    thing as an assertion that is always true.

    The floors below are per region, not one blanket number: a flat 300 chars
    already failed once on a perfectly correct trigger block that is only 273
    chars long. Each floor is roughly a third of the real size, so it catches
    "matched nothing" without tripping on normal edits.
    """

    def _text(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreater(len(text), 1000)
        return text

    def _region(self, text, start, end=None, min_len=300):
        begin = text.index(start)
        region = text[begin : text.index(end)] if end else text[begin:]
        self.assertGreater(
            len(region),
            min_len,
            "region %r is %d chars, under the %d floor; the scan is probably matching "
            "the wrong thing" % (start, len(region), min_len),
        )
        return region

    def _report_job(self, text):
        return self._region(text, "\n  report:")

    def _live_job(self, text):
        return self._region(text, "\n  live:", "\n  report:")

    def test_workflow_has_both_writeback_paths(self):
        region = self._report_job(self._text())
        self.assertIn("meetnote-verify-report", region)
        for needle in (
            "issues.createComment",
            "issues.updateComment",
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

    def test_fast_gate_pipeline_cannot_mask_failures(self):
        text = self._text()
        fast = self._region(text, "\n  fast:", "\n  live:")
        if "| tee" in fast:
            self.assertIn(
                "set -o pipefail",
                fast,
                "piping the gate through tee without pipefail reports tee's exit code, "
                "so a failing gate would look green",
            )

    def test_live_gate_is_also_driven_by_a_clock(self):
        """Response drift is time driven. Upstream can change the envelope on a day
        nobody pushes anything, so a push-only trigger cannot see it."""
        text = self._text()
        # The trigger block is short by nature: ~270 chars, so the floor is 90.
        triggers = self._region(text, "\non:", "\npermissions:", min_len=90)
        self.assertIn("schedule:", triggers)
        self.assertIn("cron:", triggers)
        self.assertIn("workflow_dispatch:", triggers)
        self.assertIn("github.event_name == 'schedule'", self._live_job(text))

    def test_live_gate_runs_on_pull_requests_and_main(self):
        live = self._live_job(self._text())
        condition = live[live.index("if: >-") : live.index("runs-on:")]
        self.assertGreater(len(condition), 100)
        self.assertIn("github.event_name == 'pull_request'", condition)
        self.assertIn("refs/heads/main", condition)
        self.assertIn("github.event_name == 'workflow_dispatch'", condition)

    def test_a_skipped_live_gate_is_only_ok_when_predicted(self):
        """The one failure mode that hides itself: if the live job's condition stops
        matching the documented policy it silently never runs, and every run stays
        green forever. So the verdict compares the job result against an
        independently computed expectation and fails on a mismatch either way."""
        region = self._report_job(self._text())
        expect = region[region.index("id: expect") : region.index("write back report")]
        self.assertGreater(len(expect), 200)
        self.assertIn("pull_request|schedule|workflow_dispatch", expect)
        self.assertIn("refs/heads/main", expect)
        verdict = region[region.index("name: verdict") :]
        self.assertGreater(len(verdict), 300)
        self.assertIn('expected="${{ steps.expect.outputs.live }}"', verdict)
        self.assertIn('[ "$live" != "skipped" ]', verdict)
        self.assertIn("no longer matches the docs", verdict)

    def test_scheduled_drift_is_escalated_somewhere_a_human_looks(self):
        """A scheduled run has no PR, and its commit comment lands on a main SHA
        that may not have moved in a week. Nobody reads that."""
        region = self._report_job(self._text())
        self.assertIn("meetnote-live-contract-alert", region)
        self.assertIn("await github.rest.issues.create({", region)
        self.assertIn("await github.rest.issues.update({", region)
        self.assertIn("state: 'open'", region)
        self.assertIn("state: 'closed'", region)

    def test_report_says_out_loud_when_drift_was_not_checked(self):
        """Omitting the live section on feature branches would let a green comment
        read as though the real contract had been verified. It was not."""
        region = self._report_job(self._text())
        self.assertIn("liveExpected", region)
        self.assertIn("没有被验证", region)

    def test_push_runs_cannot_clobber_the_pr_report(self):
        """One destination, one owner. A push to a branch with an open PR fires both
        a push run and a pull_request run for the same commit; the push run skips
        the live gate, so if it also owned the PR comment it could land second and
        overwrite the full report with "live gate did not run"."""
        region = self._report_job(self._text())
        self.assertIn("ANTI-CLOBBER", region)
        self.assertIn(
            "const issueNumber = context.payload.pull_request ? context.payload.pull_request.number : null;",
            region,
        )
        self.assertNotIn(
            "listPullRequestsAssociatedWithCommit",
            region,
            "resolving a PR from the commit puts the push run back in charge of the "
            "PR comment, which is exactly the clobber this rule prevents",
        )

    def test_scheduled_run_leaves_an_external_heartbeat(self):
        """GitHub auto-disables schedules after 60 days of repo inactivity, and a
        disabled cron leaves this YAML untouched. So asserting "the cron is still
        declared" would stay true through the exact failure it appears to guard,
        and no internal guard can help: it would have to run on the dead cron.

        The only thing that survives that is a dated trace the run itself leaves
        behind, readable from outside the repo. This checks the trace gets written
        on every scheduled run (always(), so an outage or a drift still proves the
        schedule is alive) and that a failure to deliver it is fatal.
        """
        live = self._live_job(self._text())
        heartbeat = live[live.index("leave an external heartbeat") : live.index("classify live outcome")]
        self.assertGreater(len(heartbeat), 800, "heartbeat step region looks wrong")
        self.assertIn(HEARTBEAT_PATH, heartbeat)
        self.assertIn("if: always() && github.event_name == 'schedule'", heartbeat)
        self.assertIn("checked_at", heartbeat)
        self.assertIn("git push origin HEAD:main", heartbeat)
        self.assertNotIn(
            "continue-on-error",
            heartbeat,
            "an undelivered heartbeat reads exactly like a dead cron, so it must fail the job",
        )
        self.assertIn("exit 1", heartbeat, "the retry loop must end in a real failure")

    def test_heartbeat_commit_cannot_retrigger_ci(self):
        """The heartbeat lands on main, and a push to main runs the live gate, which
        writes another heartbeat. Without a skip marker that is an infinite loop
        that also burns one real API call per lap."""
        heartbeat_commit = [
            line
            for line in self._live_job(self._text()).splitlines()
            if "git commit" in line and "heartbeat" in line
        ]
        self.assertEqual(len(heartbeat_commit), 1, "expected exactly one heartbeat commit line")
        self.assertIn("skip ci", heartbeat_commit[0])


if __name__ == "__main__":
    unittest.main()
