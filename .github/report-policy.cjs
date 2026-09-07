'use strict';

const AUTHOR = {id: 41898282, login: 'github-actions[bot]'};
const FIELDS = ['repository', 'sha', 'run_id', 'run_attempt', 'event_name'];
const REQUIRED_OK = ['stub_channel_absent', 'contract_not_emptied', 'endpoint_is_real', 'transport_is_real', 'key_absent_from_repo', 'request_contract_accepted', 'envelope_is_json', 'envelope_matches_contract', 'response_is_not_a_recording', 'content_matches_schema', 'nonce_echoed_by_model', 'probe_yielded_extractions'];
const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const strings = v => Array.isArray(v) && v.every(s => typeof s === 'string' && s.trim().length > 0);
function validIdentity(i) {
  return Boolean(i && typeof i === 'object' && !Array.isArray(i) &&
    FIELDS.every(k => typeof i[k] === 'string' && i[k].length > 0 && i[k].trim() === i[k]) &&
    /^[^/\s]+\/[^/\s]+$/.test(i.repository) &&
    /^[0-9a-f]{40}$/.test(i.sha) &&
    /^[1-9][0-9]*$/.test(i.run_id) &&
    /^[1-9][0-9]*$/.test(i.run_attempt));
}
// Validate every status, not only recovery. A current identity cannot make bad counts credible.
function consistentLive(r) {
  if (!object(r) || !['ok','drift','unconfirmed'].includes(r.status)) return false;
  if (r.exit_code !== {ok:0,drift:1,unconfirmed:78}[r.status]) return false;
  if ('gate' in r && r.gate !== 'live') return false;
  if (!Array.isArray(r.checks) || !r.checks.length || !Number.isInteger(r.checks_run) || !Number.isInteger(r.checks_passed)) return false;
  if (!r.checks.every(c => object(c) && typeof c.id === 'string' && c.id.trim().length && c.id.trim() === c.id && typeof c.ok === 'boolean' && (!('detail' in c) || typeof c.detail === 'string'))) return false;
  const ids = new Set(r.checks.map(c => c.id));
  const passed = r.checks.filter(c => c.ok === true).length;
  if (ids.size !== r.checks.length || r.checks_run !== r.checks.length || r.checks_passed !== passed) return false;
  if (!strings(r.drift) || !strings(r.unconfirmed_reasons)) return false;
  const derived = r.drift.length ? 'drift' : r.unconfirmed_reasons.length ? 'unconfirmed' : 'ok';
  if (derived !== r.status || (passed < r.checks.length && derived === 'ok')) return false;
  if (derived === 'drift' && passed === r.checks.length) return false;
  const e = r.evidence;
  if (!object(e) || typeof e.attempted_request !== 'boolean') return false;
  if ('envelope_additions' in e && !strings(e.envelope_additions)) return false;
  for (const key of ['attempts','elapsed_ms','fixture_ids_known']) {
    if (key in e && (!Number.isInteger(e[key]) || e[key] < 0)) return false;
  }
  if (derived === 'ok') {
    if (!REQUIRED_OK.every(id => ids.has(id)) || e.attempted_request !== true) return false;
    if (typeof e.response_id !== 'string' || !e.response_id.trim() || e.response_id.startsWith('fixture-')) return false;
  }
  return true;
}
function belongsToRun(report, expected) {
  const actual = report && report.run_identity;
  return validIdentity(actual) && validIdentity(expected) &&
    FIELDS.every(k => actual[k] === expected[k]) && consistentLive(report);
}
function owned(item) {
  return Boolean(item && item.user && item.user.id === AUTHOR.id && item.user.login === AUTHOR.login);
}
function selectOwned(items, marker, target) {
  if (!Array.isArray(items) || typeof target !== 'function') throw new Error('invalid target collection');
  const matches = items.filter(i => owned(i) && typeof i.body === 'string' &&
    i.body.split(/\r?\n/, 1)[0] === marker && target(i));
  if (matches.length > 1) throw new Error('ambiguous owned target: ' + matches.length);
  return matches[0] || null;
}
function assertWritten(item, id, body, target) {
  if (!owned(item) || item.id !== id || item.body !== body || !target(item)) {
    throw new Error('readback failed: author, target, id or current payload differs');
  }
}
function selfTest() {
  const assert = require('node:assert/strict');
  let n = 0;
  const test = fn => { fn(); n++; };
  const expected = {repository:'owner/repo',sha:'a'.repeat(40),run_id:'123',run_attempt:'2',event_name:'pull_request'};
  const report = {run_identity:{...expected},gate:'live',status:'ok',exit_code:0,checks_run:12,checks_passed:12,
    checks:REQUIRED_OK.map(id=>({id,ok:true,detail:''})),drift:[],unconfirmed_reasons:[],evidence:{attempted_request:true,response_id:'live-selftest'}};
  test(() => assert.equal(belongsToRun(report, expected), true));
  test(() => assert.equal(belongsToRun(null, expected), false));
  test(() => assert.equal(belongsToRun({}, expected), false));
  for (const k of FIELDS) {
    test(() => assert.equal(belongsToRun({...report,run_identity:{...expected,[k]:expected[k]+'x'}}, expected), false));
    const missing = {...expected}; delete missing[k];
    test(() => assert.equal(belongsToRun(report, missing), false));
  }
  test(() => assert.equal(belongsToRun({...report,run_identity:{...expected,run_attempt:2}}, expected), false));
  test(() => assert.equal(belongsToRun({...report,run_identity:{...expected,run_attempt:'1'}}, expected), false));
  test(() => assert.equal(belongsToRun({...report,run_identity:{...expected,sha:'b'.repeat(40)}}, expected), false));
  test(() => assert.equal(belongsToRun({...report,run_identity:{...expected,run_id:'0123'}}, {...expected,run_id:'0123'}), false));
  test(() => assert.equal(belongsToRun({...report,run_identity:{...expected,sha:expected.sha+'\n'}}, {...expected,sha:expected.sha+'\n'}), false));
  const marker = '<!-- report -->';
  const item = {id:7,user:{...AUTHOR},body:marker+'\nnew payload',issue_url:'expected'};
  const target = i => i.issue_url === 'expected';
  test(() => assert.equal(selectOwned([item], marker, target), item));
  test(() => assert.equal(selectOwned([], marker, target), null));
  for (const patch of [
    {body:'quoted\n'+item.body}, {body:'> '+item.body}, {body:marker+' suffix\npayload'},
    {user:{...AUTHOR,id:1}}, {user:{...AUTHOR,login:'other'}}, {issue_url:'other'},
  ]) test(() => assert.equal(selectOwned([{...item,...patch}], marker, target), null));
  test(() => assert.throws(() => selectOwned([item,{...item,id:8}], marker, target), /ambiguous/));
  test(() => assert.equal(selectOwned([{...item,body:marker+'\r\npayload'}], marker, target).id, 7));
  test(() => assert.doesNotThrow(() => assertWritten(item,7,item.body,target)));
  for (const patch of [{id:8},{body:marker+'\nold payload'},{user:{...AUTHOR,id:1}},{issue_url:'other'}]) {
    test(() => assert.throws(() => assertWritten({...item,...patch},7,item.body,target), /readback failed/));
  }
  const subscription = {...report,status:'unconfirmed',exit_code:78,checks:report.checks.slice(0,5),checks_run:5,checks_passed:5,
    unconfirmed_reasons:['auth_rejected:subscription_required'],evidence:{attempted_request:true,attempts:1,elapsed_ms:250}};
  const absent = {...subscription,checks:report.checks.slice(0,3),checks_run:3,checks_passed:3,unconfirmed_reasons:['secret_absent'],evidence:{attempted_request:false}};
  const drift = {...subscription,status:'drift',exit_code:1,checks:[...subscription.checks,{id:'request_contract_accepted',ok:false,detail:'http400'}],checks_run:6,checks_passed:5,drift:['request_contract_accepted: rejected'],unconfirmed_reasons:[]};
  for (const r of [subscription,absent,drift,{...drift,unconfirmed_reasons:['also unavailable']},
    {...report,checks:[...report.checks,{id:'envelope_additions_are_informational',ok:true,detail:'new'}],checks_run:13,checks_passed:13,evidence:{...report.evidence,envelope_additions:['new']}}]) {
    test(() => assert.equal(belongsToRun(r,expected),true));
  }
  for (const patch of [
    {checks_run:11},{checks_passed:11},{checks_run:'12'},{checks:[],checks_run:0,checks_passed:0},
    {checks:report.checks.map((c,i)=>i?c:{...c,ok:'true'})},
    {checks:report.checks.map((c,i)=>i?c:report.checks[1])},
    {checks:report.checks.map((c,i)=>i?c:{...c,id:'unknown'})},
    {exit_code:'0'},{exit_code:78},{status:'unconfirmed'},
    {drift:['failure']},{unconfirmed_reasons:['blocked']},{drift:{}},
    {evidence:{attempted_request:'true',response_id:'live'}},
    {evidence:{attempted_request:true,response_id:'fixture-recording'}},
    {evidence:{attempted_request:true,response_id:''}},
    {evidence:{...report.evidence,envelope_additions:'invalid'}},
    {evidence:{...report.evidence,attempts:-1}},{gate:'fast'},
  ]) test(() => assert.equal(belongsToRun({...report,...patch},expected),false));
  test(() => assert.equal(belongsToRun({...subscription,checks_passed:4},expected),false));
  test(() => assert.equal(belongsToRun({...subscription,unconfirmed_reasons:[]},expected),false));
  test(() => assert.equal(belongsToRun({...subscription,unconfirmed_reasons:[null]},expected),false));
  console.log('Report identity/ownership/consistency self-tests: '+n+'/'+n);
  return n;
}
module.exports = {belongsToRun, selectOwned, assertWritten, selfTest, consistentLive};
if (require.main === module) selfTest();
