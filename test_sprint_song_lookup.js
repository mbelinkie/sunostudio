const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync('sprint_song_lookup.gs', 'utf8'), context);
const headers = ['TEAM', 'SM EMAIL', 'SLACK ID', 'JIRA PROJECT', 'BOARD ID',
  'CURRENT GENRE', 'SEND SONG', 'NAMES IN SONGS'];
const sim = ['Sim', 'owner@example.test', '', 'SIM', '3041',
  'Tamil kuthu, animated vocals, pounding folk percussion, brass bursts, explosive dance energy, 145bpm', 'X', 'y'];
const rows = [headers, sim];
const roster = [['Team','Member Email','Lyric Name','Aliases','Active'],
  ['Home Ghost','','Lea Haas','lea.haas','y'],['Home Ghost','','Excluded Manager','manager','n'],
  ['Other Team','','Other Person','other','y']];
assert.equal(context.sprintSongNameRoster_(roster,'Home Ghost','1168').names,'Lea Haas (aliases: lea.haas)');
assert.equal(context.sprintSongNameRoster_([],'Home Ghost','1168').restricted,true);
assert.equal(context.sprintSongNameRoster_([],'Sim','3041').restricted,true);
const ghostRows = [headers,['Home Ghost','owner@example.test','C00000001','HOME','1168','pop','Slack','y']];
const ghostSprint = {boardId:'1168',sprintId:'28212',closedSprintName:'HOME GHOST 26.3.7',
  sprintStart:'2026-09-24T15:53:29Z',sprintEnd:'2026-10-08T04:00:00Z'};
assert.equal(context.buildSprintSongPayload_(ghostRows,ghostSprint).namesInSongs,'n');
const ghostPayload = context.buildSprintSongPayload_(ghostRows,ghostSprint,undefined,roster);
assert.equal(ghostPayload.namesInSongs,'y');
assert.equal(ghostPayload.namePolicy,'fair-roster-rotation');
assert.equal(ghostPayload.allowedSongNames,'Lea Haas (aliases: lea.haas)');
const found = context.lookupSprintSongBoard(rows, 3041);
assert.equal(found.ok, true);
assert.equal(found.emailAddress, 'owner@example.test');
assert.equal(found.slackChannelId, '');
assert.equal(found.jiraProject, 'SIM');
assert.equal(found.namesInSongs, 'y');
assert.equal(context.lookupSprintSongBoard(rows, 9999).error, 'NO_MATCH');
assert.equal(context.lookupSprintSongBoard([...rows, sim], 3041).error, 'DUPLICATE_BOARD_ID');
assert.equal(context.lookupSprintSongBoard([headers, [...sim.slice(0, 6), '', 'y']], 3041).error, 'SONG_DISABLED');
assert.equal(context.lookupSprintSongBoard([headers, ['Sim', '', '', 'SIM', '3041', 'genre', 'X', 'y']], 3041).error, 'BAD_EMAIL');

const sprint = {boardId:'3041', sprintId:'26013', closedSprintName:'SIM-26.3.6',
  sprintStart:'2026-09-10T18:18:38.5+0000', sprintEnd:'2026-09-24T14:52:25.3+0000', sprintGoal:'Audio & authoring'};
const before = JSON.stringify(rows);
const withChannel = [headers, sim.map((v,i)=>i===2?'C00000002':v)];
const build = (options, data=withChannel, meta=sprint)=>context.buildSprintSongPayload_(data,meta,options);
const testOptions = {namesInSongs:'n',testRunId:'run-00001'};
const noNames = build(testOptions), names = build({...testOptions,namesInSongs:'y',testRunId:'run-00002'});
assert.equal(noNames.namesInSongs,'n'); assert.equal(names.namesInSongs,'n');
assert.equal(noNames.slackChannelId,'C00000002');
assert.equal(build({...testOptions,slackChannelIdOverride:''}).slackChannelId,'');
assert.equal(build({...testOptions,slackChannelIdOverride:'C00000003'}).slackChannelId,'C00000003');
assert.equal(names.deliveryMode,'none'); assert.equal(names.emailAddress,'');
assert.equal(names.source,'boards-index-debug'); assert.equal(names.configVersion,2);
assert.equal(names.duplicateKey,'test:run-00002');
assert.throws(()=>build({...testOptions,namesInSongs:'yes'}),/BAD_TEST_NAMES/);
assert.throws(()=>build({...testOptions,slackChannelIdOverride:null}),/BAD_TEST_SLACK_OVERRIDE/);
assert.throws(()=>build({...testOptions,slackChannelIdOverride:'general'}),/BAD_TEST_SLACK_OVERRIDE/);
assert.throws(()=>build(testOptions,rows,{...sprint,sprintId:'1 OR 1=1'}),/BAD_SPRINT/);
assert.throws(()=>build(testOptions,rows,{...sprint,sprintEnd:'garbage'}),/BAD_SPRINT_DATES/);
assert.throws(()=>build(undefined),/BOARD_NOT_MIGRATED/);
const emailRows = [headers,sim.map((v,i)=>i===6?'E-mail':v)];
const prod = build(undefined,emailRows);
assert.equal(prod.duplicateKey,'3041:26013'); assert.equal(prod.source,'boards-index-form');
assert.equal(prod.deliveryMode,'email'); assert.equal(prod.emailAddress,sim[1]);
const disabledRows = [headers,sim.map((v,i)=>i===6?'N':v)];
assert.equal(build(testOptions,disabledRows).deliveryMode,'none');
assert.throws(()=>build(undefined,disabledRows),/SONG_DISABLED/);
assert.equal(JSON.stringify(rows),before);

let fetches=0, writes=0, uuid=0;
let properties={SONG_TEST_DELIVERY_SLACK_CHANNEL:'C00000000'};
let jiraSprint={id:26013,originBoardId:3041,state:'closed',name:'SIM-26.3.6',
  startDate:sprint.sprintStart,endDate:'2026-09-25T12:00:00Z',completeDate:sprint.sprintEnd,goal:sprint.sprintGoal};
Object.assign(context,{
  SpreadsheetApp:{getActive:()=>({getSheetByName:()=>({getDataRange:()=>({getDisplayValues:()=>withChannel}),setValue:()=>{writes++;}})})},
  Utilities:{getUuid:()=>`run-${String(++uuid).padStart(5,'0')}`},
  Logger:{log:()=>{}}, PropertiesService:{getScriptProperties:()=>({getProperty:k=>properties[k]})},
  diagnosticJira_:()=>jiraSprint, diagnosticAgilePages_:()=>[],
  postSongWebhook_:()=>{fetches++;return {ok:true,code:200};}
});
vm.runInContext(fs.readFileSync('SprintSongDebug.gs','utf8'),context);
assert.equal(context.debugSprintSong().status,'PREVIEW');
const simpleOptions={team:'Sim',sprintId:'26013',namesInSongs:'y',dryRun:true};
const preview=context.runSprintSongDebug_(simpleOptions);
assert.equal(preview.status,'PREVIEW');
assert.equal(preview.payload.closedSprintName,jiraSprint.name);
assert.equal(preview.payload.sprintEnd,jiraSprint.completeDate);
assert.equal(preview.payload.sprintGoal,jiraSprint.goal);
assert.equal(preview.payload.slackChannelId,'C00000002');
assert.equal(preview.payload.deliverySlackChannelId,'C00000000');
assert.equal(preview.payload.deliveryMode,'slack');
assert.equal(preview.payload.emailAddress,'');
assert.equal(preview.payload.genreSelect,sim[5]);
assert.ok(Date.parse(preview.payload.testStartedAt));
assert.equal(context.runSprintSongDebug_({...simpleOptions,team:'3041'}).payload.boardId,'3041');
assert.throws(()=>context.runSprintSongDebug_({...simpleOptions,team:'Unknown'}),/match exactly one/);
assert.throws(()=>context.runSprintSongDebug_({...simpleOptions,sprintId:'26013 OR 1=1'}),/numeric Jira sprint/);
assert.throws(()=>context.runSprintSongDebug_({...simpleOptions,namesInSongs:'yes'}),/namesInSongs/);
const originalSprint=jiraSprint;
jiraSprint={...jiraSprint,originBoardId:1168};
assert.throws(()=>context.runSprintSongDebug_(simpleOptions),/does not belong/);
context.diagnosticAgilePages_=()=>[jiraSprint];
assert.equal(context.runSprintSongDebug_(simpleOptions).status,'PREVIEW');
jiraSprint={...originalSprint,state:'future'};
assert.throws(()=>context.runSprintSongDebug_(simpleOptions),/active or completed/);
jiraSprint={...originalSprint,state:'active',endDate:'2099-01-01T00:00:00Z'};
const active=context.runSprintSongDebug_(simpleOptions).payload;
assert.equal(active.sprintEnd,active.testStartedAt);
jiraSprint=originalSprint;
assert.equal(fetches,0); assert.equal(writes,0);
assert.throws(()=>context.runSprintSongDebug_({...simpleOptions,dryRun:'false'}),/Set dryRun/);
assert.throws(()=>context.runSprintSongDebug_({...simpleOptions,dryRun:false}),/Configure the separate test rule/);
properties={WEBHOOK_SONG_TEST_URL:'https://example.test/test',WEBHOOK_SONG_TEST_SECRET:'secret',WEBHOOK_SONG_URL:'https://example.test/test/'};
properties.SONG_TEST_DELIVERY_SLACK_CHANNEL='C00000000';
assert.throws(()=>context.sendSprintSongDebug_(preview.payload),/differ from the production/);
properties.WEBHOOK_SONG_TEST_URL='https://example.test/debug';
assert.equal(context.sendSprintSongDebug_(preview.payload).status,'ACCEPTED');
assert.equal(fetches,1); assert.equal(writes,0);
assert.throws(()=>context.sendSprintSongDebug_({...preview.payload,deliveryMode:'none'}),/Invalid test payload/);
assert.throws(()=>context.sendSprintSongDebug_({...preview.payload,deliverySlackChannelId:'C00000003'}),/Invalid test payload/);
assert.throws(()=>context.sendSprintSongDebug_({...preview.payload,isTest:false}),/Invalid test payload/);

const deliveryDebugRule=JSON.parse(fs.readFileSync('sprint_song_debug_rule.json','utf8')).rules[0];
const deliveryGuard=deliveryDebugRule.components.find(c=>c.type==='jira.comparator.condition' &&
  c.value.first==='{{webhookData.deliveryMode}}:{{webhookData.deliverySlackChannelId}}');
assert.equal(deliveryGuard.value.second,'slack:C00000000');
const debugMail=deliveryDebugRule.components.find(c=>c.type==='action.send-templated-email').value.templateContextData.body;
assert.ok(debugMail.includes('===DELIVERY===\nslack'));
assert.ok(debugMail.includes('===SLACK CHANNEL ID===\n{{webhookData.deliverySlackChannelId}}'));
assert.ok(!debugMail.includes('===EMAIL==='));
assert.ok(!debugMail.includes('===SLACK CHANNEL ID===\n{{slackChannelId}}'));

const rule = JSON.parse(fs.readFileSync('sprint_song_automation_direct_payload.json', 'utf8')).rules[0];
const components = rule.components;
assert.equal(rule.state, 'DISABLED');
assert.equal(components.some(c => c.type === 'rovo.invoke-agent.action' &&
  c.value.agentInstruction.includes('Boards Index')), false);
assert.equal(components[7].value.first, '{{webhookData.configVersion}}');
assert.equal(components[9].value.first, '{{webhookData.boardId}}');
for (const key of ['teamName', 'jiraProject', 'emailAddress', 'slackChannelId',
  'genreSelect', 'namesInSongs', 'sendSongFlag']) {
  const variable = components.find(c => c.type === 'jira.create.variable' && c.value.name.value === key);
  assert.equal(variable.value.query.value, `{{webhookData.${key}}}`);
}
const slackBlock = components.find(c => c.type === 'jira.condition.container.block');
assert.equal(slackBlock.children[0].conditions[0].value.first, '{{slackChannelId}}');
assert.equal(slackBlock.children[0].children[0].type, 'rovo.invoke-agent.action');
const debugRule = JSON.parse(fs.readFileSync('sprint_song_debug_rule.json','utf8')).rules[0];
assert.equal(debugRule.state,'DISABLED'); // Import artifact is safe; live test rule is enabled separately.
assert.equal(debugRule.tags.length,0);
assert.equal(debugRule.components.filter(c=>c.type==='jira.comparator.condition').length,9);
assert.equal(debugRule.components.some(c=>c.type==='rovo.invoke-agent.action' && c.value.agentInstruction.includes('Boards Index')),false);
const mail = debugRule.components.find(c=>c.type==='action.send-templated-email');
assert.ok(mail.value.templateContextData.body.endsWith('===DELIVERY===\nslack\n\n===SLACK CHANNEL ID===\n{{webhookData.deliverySlackChannelId}}\n\n===END==='));
const emptyDigest = debugRule.components.find(c=>c.type==='jira.create.variable' && c.value.name.value==='slackDigest');
assert.equal(emptyDigest.value.query.value,'{{webhookData.emailAddress}}');
let prompts=[];
const ui={Button:{OK:'ok'},ButtonSet:{OK_CANCEL:'buttons'},prompt:()=>prompts.shift()};
context.SpreadsheetApp.getUi=()=>ui;
context.PropertiesService.getScriptProperties=()=>({getProperty:k=>properties[k],setProperties:p=>Object.assign(properties,p)});
const answer=(text,button='ok')=>({getSelectedButton:()=>button,getResponseText:()=>text});
prompts=[answer('','cancel')]; context.configureSprintSongDebug();
prompts=[answer('https://example.test/test')]; assert.throws(()=>context.configureSprintSongDebug(),/separate HTTPS/);
prompts=[answer('https://example.test/new'),answer('','cancel')]; context.configureSprintSongDebug();
assert.equal(properties.WEBHOOK_SONG_TEST_URL,'https://example.test/debug');
prompts=[answer('https://example.test/new'),answer('test-token')]; context.configureSprintSongDebug();
assert.equal(properties.WEBHOOK_SONG_TEST_URL,'https://example.test/new');
assert.equal(properties.WEBHOOK_SONG_TEST_SECRET,'test-token');

// Form boundary + persisted state: a duplicate or ambiguous send must never call Jira twice.
class Sheet {
  constructor(data=[], id=99) { this.data=data.map(r=>r.slice()); this.id=id; }
  getSheetId() { return this.id; }
  getParent() { return spreadsheet; }
  getLastRow() { return this.data.length; }
  getLastColumn() { return this.data[0].length; }
  getDataRange() { return {getValues:()=>this.data.map(r=>r.slice()), getDisplayValues:()=>this.data.map(r=>r.map(String))}; }
  appendRow(r) { this.data.push(r.slice()); }
  setFrozenRows() {}
  getRange(row,col=1,height=1,width=1) {
    if (row === 'A:A') return {setNumberFormat:format=>{this.keyFormat=format;}};
    return {getRow:()=>row,getSheet:()=>this,
      getValues:()=>this.data.slice(row-1,row-1+height).map(r=>r.slice(col-1,col-1+width)),
      getDisplayValues:()=>this.data.slice(row-1,row-1+height).map(r=>r.slice(col-1,col-1+width).map(String)),
      setValues:values=>values.forEach((r,i)=>{this.data[row-1+i] ||= []; r.forEach((v,j)=>this.data[row-1+i][col-1+j]=v);}),
      setValue:v=>{this.data[row-1][col-1]=v;}};
  }
}
const boardSheet=new Sheet(emailRows);
context.syncSongNameHistory_=()=>({added:0});
let ledger, locked=false, forwarded=[], relayResult={ok:true,code:200};
const formFields={relayToken:'dummy-test-token-000000000000000000000',...sprint,newSprintName:''};
const formTitles=Object.keys(formFields);
const responses=new Sheet([formTitles,Object.values(formFields)]);
const spreadsheet={getId:()=> 'bound-spreadsheet',getSheetByName:name=>name==='Boards Index'?boardSheet:ledger,
  insertSheet:()=>ledger=new Sheet(),getSheets:()=>[responses,boardSheet,ledger].filter(Boolean)};
properties={SONG_FORM_RELAY_ENABLED:'true',SONG_FORM_RESPONSE_SHEET_ID:'99',
  SONG_FORM_TOKEN_3041:formFields.relayToken,WEBHOOK_SONG_FORM_URL:'https://example.test/form-receiver',
  WEBHOOK_SONG_FORM_SECRET:'dummy-secret',WEBHOOK_SONG_URL:'https://example.test/legacy',
  WEBHOOK_SONG_TEST_URL:'https://example.test/debug'};
Object.assign(context,{
  SpreadsheetApp:{getActive:()=>spreadsheet,flush:()=>{},getUi:()=>ui},
  LockService:{getScriptLock:()=>({waitLock:()=>{assert.equal(locked,false);locked=true;},releaseLock:()=>{locked=false;}})},
  postSongWebhook_:(url,secret,payload)=>{assert.equal(locked,false);forwarded.push(JSON.parse(JSON.stringify(payload)));return relayResult;}
});
const event=(changes={},sheet=responses)=>({range:sheet.getRange(2),namedValues:Object.fromEntries(
  Object.entries({...formFields,...changes}).map(([k,v])=>[k,[v]]))});
const relay=e=>context.handleSprintSongFormSubmit(e);
properties.SONG_FORM_RELAY_ENABLED='false'; assert.equal(relay(undefined).status,'DISABLED');
properties.SONG_FORM_RELAY_ENABLED='true';
assert.throws(()=>relay(event({relayToken:'forged'})),/BAD_TOKEN/); assert.equal(ledger,undefined);
assert.throws(()=>relay(event({},new Sheet([],123))),/WRONG_SHEET/);
const repeated=event(); repeated.namedValues.boardId.push('3041'); assert.throws(()=>relay(repeated),/BAD_FIELDS/);
assert.throws(()=>relay(event({sprintStart:'bad'})),/BAD_SPRINT_DATES/);
assert.equal(relay(event()).status,'SENT'); assert.equal(forwarded.length,1);
assert.equal(forwarded[0].emailAddress,sim[1]); assert.equal(forwarded[0].source,'boards-index-form');
assert.equal('relayToken' in forwarded[0],false); assert.equal(JSON.stringify(ledger.data).includes(formFields.relayToken),false);
assert.equal(ledger.keyFormat,'@');
// Existing Sheets rows may have coerced board:sprint into a numeric duration.
ledger.data[1][0]=144.77291666666667;
assert.equal(relay(event()).status,'DUPLICATE'); assert.equal(forwarded.length,1);
boardSheet.data=disabledRows.map(r=>r.slice()); assert.equal(relay(event({sprintId:'26014'})).status,'SKIPPED');
assert.equal(forwarded.length,1);
boardSheet.data=emailRows.map(r=>r.slice()); relayResult={ok:false,code:''};
assert.throws(()=>relay(event({sprintId:'26015'})),/CHECK_JIRA_AUDIT/);
assert.equal(ledger.data.at(-1)[4],'UNKNOWN'); assert.equal(relay(event({sprintId:'26015'})).status,'DUPLICATE');
assert.equal(forwarded.length,2);
relayResult={ok:false,code:403}; assert.throws(()=>relay(event({sprintId:'26016'})),/HTTP_403/);
assert.equal(relay(event({sprintId:'26016'})).status,'DUPLICATE'); assert.equal(forwarded.length,3);
properties.WEBHOOK_SONG_FORM_URL=properties.WEBHOOK_SONG_TEST_URL;
assert.throws(()=>relay(event({sprintId:'26017'})),/RECEIVER_NOT_CONFIGURED/); assert.equal(forwarded.length,3);
properties.WEBHOOK_SONG_FORM_URL='https://example.test/form-receiver';
boardSheet.data=[headers,...emailRows.slice(1),...emailRows.slice(1)];
assert.throws(()=>relay(event({sprintId:'26018'})),/DUPLICATE_BOARD_ID/); assert.equal(forwarded.length,3);
assert.equal(locked,false);
boardSheet.data=emailRows.map(r=>r.slice()); relayResult={ok:true,code:200};
prompts=[answer('2')]; assert.throws(()=>context.replaySprintSongRelay(),/NOT_REPLAYABLE/);
// Replay an authenticated failed row after a human has checked Jira; stored form data must match its key.
responses.data.push(formTitles.map(k=>String({...formFields,sprintId:'26016'}[k])));
ledger.data[4][3]=3; ledger.data[4][0]=144.775; prompts=[answer('5')]; assert.equal(context.replaySprintSongRelay().status,'SENT');
assert.equal(forwarded.length,4); assert.equal(forwarded.at(-1).sprintId,'26016');
assert.equal(relay(event({sprintId:'26016'})).status,'DUPLICATE');
// User-run setup preserves existing credentials, installs once, and never enables or sends.
responses.id=1664690944;
properties.SONG_FORM_RESPONSE_SHEET_ID='1664690944';
responses.data=[['Timestamp','relayToken','boardId','sprintId','closedSprintName','newSprintName','sprintStart','sprintEnd','sprintGoal']];
properties.SONG_FORM_RELAY_ENABLED='false';
const existingBoardToken=properties.SONG_FORM_TOKEN_3041;
let installed=0;
const triggerBuilder={forSpreadsheet:s=>{assert.equal(s,spreadsheet);return triggerBuilder;},
 onFormSubmit:()=>triggerBuilder,create:()=>{installed++;}};
context.ScriptApp={getProjectTriggers:()=>installed?[{getHandlerFunction:()=> 'handleSprintSongFormSubmit'}]:[],
 newTrigger:handler=>{assert.equal(handler,'handleSprintSongFormSubmit');return triggerBuilder;}};
context.Utilities.getUuid=()=> '12345678-1234-4234-8234-123456789abc';
prompts=[answer('','cancel')]; context.configureSprintSongFormRelay(); assert.equal(installed,0);
prompts=[answer('')]; assert.throws(()=>context.configureSprintSongFormRelay(),/secret is missing/);
prompts=[answer('form-receiver-secret')]; context.configureSprintSongFormRelay();
assert.equal(installed,1); assert.equal(properties.SONG_FORM_RELAY_ENABLED,'false');
assert.equal(properties.SONG_FORM_TOKEN_3041,existingBoardToken);
assert.equal(properties.SONG_FORM_RESPONSE_SHEET_ID,'1664690944');
delete properties.SONG_FORM_TOKEN_3041;
prompts=[answer('form-receiver-secret')]; context.configureSprintSongFormRelay();
assert.equal(installed,1); assert.match(properties.SONG_FORM_TOKEN_3041,/^[a-f0-9]{64}$/);
assert.equal(forwarded.length,4);
context.enableSprintSongFormRelay(); assert.equal(properties.SONG_FORM_RELAY_ENABLED,'true'); assert.equal(properties.SONG_FORM_LEGACY_DISABLED,'true');
context.disableSprintSongFormRelay(); assert.equal(properties.SONG_FORM_RELAY_ENABLED,'false');
installed=0; assert.throws(()=>context.enableSprintSongFormRelay(),/exactly one/); installed=1;
assert.equal(forwarded.length,4);
const formRule = JSON.parse(fs.readFileSync('sprint_song_automation_form_v2.json','utf8')).rules[0];
assert.equal(formRule.state,'DISABLED');
assert.equal(formRule.trigger.value.webhookToken,'');
const guards=formRule.components.filter(c=>c.type==='jira.comparator.condition');
assert.equal(guards.length,7);
assert.ok(guards.some(c=>c.value.first==='{{webhookData.source}}' && c.value.second==='boards-index-form'));
assert.ok(guards.some(c=>c.value.first==='{{webhookData.isTest}}' && c.value.second==='false'));
const nodes=[];
function collect(nodesIn) { for(const c of nodesIn) { nodes.push(c); collect(c.children||[]); collect(c.conditions||[]); } }
collect([formRule.trigger,...formRule.components]);
const ids=new Set(nodes.map(c=>c.id)); assert.equal(ids.size,nodes.length);
for(const c of nodes) { if(c.parentId) assert.ok(ids.has(c.parentId)); if(c.conditionParentId) assert.ok(ids.has(c.conditionParentId)); }
const agents=nodes.filter(c=>c.type==='rovo.invoke-agent.action');
assert.equal(agents.length,2);
assert.equal(agents.some(c=>c.value.agentInstruction.includes('Boards Index')),false);
const writer=agents.find(c=>c.value.agentInstruction.includes('Write a sprint song'));
assert.ok(writer.value.agentInstruction.includes('LYRICS is sent to the song provider'));
assert.ok(writer.value.agentInstruction.includes('DISPLAY LYRICS is optional'));
const deliveryMails=nodes.filter(c=>c.type==='action.send-templated-email');
assert.equal(deliveryMails.length,2);
for(const c of deliveryMails) {
 const body=c.value.templateContextData.body;
 assert.ok(body.endsWith('\n\n===END==='));
 assert.equal((body.match(/===END===/g)||[]).length,2); // One replacement literal and one final marker.
 assert.equal(body.includes('===EMAIL===') && body.includes('===SLACK CHANNEL ID==='),false);
 assert.equal(body.includes('===DELIVERY==='),true);
}
assert.ok(deliveryMails.some(c=>c.value.templateContextData.body.includes('===DELIVERY===\nemail\n\n===EMAIL===')));
assert.ok(deliveryMails.some(c=>c.value.templateContextData.body.includes('===DELIVERY===\nslack\n\n===SLACK CHANNEL ID===')));
console.log('Lookup, debug isolation, Form authentication, dedupe/replay and version-2 Jira rule checks pass');

// Kaplan migration accepts both sources and does not regenerate or disable anything.
const kaplanResponses = new Sheet(responses.data, 1958283758);
spreadsheet.getSheets = () => [responses, kaplanResponses, boardSheet, ledger];
properties.SONG_FORM_OWNER_EMAIL = 'owner@example.test';
context.Session = {getEffectiveUser: () => ({getEmail: () => 'owner@example.test'})};
properties.SONG_FORM_RELAY_ENABLED = 'true';
const receiverBefore = properties.WEBHOOK_SONG_FORM_SECRET;
const boardTokenBefore = properties.SONG_FORM_TOKEN_3041;
context.prepareKaplanSprintSongRelay();
assert.equal(properties.SONG_FORM_RELAY_ENABLED, 'true');
assert.equal(properties.WEBHOOK_SONG_FORM_SECRET, receiverBefore);
assert.equal(properties.SONG_FORM_TOKEN_3041, boardTokenBefore);
assert.equal(properties.SONG_FORM_RESPONSE_SHEET_ID, '1664690944');
assert.equal(properties.SONG_FORM_LEGACY_RESPONSE_SHEET_ID, '1664690944');
assert.deepEqual(Array.from(context.sprintSongResponseSheetIds_(context.PropertiesService.getScriptProperties())), ['1664690944', '1958283758']);
assert.equal(installed, 1);
const migratedEvent = event({relayToken: properties.SONG_FORM_TOKEN_3041, sprintId: '26030'}, kaplanResponses);
assert.equal(relay(migratedEvent).status, 'SENT');
assert.equal(ledger.data.at(-1)[9], '1958283758');
assert.equal(relay(event({relayToken: properties.SONG_FORM_TOKEN_3041, sprintId: '26030'})).status, 'DUPLICATE');
assert.throws(() => relay(event({relayToken: properties.SONG_FORM_TOKEN_3041}, new Sheet([], 777))), /WRONG_SHEET/);
context.Session = {getEffectiveUser: () => ({getEmail: () => 'other@example.test'})};
assert.throws(() => context.prepareKaplanSprintSongRelay(), /configured relay owner/);
console.log('Kaplan dual-Form migration preserves credentials, ownership guard and cross-source dedupe');

context.Session = {getEffectiveUser: () => ({getEmail: () => 'owner@example.test'})};
context.finalizeKaplanSprintSongRelay();
assert.equal(properties.SONG_FORM_RESPONSE_SHEET_ID, '1958283758');
assert.equal(properties.SONG_FORM_ADDITIONAL_RESPONSE_SHEET_IDS, '');
assert.equal(properties.WEBHOOK_SONG_FORM_SECRET, receiverBefore);
assert.equal(properties.SONG_FORM_TOKEN_3041, boardTokenBefore);
assert.throws(() => relay(event({relayToken: boardTokenBefore, sprintId: '26030'})), /WRONG_SHEET/);
assert.equal(relay(migratedEvent).status, 'DUPLICATE');
// Explicit recovery can still validate a historical source; live events cannot.
assert.equal(context.readSprintSongForm_(event({relayToken: boardTokenBefore, sprintId: '26030'}), context.PropertiesService.getScriptProperties(), spreadsheet, true).sprintId, '26030');
console.log('Final Kaplan source is exclusive for live events; historical recovery remains available');

// Optional start events dispatch only after the same Form authentication; close ledger is untouched.
properties.SONG_FORM_TOKEN_1018=formFields.relayToken;
const closeLedgerBefore=JSON.stringify(ledger.data), postsBefore=forwarded.length;
context.handleLmiSprintStart_=s=>({status:'START_PILOT_DISABLED',key:'started:'+s.boardId+':'+s.sprintId});
context.handleLmiNarrativeResult_=s=>({status:'PREVIEW_READY',key:'started:'+s.boardId+':'+s.sprintId});
assert.equal(relay(event({boardId:'1018',eventType:'started'},kaplanResponses)).status,'START_PILOT_DISABLED');
assert.equal(relay(event({boardId:'1018',eventType:'brief-result'},kaplanResponses)).status,'PREVIEW_READY');
assert.equal(JSON.stringify(ledger.data),closeLedgerBefore); assert.equal(forwarded.length,postsBefore);
assert.throws(()=>relay(event({boardId:'1018',eventType:'arbitrary'},kaplanResponses)),/BAD_EVENT_TYPE/);
assert.throws(()=>relay(event({boardId:'1018',eventType:'started',relayToken:'bad'},kaplanResponses)),/BAD_TOKEN/);
assert.throws(()=>context.readSprintSongForm_(event({boardId:'1018',eventType:'started'},kaplanResponses),context.PropertiesService.getScriptProperties(),spreadsheet,true),/REPLAY_CLOSE_ONLY/);
console.log('Started and narrative events authenticate before dispatch and preserve the close-song ledger.');
