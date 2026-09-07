"""Assertions about purity, frozen contracts, documentation and current CI policy."""
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
CORE = ROOT / 'meetnote' / 'core.py'
WORKFLOW = ROOT / '.github' / 'workflows' / 'verify.yml'
TEST_FILES = ('test_core.py','test_client.py','test_cli_e2e.py','test_live_gate.py','test_meta.py')
HEARTBEAT_PATH = '.github/live-heartbeat.json'
BANNED_ATTR_CALLS = {'now','today','monotonic','time','random','uuid4','getenv','system','popen'}
BANNED_NAME_CALLS = {'print','open','input','eval','exec'}

def core_tree():
    return ast.parse(CORE.read_text(encoding='utf-8'))

class CorePurity(unittest.TestCase):
    def test_core_imports_are_pure(self):
        found=set()
        for node in ast.walk(core_tree()):
            if isinstance(node,ast.Import): found.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node,ast.ImportFrom) and node.module: found.add(node.module.split('.')[0])
        self.assertEqual(found,{'__future__','json','re','datetime'})
    def test_core_has_no_clock_or_random_calls(self):
        calls=[n for n in ast.walk(core_tree()) if isinstance(n,ast.Call)]
        self.assertGreater(len(calls),20)
        offenders=[]
        for node in calls:
            func=node.func
            if isinstance(func,ast.Attribute) and func.attr in BANNED_ATTR_CALLS: offenders.append(func.attr)
            if isinstance(func,ast.Name) and func.id in BANNED_NAME_CALLS: offenders.append(func.id)
        self.assertEqual(offenders,[])
    def test_core_touches_no_output_sinks(self):
        source=CORE.read_text(encoding='utf-8');self.assertGreater(len(source),2000)
        for forbidden in ('sys.stdout','sys.stderr','logging.','os.environ'):self.assertNotIn(forbidden,source)

class FrozenContracts(unittest.TestCase):
    def test_contract_paths_frozen_by_equality(self):
        self.assertEqual(set(core.REQUIRED_RESPONSE_PATHS),{'id','model','choices','choices[0].message.content','choices[0].finish_reason','usage.prompt_tokens','usage.completion_tokens'})
        self.assertEqual(len(core.REQUIRED_RESPONSE_PATHS),live_check.EXPECTED_CONTRACT_PATH_COUNT)
    def test_backoff_cap_is_reachable_by_construction(self):
        reachable=core.BACKOFF_BASE_MS*(core.BACKOFF_FACTOR**(core.MAX_RETRIES-1))
        self.assertEqual(reachable,core.BACKOFF_CAP_MS)
        self.assertEqual(client_mod.backoff_ms(core.MAX_RETRIES),core.BACKOFF_CAP_MS)
    def test_diag_field_names_are_stable(self):
        self.assertEqual(set(cli_mod.DIAG_FIELDS),{'attempts','waits_ms','http_status','endpoint','transport','model','today','contract_paths_checked','retry_budget'})

class Docs(unittest.TestCase):
    def test_agent_docs_are_identical(self):
        agents=(ROOT/'AGENTS.md').read_text(encoding='utf-8');claude=(ROOT/'CLAUDE.md').read_text(encoding='utf-8')
        self.assertGreater(len(agents.strip()),500);self.assertEqual(agents,claude)
    def test_agent_docs_respect_line_limit(self):
        lines=(ROOT/'AGENTS.md').read_text(encoding='utf-8').splitlines()
        self.assertGreater(len(lines),20);self.assertLessEqual(len(lines),200)
    def test_heartbeat_path_is_documented(self):
        agents=(ROOT/'AGENTS.md').read_text(encoding='utf-8')
        self.assertIn(HEARTBEAT_PATH,agents);self.assertTrue((ROOT/HEARTBEAT_PATH).exists())
        seed=json.loads((ROOT/HEARTBEAT_PATH).read_text(encoding='utf-8'))
        self.assertIn('checked_at',seed);self.assertIn('status',seed)
        self.assertIn('历史记录',agents)

class Fixtures(unittest.TestCase):
    def _fixture_paths(self):
        paths=sorted(glob.glob(str(ROOT/'tests'/'fixtures'/'*.json')));self.assertGreaterEqual(len(paths),5);return paths
    def test_fixture_ids_carry_prefix(self):
        ids=[]
        for path in self._fixture_paths():
            data=json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
            for step in data['responses']:
                body=step.get('body_json')
                if isinstance(body,dict) and 'id' in body:ids.append(body['id'])
        self.assertGreaterEqual(len(ids),4)
        for value in ids:self.assertTrue(value.startswith(live_check.FIXTURE_ID_PREFIX))
        self.assertEqual(set(ids),live_check.fixture_response_ids(str(ROOT)))
    def test_fixtures_parse_and_are_non_empty(self):
        for path in self._fixture_paths():
            data=json.loads(pathlib.Path(path).read_text(encoding='utf-8'));self.assertTrue(data['responses'],path)
            for step in data['responses']:
                self.assertIn('status',step,path);self.assertTrue('body' in step or 'body_json' in step or 'raise' in step,path)

class CheckCount(unittest.TestCase):
    def test_expected_check_count_is_exact(self):
        total=0
        for name in TEST_FILES:
            tree=ast.parse((ROOT/'tests'/name).read_text(encoding='utf-8'))
            found=[n.name for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
            self.assertGreater(len(found),0,name);total+=len(found)
        self.assertEqual(total,EXPECTED_CHECKS)

class Workflow(unittest.TestCase):
    def _text(self):
        text=WORKFLOW.read_text(encoding='utf-8');self.assertGreater(len(text),1000);return text
    def _region(self,text,start,end=None,min_len=300):
        begin=text.index(start);region=text[begin:text.index(end)] if end else text[begin:]
        self.assertGreater(len(region),min_len);return region
    def _report_job(self,text):return self._region(text,'\n  report:')
    def _live_job(self,text):return self._region(text,'\n  live:','\n  report:')
    def test_workflow_has_both_writeback_paths(self):
        region=self._report_job(self._text());self.assertIn('meetnote-verify-report',region)
        for needle in ('issues.createComment','issues.updateComment','createCommitComment','updateCommitComment'):self.assertIn(needle,region)
    def test_workflow_failure_paths_are_fatal(self):
        text=self._text();region=self._report_job(text)
        writeback=region[region.index('id: writeback'):region.index('confirm writeback')]
        self.assertGreater(len(writeback),200);self.assertNotIn('continue-on-error',writeback)
        self.assertIn("steps.writeback.outcome != 'success'",region)
        self.assertIn('"$code" = "78"',text);self.assertIn('not a pass',text)
    def test_fast_gate_pipeline_cannot_mask_failures(self):
        fast=self._region(self._text(),'\n  fast:','\n  live:')
        if '| tee' in fast:self.assertIn('set -o pipefail',fast)
        self.assertIn('set -e',fast)
    def test_live_gate_is_also_driven_by_a_clock(self):
        # Historical test name retained for count continuity; clock now drives FAST only.
        text=self._text();triggers=self._region(text,'\non:','\npermissions:',min_len=90)
        for needle in ('schedule:','cron:','workflow_dispatch:','run_live:','default: false'):self.assertIn(needle,triggers)
        live=self._live_job(text);condition=live[live.index('if: >-'):live.index('runs-on:')]
        self.assertNotIn("github.event_name == 'schedule'",condition)
    def test_live_gate_runs_on_pull_requests_and_main(self):
        # User chose not to use meetnote: automatic events must NOT make real requests.
        live=self._live_job(self._text());condition=live.split('if: >-',1)[1].split('runs-on:',1)[0].strip()
        self.assertEqual(condition,"github.event_name == 'workflow_dispatch' && inputs.run_live == true")
        self.assertNotIn("github.event_name == 'pull_request'",condition)
        self.assertNotIn("github.event_name == 'push'",condition)
    def test_a_skipped_live_gate_is_only_ok_when_predicted(self):
        region=self._report_job(self._text());expect=region[region.index('id: expect'):region.index('write back report')]
        self.assertGreater(len(expect),200)
        self.assertIn('EVENT_NAME: ${{ github.event_name }}',expect);self.assertIn('RUN_LIVE: ${{ inputs.run_live }}',expect)
        self.assertIn("if [ \"$EVENT_NAME\" = 'workflow_dispatch' ] && [ \"$RUN_LIVE\" = 'true' ]; then",expect)
        self.assertNotIn('pull_request|schedule|workflow_dispatch',expect)
        verdict=region[region.index('name: verdict'):];self.assertGreater(len(verdict),300)
        self.assertIn('expected="${{ steps.expect.outputs.live }}"',verdict)
        self.assertIn('[ "$live" != "skipped" ]',verdict);self.assertIn('no longer matches the docs',verdict)
    def test_scheduled_drift_is_escalated_somewhere_a_human_looks(self):
        # Legacy recovery logic is retained, gated OFF while live monitoring is inactive.
        region=self._report_job(self._text())
        for needle in ('meetnote-live-contract-alert','await github.rest.issues.create({','await github.rest.issues.update({',"state: 'open'","state: 'closed'","isSchedule=context.eventName==='schedule' && liveExpected"):
            self.assertIn(needle,region)
    def test_report_says_out_loud_when_drift_was_not_checked(self):
        region=self._report_job(self._text())
        for needle in ('liveExpected','没有被验证','功能暂不使用','历史告警和心跳保留，不表示恢复'):self.assertIn(needle,region)
    def test_push_runs_cannot_clobber_the_pr_report(self):
        region=self._report_job(self._text());self.assertIn('ANTI-CLOBBER',region)
        self.assertIn('const issueNumber = context.payload.pull_request ? context.payload.pull_request.number : null;',region)
        self.assertNotIn('listPullRequestsAssociatedWithCommit',region)
    def test_scheduled_run_leaves_an_external_heartbeat(self):
        # Legacy step retained but unreachable with the current manual-only job condition.
        live=self._live_job(self._text());heartbeat=live[live.index('leave an external heartbeat'):live.index('classify live outcome')]
        self.assertGreater(len(heartbeat),800)
        self.assertIn(HEARTBEAT_PATH,heartbeat);self.assertIn("if: always() && github.event_name == 'schedule'",heartbeat)
        self.assertIn('checked_at',heartbeat);self.assertIn('git push origin HEAD:main',heartbeat)
        self.assertNotIn('continue-on-error',heartbeat);self.assertIn('exit 1',heartbeat)
        self.assertEqual(live.split('if: >-',1)[1].split('runs-on:',1)[0].strip(),"github.event_name == 'workflow_dispatch' && inputs.run_live == true")
    def test_heartbeat_commit_cannot_retrigger_ci(self):
        heartbeat_commit=[line for line in self._live_job(self._text()).splitlines() if 'git commit' in line and 'heartbeat' in line]
        self.assertEqual(len(heartbeat_commit),1);self.assertIn('skip ci',heartbeat_commit[0])

if __name__=='__main__':unittest.main()
