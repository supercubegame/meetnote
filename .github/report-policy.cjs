'use strict';

const AUTHOR = {id: 41898282, login: 'github-actions[bot]'};
const FIELDS = ['repository', 'sha', 'run_id', 'run_attempt', 'event_name'];
function validIdentity(i) {
  return Boolean(i && typeof i === 'object' && !Array.isArray(i) &&
    FIELDS.every(k => typeof i[k] === 'string' && i[k].length > 0 && i[k].trim() === i[k]) &&
    /^[^/\s]+\/[^/\s]+$/.test(i.repository) &&
    /^[0-9a-f]{40}$/.test(i.sha) &&
    /^[1-9][0-9]*$/.test(i.run_id) &&
    /^[1-9][0-9]*$/.test(i.run_attempt));
}
function belongsToRun(report, expected) {
  const actual = report && report.run_identity;
  return validIdentity(actual) && validIdentity(expected) &&
    FIELDS.every(k => actual[k] === expected[k]);
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
  const report = {run_identity:{...expected}};
  test(() => assert.equal(belongsToRun(report, expected), true));
  test(() => assert.equal(belongsToRun(null, expected), false));
  test(() => assert.equal(belongsToRun({}, expected), false));
  for (const k of FIELDS) {
    test(() => assert.equal(belongsToRun({run_identity:{...expected,[k]:expected[k]+'x'}}, expected), false));
    const missing = {...expected}; delete missing[k];
    test(() => assert.equal(belongsToRun(report, missing), false));
  }
  test(() => assert.equal(belongsToRun({run_identity:{...expected,run_attempt:2}}, expected), false));
  test(() => assert.equal(belongsToRun({run_identity:{...expected,run_attempt:'1'}}, expected), false));
  test(() => assert.equal(belongsToRun({run_identity:{...expected,sha:'b'.repeat(40)}}, expected), false));
  test(() => assert.equal(belongsToRun({run_identity:{...expected,run_id:'0123'}}, {...expected,run_id:'0123'}), false));
  test(() => assert.equal(belongsToRun({run_identity:{...expected,sha:expected.sha+'\n'}}, {...expected,sha:expected.sha+'\n'}), false));
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
  console.log('Report identity/ownership self-tests: '+n+'/'+n);
  return n;
}
module.exports = {belongsToRun, selectOwned, assertWritten, selfTest};
if (require.main === module) selfTest();
