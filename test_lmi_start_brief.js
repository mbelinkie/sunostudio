const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const ctx=vm.createContext({RT_CSS:'body{color:#17181c}',diagnosticBucket_:(status,todo,done)=>
  todo.includes('"'+status+'"')?'To Do':done.includes('"'+status+'"')?'Done':'In Progress'});
vm.runInContext(fs.readFileSync('SprintStartBrief.gs','utf8'),ctx);
const row=[]; row[3]='("Open")'; row[4]='("Done", "Deploy (STAGE)")';
const issues=Array.from({length:151},(_,i)=>({key:'LMI-'+(100+i),fields:{summary:'License reminder <script> & "Export"',
  status:{name:i===0?'Deploy (STAGE)':'Open'},pts:i===1?null:2,risk:i===0?{value:'Green'}:null}}));
const facts=ctx.lmiStartMetrics_(issues,'pts','risk',row);
assert.equal(facts.issueCount,151); assert.equal(facts.points,300); assert.equal(facts.unestimated,1);
assert.equal(facts.buckets.Done,1); assert.equal(facts.buckets['To Do'],150);
assert.equal(facts.risk.label,'Incomplete risk coverage'); assert.equal(facts.risk.assessed,1);
assert.equal(ctx.lmiStartMetrics_(issues,'pts','',row).risk.label,'Not assessed');
assert.equal(ctx.lmiStartMetrics_([issues[0]],'pts','risk',row).risk.label,'Recorded Green risk');
assert.throws(()=>ctx.lmiStartMetrics_([issues[0],issues[0]],'pts','risk',row),/DUPLICATE/);
assert.throws(()=>ctx.lmiStartMetrics_([{key:'LMI-1',fields:{pts:-1,status:{name:'Open'}}}],'pts','',row),/STORY_POINTS/);
Object.assign(facts,{sprintGoal:'Renew licenses <script>alert(1)</script>',sprintName:'LMI <26.3.7>',sprintStart:'2026-09-23',
  sprintEnd:'2026-10-07',boardUrl:'https://example.test/lmi',capturedAt:'2026-10-04'});
const proseInput=ctx.lmiNarrativeInput_({...facts,sprintGoal:'Release 13.3 PROD work'});
assert.ok(!proseInput.includes('13.3')); assert.ok(!proseInput.includes('PROD')); assert.ok(proseInput.includes('LMI-100:'));
const reply={summary:'Planned licensing work focuses on learner communication.',themes:[{label:'License reminders',
  description:'The planned work improves license reminder communication.',evidenceKeys:['LMI-100']}]};
assert.deepEqual(JSON.parse(JSON.stringify(ctx.validateLmiNarrative_(JSON.stringify(reply),facts))),reply);
const bad=structuredClone(reply); bad.themes[0].evidenceKeys=['LMI-999999'];
assert.throws(()=>ctx.validateLmiNarrative_(JSON.stringify(bad),facts),/EVIDENCE/);
bad.themes[0].evidenceKeys=['LMI-250']; // captured but outside the bounded narrative evidence
assert.throws(()=>ctx.validateLmiNarrative_(JSON.stringify(bad),facts),/EVIDENCE/);
assert.throws(()=>ctx.validateLmiNarrative_('```json\n{}\n```',facts),/JSON/);
assert.throws(()=>ctx.validateLmiNarrative_(JSON.stringify({...reply,recipients:'stranger@example.test'}),facts),/SHAPE/);
assert.throws(()=>ctx.validateLmiNarrative_(JSON.stringify({...reply,summary:'<script>bad</script>'}),facts),/SHAPE/);
assert.throws(()=>ctx.validateLmiNarrative_(JSON.stringify({...reply,summary:'Release 13.3 fixes production defects.'}),facts),/SHAPE/);
const html=ctx.renderLmiStartBrief_(facts,reply,true);
assert.ok(html.includes('151 work items · 300 estimated points'));
assert.ok(html.includes('not a historical sprint-start baseline'));
assert.ok(html.includes('metrics include all 151'));
assert.ok(html.includes('&lt;script&gt;')); assert.ok(!html.includes('<script>'));
assert.ok(html.includes('charset="UTF-8"'));
const callbackFacts={issueCount:3,issues:['LMI-100','LMI-101','LMI-102'].map(key=>({key})),points:4,unestimated:1,
  buckets:{'To Do':2,'In Progress':1,Done:0},risk:{label:'Not assessed',assessed:0,unknown:3},sprintId:'26312',
  sprintName:'LMI planning',sprintStart:'2026-10-01',sprintEnd:'2026-10-14',sprintGoal:'License reminder work',
  boardUrl:'https://example.test/lmi',capturedAt:'2026-10-01T00:00:00Z'};
const callbackResponse=JSON.stringify({summary:'Planned license work focuses on clear renewal communications.',
  themes:[{label:'License reminders',description:'The planned work improves license reminder communication.',evidenceKeys:['LMI-100']}]});
ctx.validateLmiNarrative_(callbackResponse,callbackFacts);
assert.throws(()=>ctx.validateLmiNarrative_('{"summary":"<script>bad</script>","themes":[]}',callbackFacts));

// Callback races, duplicate delivery and uncertain-send state must fail closed.
let records, sends, creates, failSend;
const log={getDataRange:()=>({getValues:()=>records}),getRange:(r,c)=>({setValue:v=>{records[r-1][c-1]=v;}})};
ctx.SpreadsheetApp={getActive:()=>({getSheetByName:()=>log}),flush:()=>{}};
ctx.LockService={getScriptLock:()=>({waitLock:()=>{},releaseLock:()=>{}})};
ctx.MimeType={HTML:'text/html'}; ctx.Logger={log:()=>{}};
ctx.DriveApp={getFileById:()=>({getBlob:()=>({getDataAsString:()=>JSON.stringify(callbackFacts)})}),
  createFile:()=>{creates++;return {getId:()=> 'result-file',getUrl:()=> 'private-preview'};}};
ctx.MailApp={sendEmail:()=>{sends++;if(failSend)throw new Error('send interrupted');}};
ctx.PropertiesService={getScriptProperties:()=>({getProperty:key=>key==='LMI_START_RECIPIENTS'?'lmi@example.test':''})};
const callback={boardId:'1018',sprintId:callbackFacts.sprintId,sprintGoal:callbackResponse};
const reset=(preview,state='FORWARDING')=>{callbackFacts.isPreview=preview;sends=creates=0;failSend=false;
  records=[['key','boardId','sprintId','status','receivedAt','factsFile','resultFile','error'],
    ['started:1018:'+callbackFacts.sprintId,'1018',callbackFacts.sprintId,state,'','facts-file','','']];};
reset(true);
assert.equal(ctx.handleLmiNarrativeResult_(callback).status,'PREVIEW_READY');
assert.equal(sends,0); assert.equal(creates,1);
assert.equal(ctx.handleLmiNarrativeResult_(callback).status,'DUPLICATE'); assert.equal(creates,1);
reset(true,'FORWARD_UNKNOWN'); assert.equal(ctx.handleLmiNarrativeResult_(callback).status,'PREVIEW_READY');
reset(true); assert.throws(()=>ctx.handleLmiNarrativeResult_({...callback,sprintGoal:'invalid'}));
assert.equal(records[1][3],'NARRATIVE_REJECTED'); assert.equal(sends,0);
reset(false); failSend=true; assert.throws(()=>ctx.handleLmiNarrativeResult_(callback),/interrupted/);
assert.equal(records[1][3],'DELIVERY_UNKNOWN'); assert.equal(sends,1);
assert.equal(ctx.handleLmiNarrativeResult_(callback).status,'DUPLICATE'); assert.equal(sends,1);
// Native preview cannot enable production or send an email; credentials stay inside the request.
const properties={WEBHOOK_LMI_START_URL:'https://example.test/receiver',LMI_START_FORM_URL:'https://example.test/formResponse',LMI_START_BOARD_URL:'https://example.test/lmi',
  LMI_START_RECIPIENTS:'lmi@example.test',
  WEBHOOK_LMI_START_SECRET:'fixture-secret',SONG_FORM_TOKEN_1018:'fixture-token&+='};
let nativeRequest, nativeCode=200, nativeCalls=0;
ctx.PropertiesService={getScriptProperties:()=>({getProperty:k=>properties[k],setProperties:v=>Object.assign(properties,v)})};
const lmiRow=[];lmiRow[0]='LMI';lmiRow[13]='1018';
ctx.SpreadsheetApp={getActive:()=>({getSheetByName:()=>({getDataRange:()=>({getDisplayValues:()=>[[],lmiRow]})})})};
ctx.storyPointsFieldId_=()=> 'pts';
ctx.diagnosticTeam_=()=>({sprint:{id:26312,originBoardId:1018,state:'active',name:'LMI & export',startDate:'2026-09-23T15:00:00Z',endDate:'2026-10-07T15:00:00Z',goal:'Licensing + reporting'}});
ctx.UrlFetchApp={fetch:(url,options)=>{nativeCalls++;nativeRequest={url,options};return {getResponseCode:()=>nativeCode};}};
properties.LMI_START_MODE='production';
assert.throws(()=>ctx.runLmiStartRelayPreview(),/REFUSES_PRODUCTION/);assert.equal(nativeCalls,0);
delete properties.LMI_START_MODE;delete properties.WEBHOOK_LMI_START_SECRET;
assert.throws(()=>ctx.runLmiStartRelayPreview(),/NOT_CONFIGURED/);assert.equal(nativeCalls,0);
properties.WEBHOOK_LMI_START_SECRET='fixture-secret';sends=0;
assert.equal(ctx.runLmiStartRelayPreview().status,'SUBMITTED');assert.equal(sends,0);
assert.equal(properties.LMI_START_MODE,'preview');assert.equal(properties.LMI_START_RELAY_ENABLED,'true');
const posted=new URLSearchParams(nativeRequest.options.payload);
assert.equal(posted.get('entry.62524025'),properties.SONG_FORM_TOKEN_1018);
assert.equal(posted.get('entry.965099157'),'LMI & export');assert.equal(posted.get('entry.1722054716'),'started');
assert.equal(posted.size,9);assert.ok(nativeRequest.url.endsWith('/formResponse'));
assert.ok(!nativeRequest.options.payload.includes(properties.WEBHOOK_LMI_START_SECRET));
nativeCode=403;assert.throws(()=>ctx.runLmiStartRelayPreview(),/FORM_HTTP_403/);assert.equal(sends,0);
const readiness=ctx.inspectLmiStartRelayConfiguration();
assert.ok(Object.values(readiness).every(v=>typeof v==='boolean'));
assert.equal(readiness.receiverUrlCorrect,true);assert.equal(readiness.receiverSecretSaved,true);
assert.equal(readiness.formUrlSaved,true);assert.equal(readiness.boardUrlSaved,true);assert.equal(readiness.recipientsSaved,true);
assert.ok(!JSON.stringify(readiness).includes(properties.WEBHOOK_LMI_START_SECRET));
console.log('LMI pilot: metrics, evidence, risk, callback safety and native preview production/email guards passed.');
