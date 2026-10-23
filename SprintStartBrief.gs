/* LMI start-brief pilot. Fixed metrics and HTML; Rovo supplies evidenced prose only.
 * Configure Script Properties before enabling delivery: LMI_START_RECIPIENTS,
 * LMI_START_FORM_URL, WEBHOOK_LMI_START_URL, and WEBHOOK_LMI_START_SECRET. */

function lmiStartUrlIsValid_(url) {
  return /^https:\/\/[^\s]+$/.test(String(url || ''));
}

function lmiStartRecipients_(props) {
  const recipients=String(props.getProperty('LMI_START_RECIPIENTS') || '').trim();
  if (!recipients || recipients.split(',').some(value=>!/^\S+@\S+\.\S+$/.test(value.trim()))) {
    throw new Error('LMI_START_RECIPIENTS_NOT_CONFIGURED');
  }
  return recipients;
}

function lmiStartBoardUrl_(props) {
  const url=String(props.getProperty('LMI_START_BOARD_URL') || '').trim();
  if (!lmiStartUrlIsValid_(url)) throw new Error('LMI_START_BOARD_URL_NOT_CONFIGURED');
  return url;
}

function buildLmiStartFacts_(sprintId) {
  if (!/^\d+$/.test(String(sprintId))) throw new Error('BAD_START_SPRINT');
  const ss = SpreadsheetApp.getActive(), props=PropertiesService.getScriptProperties();
  const rows = ss.getSheetByName('Boards Index').getDataRange().getDisplayValues().slice(1).filter(r=>r[13]==='1018');
  if (rows.length!==1 || rows[0][0]!=='LMI') throw new Error('BAD_LMI_CONFIGURATION');
  const row = rows[0], sprint = diagnosticJira_('/rest/agile/1.0/sprint/'+sprintId);
  if (String(sprint.originBoardId)!=='1018' || sprint.state!=='active') throw new Error('SPRINT_NOT_ACTIVE_ON_LMI');
  const pointsField = storyPointsFieldId_();
  if (!pointsField) throw new Error('STORY_POINTS_FIELD_UNAVAILABLE');
  const risks = diagnosticJira_('/rest/api/3/field').filter(f=>f.name.toLowerCase()==='risk level');
  const riskField = risks.length===1 ? risks[0].id : '';
  const fields = ['summary','status','issuetype','parent',pointsField].concat(riskField ? [riskField] : []);
  const jql = '('+row[2].replace(/\bORDER\s+BY\s+[\s\S]*$/i,'').trim()+') AND sprint = '+sprintId+
    " AND issuetype != 'Epic' AND issuetype NOT IN subTaskIssueTypes()";
  const issues = diagnosticSearch_(jql,fields);
  const facts = lmiStartMetrics_(issues,pointsField,riskField,row);
  return Object.assign(facts,{boardId:'1018',teamName:'LMI',sprintId:String(sprint.id),sprintName:sprint.name,
    sprintStart:sprint.startDate,sprintEnd:sprint.endDate,sprintGoal:sprint.goal || '',
    boardUrl:lmiStartBoardUrl_(props),
    capturedAt:new Date().toISOString(),query:jql,riskField:riskField,
    previousSongUrl:''});
}

function lmiStartMetrics_(issues,pointsField,riskField,row) {
  const out = {issueCount:issues.length,points:0,unestimated:0,buckets:{'To Do':0,'In Progress':0,Done:0},
    risk:{Red:0,Amber:0,Green:0,unknown:0},issues:[]};
  const keys = new Set();
  issues.forEach(i=>{
    if (!/^[A-Z][A-Z0-9_]*-\d+$/.test(i.key) || keys.has(i.key)) throw new Error('INVALID_OR_DUPLICATE_ISSUE_KEY');
    keys.add(i.key);
    const f = i.fields, raw = f[pointsField], points = raw==null || raw==='' ? null : Number(raw);
    if (points!==null && (!Number.isFinite(points) || points<0)) throw new Error('INVALID_STORY_POINTS');
    if (points===null) out.unestimated++; else out.points+=points;
    out.buckets[diagnosticBucket_(f.status.name,row[3],row[4])]++;
    const value = riskField ? f[riskField] : null;
    const label = String(value && (value.value || value.name) || value || '').trim().toLowerCase();
    const risk = {red:'Red',high:'Red',amber:'Amber',medium:'Amber',green:'Green',low:'Green'}[label] || 'unknown';
    out.risk[risk]++;
    out.issues.push({key:i.key,summary:String(f.summary || '').slice(0,300),status:f.status.name,points:points,
      parentKey:f.parent && f.parent.key || '',risk:risk});
  });
  out.risk.assessed=issues.length-out.risk.unknown;
  out.risk.label=out.risk.Red ? 'Recorded Red risk' : out.risk.Amber ? 'Recorded Amber risk' :
    !out.risk.assessed ? 'Not assessed' : out.risk.unknown ? 'Incomplete risk coverage' : 'Recorded Green risk';
  return out;
}

function lmiNarrativeInput_(facts) {
  // Metrics always use all pages; cap only the prose evidence, with honest coverage.
  const evidence = facts.issues.slice(0,150);
  // Keep exact releases/environments in the fixed goal block, not generative prose.
  const prose=s=>String(s).replace(/\b(?:prod|production|dev|stage|release|green|amber|red)\b/gi,'')
    .replace(/\d+(?:\.\d+)*/g,'').replace(/\s+/g,' ').trim();
  return 'SPRINT GOAL: '+prose(facts.sprintGoal)+'\nISSUE EVIDENCE:\n'+
    evidence.map(i=>i.key+': '+prose(i.summary)).join('\n')+'\nThese are planned/captured work, not claimed delivery. Return only the required JSON.';
}

function validateLmiNarrative_(text,facts) {
  if (typeof text!=='string' || text.length>10000) throw new Error('BAD_NARRATIVE_SIZE');
  let result;
  try { result=JSON.parse(text); } catch (_) { throw new Error('BAD_NARRATIVE_JSON'); }
  const exactKeys = (o,keys)=>o && !Array.isArray(o) && Object.keys(o).sort().join(',')===keys.sort().join(',');
  const plain = (s,words)=>typeof s==='string' && s.trim().length>0 && s.length<=1000 &&
    s.trim().split(/\s+/).length<=words && !/[<>\d]|https?:\/\/|\[.+\]\(.+\)|\b(?:prod|production|dev|stage|green|amber|red)\b/i.test(s);
  if (!exactKeys(result,['summary','themes']) || !plain(result.summary,70) || !Array.isArray(result.themes) ||
      result.themes.length>5 || facts.issueCount>0 && !result.themes.length) throw new Error('BAD_NARRATIVE_SHAPE');
  const allowed = new Set(facts.issues.slice(0,150).map(i=>i.key));
  result.themes.forEach(t=>{
    if (!exactKeys(t,['label','description','evidenceKeys']) || !plain(t.label,6) || !plain(t.description,35) ||
        !Array.isArray(t.evidenceKeys) || !t.evidenceKeys.length || t.evidenceKeys.length>5 ||
        new Set(t.evidenceKeys).size!==t.evidenceKeys.length || t.evidenceKeys.some(k=>!allowed.has(k))) {
      throw new Error('BAD_THEME_EVIDENCE');
    }
  });
  return result;
}

function renderLmiStartBrief_(facts,narrative,preview) {
  const esc = s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cell = 'padding:10px 8px;border-bottom:1px solid #e3e0da;text-align:left;vertical-align:top;';
  const themes = narrative.themes.map(t=>'<tr><td style="'+cell+'"><strong>'+esc(t.label)+'</strong></td><td style="'+cell+'">'+
    esc(t.description)+'<br><small>'+t.evidenceKeys.map(esc).join(', ')+'</small></td></tr>').join('');
  return '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+
    '<style>'+RT_CSS+'</style></head><body><div class="container"><div class="content">'+
    '<div class="masthead"><div class="masthead-team">LMI · Sprint kickoff'+(preview?' · Preview':'')+'</div></div>'+
    '<h1 style="font-size:24px">'+esc(facts.sprintName)+'</h1><p>'+esc(narrative.summary)+'</p>'+
    (preview?'<p><strong>Current-scope preview; this is not a historical sprint-start baseline.</strong></p>':'')+
    '<div class="section"><div class="section-title">Captured scope</div><p><strong>'+facts.issueCount+' work items · '+
    facts.points+' estimated points</strong><br>'+facts.unestimated+' unestimated items</p><p>'+esc(facts.sprintStart)+' to '+
    esc(facts.sprintEnd)+'</p><p>To Do '+facts.buckets['To Do']+' · In Progress '+facts.buckets['In Progress']+
    ' · Board Done '+facts.buckets.Done+'</p><a href="'+esc(facts.boardUrl)+'">Open LMI board</a></div>'+
    '<div class="section"><div class="section-title">Sprint goal</div><p>'+esc(facts.sprintGoal || 'No goal supplied')+'</p></div>'+
    '<div class="section"><div class="section-title">Planned work themes</div><table style="width:100%;border-collapse:collapse">'+themes+'</table>'+
    (facts.issueCount>150?'<p><small>Themes use the first 150 captured issues; metrics include all '+facts.issueCount+'.</small></p>':'')+'</div>'+
    '<div class="section"><div class="section-title">Recorded issue risk</div><p>'+esc(facts.risk.label)+' · '+facts.risk.assessed+
    '/'+facts.issueCount+' assessed; '+facts.risk.unknown+' missing or unrecognized</p><p><small>Recorded issue risk is not a delivery forecast.</small></p></div>'+
    '<div class="footer">Captured '+esc(facts.capturedAt)+' · Daily Briefing</div></div></div></body></html>';
}

// Manual preparation only: writes a Kaplan-private fact file, sends nothing, creates no baseline.
function prepareLmiSprintStartPreview() {
  const row = SpreadsheetApp.getActive().getSheetByName('Boards Index').getDataRange().getDisplayValues().slice(1).find(r=>r[13]==='1018');
  const sprint = diagnosticTeam_(row,storyPointsFieldId_()).sprint;
  const facts = buildLmiStartFacts_(sprint.id);
  const file = DriveApp.createFile('LMI-start-facts-preview-'+fmtDate_(new Date())+'.json',JSON.stringify(facts,null,2),MimeType.PLAIN_TEXT);
  PropertiesService.getScriptProperties().setProperty('LMI_START_PREVIEW_FACTS_FILE',file.getId());
  Logger.log('Private facts preview: %s; %s issues, %s points, risk %s.',file.getUrl(),facts.issueCount,facts.points,facts.risk.label);
  return facts;
}

// Paste a tested agent JSON response into LMI Start Preview!B2, then run. Never sends.
function previewLmiSprintStartBrief() {
  const ss=SpreadsheetApp.getActive(), sheet=ss.getSheetByName('LMI Start Preview');
  if (!sheet) throw new Error('Paste the agent JSON into LMI Start Preview!B2 first.');
  const id=PropertiesService.getScriptProperties().getProperty('LMI_START_PREVIEW_FACTS_FILE');
  if (!id) throw new Error('Run prepareLmiSprintStartPreview first.');
  const facts=JSON.parse(DriveApp.getFileById(id).getBlob().getDataAsString());
  const narrative=validateLmiNarrative_(sheet.getRange('B2').getValue(),facts);
  const file=DriveApp.createFile('LMI-start-brief-preview-'+fmtDate_(new Date())+'.html',renderLmiStartBrief_(facts,narrative,true),MimeType.HTML);
  sheet.getRange('A3:B4').setValues([['Private preview',file.getUrl()],['Result','Validated; no email sent']]);
  Logger.log('Validated private preview: %s',file.getUrl());
}

// Manual readiness check: presence only, no credential values or request.
function inspectLmiStartRelayConfiguration() {
  const p=PropertiesService.getScriptProperties();
  const receiverUrl=p.getProperty('WEBHOOK_LMI_START_URL');
  const result={receiverSecretSaved:!!p.getProperty('WEBHOOK_LMI_START_SECRET'),
    receiverUrlSaved:!!p.getProperty('WEBHOOK_LMI_START_URL'),
    receiverUrlCorrect:lmiStartUrlIsValid_(receiverUrl),
    formUrlSaved:lmiStartUrlIsValid_(p.getProperty('LMI_START_FORM_URL')),
    boardUrlSaved:lmiStartUrlIsValid_(p.getProperty('LMI_START_BOARD_URL')),
    recipientsSaved:!!p.getProperty('LMI_START_RECIPIENTS'),
    boardTokenSaved:!!p.getProperty('SONG_FORM_TOKEN_1018'),productionMode:p.getProperty('LMI_START_MODE')==='production'};
  Logger.log('LMI readiness (no credential values): '+JSON.stringify(result));
  return result;
}

// Manual native acceptance: current scope, private preview only; never sends email.
function runLmiStartRelayPreview() {
  const props=PropertiesService.getScriptProperties();
  const receiverUrl=props.getProperty('WEBHOOK_LMI_START_URL');
  const formUrl=props.getProperty('LMI_START_FORM_URL');
  if (props.getProperty('LMI_START_MODE')==='production') throw new Error('PREVIEW_REFUSES_PRODUCTION_MODE');
  if (!lmiStartUrlIsValid_(receiverUrl) || !lmiStartUrlIsValid_(formUrl) || !props.getProperty('WEBHOOK_LMI_START_SECRET') ||
      !props.getProperty('SONG_FORM_TOKEN_1018')) throw new Error('START_RECEIVER_NOT_CONFIGURED');
  const rows=SpreadsheetApp.getActive().getSheetByName('Boards Index').getDataRange().getDisplayValues().slice(1).filter(r=>r[13]==='1018');
  if (rows.length!==1 || rows[0][0]!=='LMI') throw new Error('BAD_LMI_CONFIGURATION');
  const sprint=diagnosticTeam_(rows[0],storyPointsFieldId_()).sprint;
  if (!sprint || String(sprint.originBoardId)!=='1018' || sprint.state!=='active') throw new Error('SPRINT_NOT_ACTIVE_ON_LMI');
  const fields={'entry.62524025':props.getProperty('SONG_FORM_TOKEN_1018'),'entry.709934322':'1018',
    'entry.594101504':String(sprint.id),'entry.965099157':sprint.name,'entry.1756844489':'',
    'entry.1203236136':sprint.startDate,'entry.1492631562':sprint.endDate,'entry.712506626':sprint.goal || '',
    'entry.1722054716':'started'};
  props.setProperties({LMI_START_MODE:'preview',LMI_START_RELAY_ENABLED:'true'});
  const response=UrlFetchApp.fetch(formUrl,
    {method:'post',contentType:'application/x-www-form-urlencoded',muteHttpExceptions:true,
      payload:Object.keys(fields).map(k=>encodeURIComponent(k)+'='+encodeURIComponent(fields[k])).join('&')});
  const code=response.getResponseCode();
  if (code!==200) throw new Error('START_PREVIEW_FORM_HTTP_'+code);
  Logger.log('Native LMI preview submitted for sprint %s; inspect _Sprint_Start_Log for PREVIEW_READY. HTTP 200 alone is not completion.',sprint.id);
  return {status:'SUBMITTED',sprintId:String(sprint.id),httpCode:code};
}

// The receiver credential must be entered by the owner in Project Settings.
function handleLmiSprintStart_(sprint,e) {
  if (sprint.boardId!=='1018') throw new Error('START_PILOT_LMI_ONLY');
  const props=PropertiesService.getScriptProperties();
  if (props.getProperty('LMI_START_RELAY_ENABLED')!=='true') return {status:'START_PILOT_DISABLED'};
  const ss=SpreadsheetApp.getActive(), lock=LockService.getScriptLock(), key='started:1018:'+sprint.sprintId;
  lock.waitLock(30000);
  let log, row;
  try {
    log=ss.getSheetByName('_Sprint_Start_Log') || ss.insertSheet('_Sprint_Start_Log');
    if (!log.getLastRow()) log.appendRow(['key','boardId','sprintId','status','receivedAt','factsFile','resultFile','error']);
    const existing=log.getDataRange().getValues().findIndex((r,i)=>i>0 && r[0]===key);
    if (existing>=0) return {status:'DUPLICATE',key:key};
    row=log.getLastRow()+1; log.getRange(row,1,1,8).setValues([[key,'1018',sprint.sprintId,'PREPARING',new Date(),'','','']]);
    SpreadsheetApp.flush();
  } finally { lock.releaseLock(); }
  try {
    const facts=buildLmiStartFacts_(sprint.sprintId);
    facts.isPreview=props.getProperty('LMI_START_MODE')!=='production';
    const file=DriveApp.createFile('LMI-start-baseline-'+sprint.sprintId+'.json',JSON.stringify(facts),MimeType.PLAIN_TEXT);
    log.getRange(row,4).setValue('FORWARDING'); log.getRange(row,6).setValue(file.getId()); SpreadsheetApp.flush();
    const url=props.getProperty('WEBHOOK_LMI_START_URL') || '', secret=props.getProperty('WEBHOOK_LMI_START_SECRET') || '';
    if (!lmiStartUrlIsValid_(url) || !secret) throw new Error('START_RECEIVER_NOT_CONFIGURED');
    const payload={source:'boards-index-start',configVersion:1,eventType:'started',boardId:'1018',sprintId:sprint.sprintId,
      sprintName:facts.sprintName,sprintStart:facts.sprintStart,sprintEnd:facts.sprintEnd,isTest:facts.isPreview,
      duplicateKey:key,narrativeInput:lmiNarrativeInput_(facts),callbackToken:props.getProperty('SONG_FORM_TOKEN_1018')};
    const result=postSongWebhook_(url,secret,payload);
    if (!result.ok) throw new Error('START_RECEIVER_HTTP_'+(result.code || 'UNKNOWN'));
    // A fast callback may already have advanced the status. Never overwrite it.
    if (log.getRange(row,4).getValue()==='FORWARDING') log.getRange(row,4).setValue('FORWARDED');
    return {status:'FORWARDED',key:key};
  } catch (error) {
    const status=log.getRange(row,4).getValue();
    if (['PREPARING','FORWARDING'].includes(status)) {
      log.getRange(row,4).setValue(status==='FORWARDING'?'FORWARD_UNKNOWN':'FAILED');
      log.getRange(row,8).setValue('Start preparation/forwarding failed; inspect execution without exposing credentials.');
    }
    throw new Error('START_PREPARATION_OR_FORWARD_FAILED');
  }
}

function handleLmiNarrativeResult_(sprint) {
  if (sprint.boardId!=='1018') throw new Error('START_PILOT_LMI_ONLY');
  const props=PropertiesService.getScriptProperties(), ss=SpreadsheetApp.getActive(), lock=LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const log=ss.getSheetByName('_Sprint_Start_Log'), key='started:1018:'+sprint.sprintId;
    if (!log) throw new Error('START_BASELINE_MISSING');
    const records=log.getDataRange().getValues(), index=records.findIndex((r,i)=>i>0 && r[0]===key);
    if (index<0 || !records[index][5]) throw new Error('START_BASELINE_MISSING');
    if (['PREVIEW_READY','DELIVERED','DELIVERY_UNKNOWN','NARRATIVE_REJECTED'].includes(records[index][3])) return {status:'DUPLICATE',key:key};
    if (!['FORWARDED','FORWARDING','FORWARD_UNKNOWN'].includes(records[index][3])) throw new Error('START_NOT_AWAITING_NARRATIVE');
    const facts=JSON.parse(DriveApp.getFileById(records[index][5]).getBlob().getDataAsString());
    let narrative;
    try { narrative=validateLmiNarrative_(sprint.sprintGoal,facts); }
    catch (error) { log.getRange(index+1,4).setValue('NARRATIVE_REJECTED'); log.getRange(index+1,8).setValue(error.message); throw error; }
    const html=renderLmiStartBrief_(facts,narrative,facts.isPreview);
    const result=DriveApp.createFile('LMI-start-brief-'+sprint.sprintId+'.html',html,MimeType.HTML);
    log.getRange(index+1,7).setValue(result.getId());
    if (facts.isPreview) {
      log.getRange(index+1,4).setValue('PREVIEW_READY'); Logger.log('Private start preview: %s',result.getUrl());
    } else {
      log.getRange(index+1,4).setValue('DELIVERY_UNKNOWN'); SpreadsheetApp.flush();
      MailApp.sendEmail({to:lmiStartRecipients_(props),subject:'Sprint kickoff — LMI — '+facts.sprintName,body:narrative.summary,htmlBody:html});
      log.getRange(index+1,4).setValue('DELIVERED');
    }
    return {status:facts.isPreview?'PREVIEW_READY':'DELIVERED',key:key};
  } finally { lock.releaseLock(); }
}
