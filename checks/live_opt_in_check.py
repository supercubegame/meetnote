"""Zero-network tests of the actual workflow's opt-in condition and expectation shell."""
import os,re,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
text=(ROOT/'.github/workflows/verify.yml').read_text()
live=text.split('\n  live:',1)[1].split('\n  report:',1)[0]
condition=live.split('if: >-',1)[1].split('runs-on:',1)[0].strip()
assert condition=="github.event_name == 'workflow_dispatch' && inputs.run_live == true",condition
inputs=text.split('  workflow_dispatch:',1)[1].split('\npermissions:',1)[0]
assert re.search(r'run_live:\n(?:.*\n)*?        type: boolean\n        required: true\n        default: false',inputs)
region=text.split('        id: expect\n',1)[1].split('      - name: write back report',1)[0]
assert 'EVENT_NAME: ${{ github.event_name }}' in region and 'RUN_LIVE: ${{ inputs.run_live }}' in region
script=region.split('        run: |\n',1)[1]
script='\n'.join(line[10:] for line in script.splitlines())
count=0
for event in ['push','pull_request','schedule','workflow_dispatch']:
 for opt in ['', 'false', 'true', 'True']:
  with tempfile.TemporaryDirectory() as d:
   output=Path(d)/'out'
   env=dict(os.environ,EVENT_NAME=event,RUN_LIVE=opt,GITHUB_OUTPUT=str(output))
   subprocess.run(['bash','-e','-o','pipefail','-c',script],env=env,check=True,capture_output=True)
   want=event=='workflow_dispatch' and opt=='true'
   assert output.read_text().strip()=='live='+str(want).lower(),(event,opt)
   count+=1
assert "isSchedule=context.eventName==='schedule' && liveExpected" in text
assert '功能暂不使用' in text and '历史告警和心跳保留，不表示恢复' in text
fast=text.split('\n  fast:',1)[1].split('\n  live:',1)[0]
assert 'set -e' in fast and 'python checks/live_opt_in_check.py' in fast
assert 'STEPFUN_API_KEY' not in fast
print(f'Opt-in policy {count}/{count}: real expectation shell checked; exact live condition and default false guarded. No network requests performed.')
