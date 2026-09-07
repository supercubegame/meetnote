'use strict';
const fields=['repository','sha','run_id','run_attempt','event_name'];
const obj=r=>r!==null&&typeof r==='object'&&!Array.isArray(r);
function validateFast(r,expected){
 const out=[];const need=(ok,why)=>{if(!ok)out.push(why);};
 if(!obj(r))return ['fast report missing or not an object'];
 need(obj(r.run_identity)&&fields.every(k=>typeof expected[k]==='string'&&expected[k].length&&r.run_identity[k]===expected[k]),'fast execution identity mismatch');
 need(r.gate==='fast'&&['ok','red'].includes(r.status),'invalid fast gate/status');
 need(['checks_run','checks_passed','expected_checks'].every(k=>Number.isInteger(r[k])&&r[k]>=0)&&r.expected_checks>0,'invalid fast counts');
 need(Array.isArray(r.hard_errors)&&r.hard_errors.every(x=>typeof x==='string'),'invalid hard errors');
 need(Array.isArray(r.failures)&&r.failures.every(x=>obj(x)&&typeof x.test==='string'&&typeof x.summary==='string'),'invalid failures');
 need(obj(r.per_module)&&Object.keys(r.per_module).length>0,'empty module ledger');
 need(typeof r.duration_s==='number'&&Number.isFinite(r.duration_s)&&r.duration_s>=0,'invalid duration');
 if(out.length)return out;
 const modules=Object.values(r.per_module);
 need(modules.every(m=>obj(m)&&['run','failures','errors'].every(k=>Number.isInteger(m[k])&&m[k]>=0)),'invalid module counts');
 if(out.length)return out;
 need(modules.reduce((n,m)=>n+m.run,0)===r.checks_run,'module total mismatch');
 need(r.checks_passed===r.checks_run-r.failures.length,'passed total mismatch');
 need(r.count_matches===(r.checks_run===r.expected_checks),'count_matches mismatch');
 need(r.status===(r.failures.length||r.hard_errors.length?'red':'ok'),'status and failures disagree');
 if(r.status==='ok')need(r.count_matches&&r.checks_run>0&&modules.every(m=>m.run>0&&m.failures===0&&m.errors===0),'ok report has missing or failed module');
 return out;
}
function assertIssue(item,number,body,state,repoApi){
 if(!obj(item)||item.number!==number||item.body!==body||item.state!==state||item.repository_url!==repoApi||item.pull_request||item.user?.id!==41898282||item.user?.login!=='github-actions[bot]')throw Error('alert readback mismatch');
}
function selfTest(){
 const a=require('node:assert/strict');let n=0;const test=f=>{f();n++;};
 const identity={repository:'owner/repo',sha:'a'.repeat(40),run_id:'1',run_attempt:'2',event_name:'push'};
 const good={run_identity:identity,gate:'fast',status:'ok',checks_run:2,checks_passed:2,expected_checks:2,count_matches:true,duration_s:1,hard_errors:[],failures:[],per_module:{one:{run:2,failures:0,errors:0}}};
 test(()=>a.deepEqual(validateFast(good,identity),[]));
 for(const patch of [{run_identity:{...identity,run_attempt:'1'}},{run_identity:null},{checks_run:'2'},{checks_passed:1},{per_module:{}},{count_matches:'true'},{status:'red'},{failures:[{}]},{hard_errors:{}},{duration_s:NaN},{checks_run:0,checks_passed:0}])test(()=>a(validateFast({...good,...patch},identity).length));
 test(()=>a.deepEqual(validateFast({...good,status:'red',checks_passed:1,failures:[{test:'x',summary:'failed'}],per_module:{one:{run:2,failures:1,errors:0}}},identity),[]));
 const issue={number:4,body:'current',state:'open',repository_url:'repo',user:{id:41898282,login:'github-actions[bot]'}};
 test(()=>assertIssue(issue,4,'current','open','repo'));
 for(const patch of [{number:5},{body:'old'},{state:'closed'},{repository_url:'other'},{user:{id:1,login:'github-actions[bot]'}},{pull_request:{}}])test(()=>a.throws(()=>assertIssue({...issue,...patch},4,'current','open','repo')));
 console.log('Fast/alert self-tests '+n+'/'+n);return n;
}
module.exports={validateFast,assertIssue,selfTest};
