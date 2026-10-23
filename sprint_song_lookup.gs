/* Exact board lookup, payloads and Form relay. Relay is disabled until configured. */
function lookupSprintSongBoard(rows, boardId, allowDisabled) {
  var id = String(boardId == null ? '' : boardId).trim();
  if (!/^\d+$/.test(id)) return {ok: false, error: 'BAD_BOARD_ID'};
  var headers = (rows[0] || []).map(h => String(h).trim());
  var names = ['BOARD ID', 'TEAM', 'JIRA PROJECT', 'SM EMAIL', 'SLACK ID',
    'CURRENT GENRE', 'NAMES IN SONGS', 'SEND SONG'];
  var columns = {};
  for (var i = 0; i < names.length; i++) {
    if (headers.indexOf(names[i]) < 0) return {ok: false, error: 'MISSING_COLUMN'};
    if (headers.indexOf(names[i]) !== headers.lastIndexOf(names[i])) return {ok: false, error: 'DUPLICATE_COLUMN'};
    columns[names[i]] = headers.indexOf(names[i]);
  }
  var matches = rows.slice(1).filter(function (row) {
    return String(row[columns['BOARD ID']] || '').trim() === id;
  });
  if (matches.length !== 1) return {ok: false, error: matches.length ? 'DUPLICATE_BOARD_ID' : 'NO_MATCH'};
  var row = matches[0];
  function value(name) { return String(row[columns[name]] || '').trim(); }
  var flag = value('SEND SONG');
  if (!['', 'N', 'X', 'E-mail', 'Slack'].includes(flag)) return {ok: false, error: 'BAD_SEND_SONG'};
  if (['', 'N'].includes(flag) && allowDisabled !== true) return {ok: false, error: 'SONG_DISABLED'};
  if (!value('TEAM') || !/^[A-Z][A-Z0-9_]*$/.test(value('JIRA PROJECT')) || !value('CURRENT GENRE')) {
    return {ok: false, error: 'INCOMPLETE_ROW'};
  }
  var email = value('SM EMAIL'), channel = value('SLACK ID');
  if (['X', 'E-mail'].includes(flag) && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return {ok: false, error: 'BAD_EMAIL'};
  if ((channel || flag === 'Slack') && !/^[CG][A-Z0-9]{8,}$/.test(channel)) return {ok: false, error: 'BAD_SLACK_CHANNEL'};
  var namesFlag = value('NAMES IN SONGS').toLowerCase();
  if (!['', 'y', 'n'].includes(namesFlag)) return {ok: false, error: 'BAD_NAMES_FLAG'};
  return {
    ok: true,
    teamName: value('TEAM'),
    jiraProject: value('JIRA PROJECT'),
    emailAddress: value('SM EMAIL'),
    slackChannelId: value('SLACK ID'),
    genreSelect: value('CURRENT GENRE'),
    namesInSongs: namesFlag || 'n',
    sendSongFlag: flag,
    deliveryMode: flag === 'Slack' ? 'slack' : ['X', 'E-mail'].includes(flag) ? 'email' : 'none'
  };
}

function buildSprintSongPayload_(rows, sprint, testOptions, rosterRows) {
  const isTest = testOptions !== undefined;
  const config = lookupSprintSongBoard(rows, sprint.boardId, isTest);
  if (!config.ok) throw new Error(config.error);
  if (!isTest && !['E-mail', 'Slack'].includes(config.sendSongFlag)) throw new Error('BOARD_NOT_MIGRATED');
  const payload = {};
  for (const key of ['boardId', 'sprintId', 'closedSprintName', 'sprintStart', 'sprintEnd', 'sprintGoal', 'newSprintName']) {
    payload[key] = String(sprint[key] == null ? '' : sprint[key]).trim();
  }
  if (!/^\d+$/.test(payload.sprintId) || !payload.closedSprintName) throw new Error('BAD_SPRINT');
  const start = Date.parse(payload.sprintStart), end = Date.parse(payload.sprintEnd);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) throw new Error('BAD_SPRINT_DATES');
  for (const key of ['teamName', 'jiraProject', 'emailAddress', 'slackChannelId', 'genreSelect', 'namesInSongs', 'sendSongFlag', 'deliveryMode']) payload[key] = config[key];
  payload.configVersion = 2;
  payload.source = isTest ? 'boards-index-debug' : 'boards-index-form';
  payload.isTest = isTest;
  if (isTest) {
    if (!testOptions || !['y', 'n'].includes(testOptions.namesInSongs)) throw new Error('BAD_TEST_NAMES');
    if (!/^[a-zA-Z0-9-]{8,80}$/.test(testOptions.testRunId || '')) throw new Error('BAD_TEST_RUN_ID');
    payload.testRunId = testOptions.testRunId;
    payload.namesInSongs = testOptions.namesInSongs;
    if (Object.prototype.hasOwnProperty.call(testOptions, 'slackChannelIdOverride')) {
      if (typeof testOptions.slackChannelIdOverride !== 'string') throw new Error('BAD_TEST_SLACK_OVERRIDE');
      const override = testOptions.slackChannelIdOverride.trim();
      if (override && !/^[CG][A-Z0-9]{8,}$/.test(override)) throw new Error('BAD_TEST_SLACK_OVERRIDE');
      payload.slackChannelId = override;
    }
    payload.deliveryMode = 'none';
    payload.emailAddress = '';
  }
  payload.duplicateKey = isTest ? 'test:' + payload.testRunId : payload.boardId + ':' + payload.sprintId;
  const roster = sprintSongNameRoster_(rosterRows || [],payload.teamName,payload.boardId);
  payload.namePolicy = 'fair-roster-rotation';
  payload.allowedSongNames = roster.names;
  if (roster.restricted && !roster.names) payload.namesInSongs = 'n';
  return payload;
}

function readSprintSongRoster_(ss) {
  const sheet = ss.getSheetByName('Song Roster');
  return sheet ? sheet.getDataRange().getDisplayValues() : [];
}

function sprintSongNameRoster_(rows, teamName, boardId) {
  const headers = rows[0] || [];
  const team = headers.indexOf('Team'), name = headers.indexOf('Lyric Name');
  const aliases = headers.indexOf('Aliases'), active = headers.indexOf('Active');
  const count = headers.indexOf('Mention Count');
  const title = headers.indexOf('Title');
  const members = rows.slice(1).filter(r => r[team] === teamName && String(r[name] || '').trim());
  const eligible = members.filter(r => String(r[active] || '').toLowerCase() === 'y' &&
    (title < 0 || sprintSongCoderTitle_(r[title])));
  const seen = new Set();
  for (const r of eligible) {
    const label = String(r[name]).trim(), alias = String(r[aliases] || '').trim();
    if (/[\r\n]/.test(label + alias) || label.length > 100 || alias.length > 200 ||
        seen.has(label.toLowerCase())) throw new Error('BAD_NAME_ROSTER');
    seen.add(label.toLowerCase());
    if (count >= 0 && (!Number.isFinite(Number(r[count] || 0)) || Number(r[count] || 0) < 0))
      throw new Error('BAD_NAME_ROSTER_COUNT');
  }
  const minimum = Math.min(...eligible.map(r=>Number(r[count] || 0)));
  const selected = eligible.filter(r=>Number(r[count] || 0) === minimum);
  // Shuffle ties so an uncredited shout-out has no fixed alphabetical order.
  for (let i=selected.length-1;i>0;i--) {
    const j=Math.floor(Math.random()*(i+1)); [selected[i],selected[j]]=[selected[j],selected[i]];
  }
  return {restricted:true, names:selected.map(r => String(r[name]).trim() +
    (r[aliases] ? ' (aliases: ' + String(r[aliases]).trim() + ')' : '')).join('\n')};
}

// Missing/ambiguous titles and leadership roles need owner review, never automatic activation.
function sprintSongCoderTitle_(title) {
  const value = String(title || '').trim();
  return !/\b(manager|director|scrum|product|owner|coach|delivery|designer)\b/i.test(value) &&
    /\b(engineer|developer|programmer|QA|quality assurance|tester|SDET)\b/i.test(value);
}

// Count actual authored appearances once per production team/sprint, using the existing Kaplan mailbox.
function refreshSprintSongNameHistory() {
  syncSongNameHistory_(SpreadsheetApp.getActive());
}

// Read-only preview: no song request, email or delivery.
function previewSprintSongNameRotation() {
  const rows = readSprintSongRoster_(SpreadsheetApp.getActive());
  for (const team of [...new Set(rows.slice(1).map(r=>r[rows[0].indexOf('Team')]).filter(Boolean))])
    Logger.log('%s: %s',team,sprintSongNameRoster_(rows,team).names || '(names off: no eligible roster)');
}

function syncSongNameHistory_(ss, emails) {
  if (Session.getEffectiveUser().getEmail().toLowerCase() !== SONG_AUDIT_MAILBOX.toLowerCase())
    throw new Error('NAME_HISTORY_WRONG_MAILBOX');
  const rosterSheet = ss.getSheetByName('Song Roster');
  if (!rosterSheet) throw new Error('MISSING_SONG_ROSTER');
  let roster, columns;
  const messages = emails || fetchRecentSongEmails_(true);
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    roster = rosterSheet.getDataRange().getDisplayValues(); columns = roster[0] || [];
    for (const header of ['Team','Lyric Name','Aliases','Active','Mention Count','Last Named'])
      if (columns.indexOf(header) < 0 || columns.indexOf(header) !== columns.lastIndexOf(header))
        throw new Error('BAD_NAME_ROSTER_COLUMNS');
    const history = ss.getSheetByName('Song Name History');
    if (!history) throw new Error('MISSING_SONG_NAME_HISTORY');
    const existing = history.getDataRange().getValues();
    const expected = ['Song Key','Team','Sprint','Song Date','Member Email','Lyric Name'];
    const historyHeaders = existing[0].map(v=>String(v).trim().toLowerCase());
    if (JSON.stringify(historyHeaders.slice(0,6)) !== JSON.stringify(expected.map(v=>v.toLowerCase())) ||
        ![6,7].includes(historyHeaders.length) || historyHeaders.length===7 && historyHeaders[6]!=='gmail message id')
      throw new Error('BAD_NAME_HISTORY_COLUMNS');
    const additions = sprintSongNameHistoryRows_(roster,messages,existing.slice(1));
    if (additions.length) history.getRange(history.getLastRow()+1,1,additions.length,historyHeaders.length)
      .setValues(additions.map(r=>r.slice(0,historyHeaders.length)));
    const stats = sprintSongNameStats_(existing.slice(1).concat(additions));
    const team = columns.indexOf('Team'), name = columns.indexOf('Lyric Name');
    if (roster.length > 1) {
      const values = roster.slice(1).map(r => stats[sprintSongNameKey_(r[team],r[name])] || [0,'']);
      rosterSheet.getRange(2,columns.indexOf('Mention Count')+1,values.length,1).setValues(values.map(v=>[v[0]]));
      rosterSheet.getRange(2,columns.indexOf('Last Named')+1,values.length,1).setValues(values.map(v=>[v[1]]));
    }
    SpreadsheetApp.flush();
    Logger.log('NAME HISTORY: %s new person/song appearances.',additions.length);
    return {added:additions.length};
  } finally {lock.releaseLock();}
}

function sprintSongNameKey_(team,name) {
  return String(team || '').trim().toLowerCase()+'|'+String(name || '').trim().toLowerCase();
}

function sprintSongNameStats_(rows) {
  const seen = new Set(), stats = {};
  for (const r of rows) {
    const key = sprintSongNameKey_(r[1],r[5]), pair = String(r[0])+'|'+key;
    if (!r[0] || !r[1] || !r[5] || seen.has(pair)) continue;
    seen.add(pair);
    const date = new Date(r[3]);
    const old = stats[key] || [0,''];
    stats[key] = [old[0]+1,Number.isFinite(date.getTime()) &&
      (!old[1] || date > new Date(old[1])) ? date : old[1]];
  }
  return stats;
}

function sprintSongNameHistoryRows_(roster,messages,existing) {
  const cols = roster[0] || [], teamCol = cols.indexOf('Team'), nameCol = cols.indexOf('Lyric Name');
  const emailCol = cols.indexOf('Member Email');
  const teams = [...new Set(roster.slice(1).map(r=>String(r[teamCol] || '').trim()).filter(Boolean))];
  const seen = new Set(existing.map(r=>String(r[0])+'|'+sprintSongNameKey_(r[1],r[5])));
  const additions = [];
  for (const message of messages) {
    if (!message.subject || !/^\[SPRINT SONG\]\s+/i.test(message.subject) ||
        /^\[SPRINT SONG\]\s*TEST\b/i.test(message.subject)) continue;
    const matches = teams.filter(t=>songAuditNorm_(message.subject).startsWith('[sprint song] '+songAuditNorm_(t)+' - '));
    if (matches.length !== 1) continue;
    const team = matches[0], prefix = '[sprint song] '+songAuditNorm_(team)+' - ';
    const sprint = songAuditNorm_(message.subject).slice(prefix.length);
    if (!sprint) continue;
    const body = String(message.lyrics || '');
    const block = body.match(/===DISPLAY LYRICS===\s*([\s\S]*?)(?====[A-Z][A-Z _-]*===|$)/) ||
      body.match(/===LYRICS===\s*([\s\S]*?)(?====[A-Z][A-Z _-]*===|$)/);
    if (!block) continue;
    const words = ' '+block[1].normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim()+' ';
    const songKey = team+'|'+sprint;
    for (const r of roster.slice(1).filter(r=>r[teamCol]===team && String(r[nameCol] || '').trim())) {
      const name = String(r[nameCol]).trim();
      // Aliases identify authored variants; whole words avoid counting Ann inside Joanna.
      const variants = [name].concat(String(r[cols.indexOf('Aliases')] || '').split(/[,;|]+/)).filter(Boolean);
      const hit = variants.some(v=>{const key=v.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();return key && words.includes(' '+key+' ');});
      const pair = songKey+'|'+sprintSongNameKey_(team,name);
      if (hit && !seen.has(pair)) {
        seen.add(pair);
        additions.push([songKey,team,sprint,message.date,String(r[emailCol] || ''),name,message.messageId || '']);
      }
    }
  }
  return additions;
}

function sprintSongRelayOwner_(props) {
  const owner = String(props.getProperty('SONG_FORM_OWNER_EMAIL') || '').trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(owner)) throw new Error('SONG_FORM_OWNER_EMAIL_NOT_CONFIGURED');
  return owner;
}

function assertSprintSongRelayOwner_(props) {
  const current = String(Session.getEffectiveUser().getEmail() || '').trim().toLowerCase();
  if (current !== sprintSongRelayOwner_(props)) throw new Error('Run this operation as the configured relay owner.');
}

// Human-run credential setup. The owner copies the token from Project Settings into Jira.
function configureHomeGhostSongRelay() {
  const props = PropertiesService.getScriptProperties(), key = 'SONG_FORM_TOKEN_1168';
  assertSprintSongRelayOwner_(props);
  const token = props.getProperty(key) || Utilities.getUuid().replace(/-/g,'')+Utilities.getUuid().replace(/-/g,'');
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) throw new Error('Existing board token is invalid.');
  props.setProperty(key,token);
  Logger.log('Ghost token is saved as SONG_FORM_TOKEN_1168 in Project Settings. Copy its value into the Jira Custom data token placeholder; keep the shim disabled until verified.');
}

// Run from the bound spreadsheet editor. Setup creates credentials, not song requests.
function configureSprintSongFormRelay() {
  const ss = SpreadsheetApp.getActive();
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('SONG_FORM_RELAY_ENABLED') === 'true') throw new Error('Disable the relay before setup.');
  const receiverUrl = (props.getProperty('WEBHOOK_SONG_FORM_URL') || '').trim();
  if (!/^https:\/\/[^\s?#]+$/.test(receiverUrl)) throw new Error('Configure WEBHOOK_SONG_FORM_URL before setup.');
  const responseId = props.getProperty('SONG_FORM_RESPONSE_SHEET_ID') || '1958283758';
  const response = ss.getSheets().find(s => String(s.getSheetId()) === responseId);
  const expected = ['Timestamp', 'relayToken', 'boardId', 'sprintId', 'closedSprintName', 'newSprintName', 'sprintStart', 'sprintEnd', 'sprintGoal'];
  if (!response || JSON.stringify(response.getRange(1, 1, 1, 9).getDisplayValues()[0]) !== JSON.stringify(expected)) {
    throw new Error('The production Form response columns do not match.');
  }
  const boards = ss.getSheetByName('Boards Index');
  if (!boards) throw new Error('Boards Index is missing.');
  const rows = boards.getDataRange().getDisplayValues();
  const col = rows[0].indexOf('BOARD ID');
  if (col < 0 || col !== rows[0].lastIndexOf('BOARD ID')) throw new Error('BOARD ID column is missing or duplicated.');
  const ids = rows.slice(1).map(r => String(r[col] || '').trim()).filter(Boolean);
  if (!ids.length || ids.some(id => !/^\d+$/.test(id)) || new Set(ids).size !== ids.length) {
    throw new Error('Board IDs must be present, numeric and unique.');
  }
  const triggers = ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'handleSprintSongFormSubmit');
  if (triggers.length > 1) throw new Error('Remove duplicate relay triggers before setup.');
  const ui = SpreadsheetApp.getUi();
  const input = ui.prompt('Sprint Song Relay setup', 'Paste the secret from Sprint Song Generator - Form payload v2:', ui.ButtonSet.OK_CANCEL);
  if (input.getSelectedButton() !== ui.Button.OK) return;
  const secret = input.getResponseText().trim();
  if (!secret || /[\s\r\n]/.test(secret)) throw new Error('Receiver secret is missing or invalid.');
  const settings = {WEBHOOK_SONG_FORM_URL: receiverUrl,
    WEBHOOK_SONG_FORM_SECRET: secret, SONG_FORM_RESPONSE_SHEET_ID: responseId, SONG_FORM_RELAY_ENABLED: 'false'};
  for (const id of ids) {
    const key = 'SONG_FORM_TOKEN_' + id;
    const existing = props.getProperty(key);
    if (existing && !/^[A-Za-z0-9_-]{32,256}$/.test(existing)) throw new Error('Existing board token is invalid.');
    settings[key] = existing || (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g, '');
  }
  props.setProperties(settings);
  if (!triggers.length) ScriptApp.newTrigger('handleSprintSongFormSubmit').forSpreadsheet(ss).onFormSubmit().create();
  Logger.log('Form relay configured for %s boards; it remains disabled.', ids.length);
}

// Manual controls for the existing relay; never create or replace credentials here.
function enableSprintSongFormRelay() {
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('WEBHOOK_SONG_FORM_URL') || !props.getProperty('WEBHOOK_SONG_FORM_SECRET') ||
      !props.getProperty('SONG_FORM_RESPONSE_SHEET_ID') ||
      ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'handleSprintSongFormSubmit').length !== 1) {
    throw new Error('Configure the receiver and exactly one Form-submit trigger first.');
  }
  props.setProperties({SONG_FORM_RELAY_ENABLED: 'true', SONG_FORM_LEGACY_DISABLED: 'true'});
  Logger.log('Form relay enabled.');
}

function disableSprintSongFormRelay() {
  PropertiesService.getScriptProperties().setProperties({SONG_FORM_RELAY_ENABLED: 'false'});
  Logger.log('Form relay disabled.');
}

// Install as a spreadsheet Form-submit trigger, not a Forms trigger.
function handleSprintSongFormSubmit(e, allowLegacyReplay) {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('SONG_FORM_RELAY_ENABLED') !== 'true') return {status: 'DISABLED'};
  const ss = SpreadsheetApp.getActive();
  const sprint = readSprintSongForm_(e, props, ss, allowLegacyReplay === true);
  if (sprint.eventType==='started') return handleLmiSprintStart_(sprint,e);
  if (sprint.eventType==='brief-result') return handleLmiNarrativeResult_(sprint);
  const key = sprint.boardId + ':' + sprint.sprintId;
  syncSongNameHistory_(ss);
  const lock = LockService.getScriptLock();
  let log, row, record, payload, configError;
  // ponytail: linear ledger scan is adequate for 25 teams; archive old rows if it grows slow.
  lock.waitLock(30000);
  try {
    log = ss.getSheetByName('_Song_Relay_Log');
    if (!log) {
      log = ss.insertSheet('_Song_Relay_Log');
      log.appendRow(['key', 'boardId', 'sprintId', 'responseRow', 'status', 'receivedAt', 'updatedAt', 'httpCode', 'error', 'responseSheetId']);
      log.setFrozenRows(1);
    }
    log.getRange(1, 10).setValue('responseSheetId');
    log.getRange('A:A').setNumberFormat('@'); // Keep board:sprint keys as text, not durations.
    const records = log.getDataRange().getValues();
    const index = records.findIndex((r, i) => i > 0 && String(r[1]) + ':' + String(r[2]) === key);
    const existing = index < 0 ? null : records[index];
    if (existing && existing[4] !== 'REPLAY_READY') return {status: 'DUPLICATE', previousStatus: existing[4], key: key};
    const now = new Date();
    record = [key, sprint.boardId, sprint.sprintId, e.range.getRow(), 'RECEIVED', now, now, '', '', String(e.range.getSheet().getSheetId())];
    if (existing) {
      row = index + 1;
      record[5] = existing[5];
    } else {
      row = log.getLastRow() + 1; // appendRow reparses colon keys even in a text column.
    }
    try {
      const boards = ss.getSheetByName('Boards Index');
      if (!boards) throw new Error('MISSING_BOARDS_INDEX');
      payload = buildSprintSongPayload_(boards.getDataRange().getDisplayValues(), sprint, undefined, readSprintSongRoster_(ss));
    } catch (error) {
      configError = String(error.message);
      const known = ['BAD_BOARD_ID', 'MISSING_COLUMN', 'DUPLICATE_COLUMN', 'DUPLICATE_BOARD_ID', 'NO_MATCH',
        'BAD_SEND_SONG', 'SONG_DISABLED', 'INCOMPLETE_ROW', 'BAD_EMAIL', 'BAD_SLACK_CHANNEL',
        'BAD_NAMES_FLAG', 'BOARD_NOT_MIGRATED', 'BAD_SPRINT', 'BAD_SPRINT_DATES', 'MISSING_BOARDS_INDEX'];
      if (!known.includes(configError)) configError = 'CONFIG_READ_FAILED';
    }
    record[4] = configError === 'SONG_DISABLED' ? 'SKIPPED' : configError ? 'FAILED' : 'SENDING';
    record[8] = configError || '';
    log.getRange(row, 1, 1, record.length).setValues([record]);
    SpreadsheetApp.flush(); // Persist reservation before releasing the lock or calling Jira.
  } finally {
    lock.releaseLock();
  }
  if (record[4] === 'SKIPPED') return {status: 'SKIPPED', key: key};
  if (configError) throw new Error('SONG_RELAY_' + configError);

  const url = (props.getProperty('WEBHOOK_SONG_FORM_URL') || '').trim();
  const secret = (props.getProperty('WEBHOOK_SONG_FORM_SECRET') || '').trim();
  const legacyUrl = (props.getProperty('WEBHOOK_SONG_URL') || '').trim().replace(/\/+$/, '');
  const testUrl = (props.getProperty('WEBHOOK_SONG_TEST_URL') || '').trim().replace(/\/+$/, '');
  let result;
  if (!/^https:\/\/[^\s?#]+$/.test(url) || !secret ||
      [legacyUrl, testUrl].includes(url.replace(/\/+$/, ''))) {
    result = {ok: false, code: '', safeError: 'RECEIVER_NOT_CONFIGURED'};
  } else {
    try {
      result = postSongWebhook_(url, secret, payload);
    } catch (error) {
      result = {ok: false, code: ''}; // Never expose URL-bearing transport errors.
    }
  }
  record[4] = result.ok ? 'SENT' : result.code || result.safeError ? 'FAILED' : 'UNKNOWN';
  record[6] = new Date();
  record[7] = result.code || '';
  record[8] = result.ok ? '' : result.safeError || (result.code ? 'HTTP_' + result.code : 'CHECK_JIRA_AUDIT');
  lock.waitLock(30000);
  try {
    log.getRange(row, 1, 1, record.length).setValues([record]);
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }
  Logger.log('SONG RELAY %s: %s', key, record[4]);
  if (!result.ok) throw new Error('SONG_RELAY_' + record[8]);
  return {status: 'SENT', key: key, httpCode: result.code};
}

function readSprintSongForm_(e, props, ss, allowLegacyReplay) {
  if (!e || !e.range || !e.namedValues) throw new Error('SONG_RELAY_BAD_EVENT');
  const sheet = e.range.getSheet();
  const accepted = sprintSongResponseSheetIds_(props);
  if (allowLegacyReplay === true) accepted.push(props.getProperty('SONG_FORM_LEGACY_RESPONSE_SHEET_ID'));
  if (sheet.getParent().getId() !== ss.getId() ||
      !accepted.includes(String(sheet.getSheetId())) || e.range.getRow() < 2) {
    throw new Error('SONG_RELAY_WRONG_SHEET');
  }
  const limits = {relayToken: 256, boardId: 12, sprintId: 12, closedSprintName: 200,
    newSprintName: 200, sprintStart: 80, sprintEnd: 80, sprintGoal: 10000};
  const sprint = {};
  for (const name of Object.keys(limits)) {
    const values = e.namedValues[name];
    if (!Array.isArray(values) || values.length !== 1 || typeof values[0] !== 'string' || values[0].length > limits[name]) {
      throw new Error('SONG_RELAY_BAD_FIELDS');
    }
    sprint[name] = values[0].trim();
  }
  if (!/^\d+$/.test(sprint.boardId) || !/^\d+$/.test(sprint.sprintId) || !sprint.closedSprintName) {
    throw new Error('SONG_RELAY_BAD_SPRINT');
  }
  const token = props.getProperty('SONG_FORM_TOKEN_' + sprint.boardId) || '';
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token) || sprint.relayToken !== token) throw new Error('SONG_RELAY_BAD_TOKEN');
  const start = Date.parse(sprint.sprintStart), end = Date.parse(sprint.sprintEnd);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start || /[\r\n]/.test(sprint.closedSprintName)) {
    throw new Error('SONG_RELAY_BAD_SPRINT_DATES');
  }
  delete sprint.relayToken;
  const eventValues=e.namedValues.eventType;
  if (eventValues && (!Array.isArray(eventValues) || eventValues.length!==1 || typeof eventValues[0]!=='string')) throw new Error('SONG_RELAY_BAD_EVENT_TYPE');
  sprint.eventType=eventValues && eventValues[0].trim() || 'closed';
  if (!['closed','started','brief-result'].includes(sprint.eventType)) throw new Error('SONG_RELAY_BAD_EVENT_TYPE');
  if (allowLegacyReplay && sprint.eventType!=='closed') throw new Error('SONG_RELAY_REPLAY_CLOSE_ONLY');
  return sprint;
}

// Run from the bound sheet only after confirming that Jira did not accept this key.
function replaySprintSongRelay() {
  const ui = SpreadsheetApp.getUi();
  const answer = ui.prompt('Replay a failed song relay',
    'Enter its row number in _Song_Relay_Log. First confirm the key is absent from Jira audit logs.', ui.ButtonSet.OK_CANCEL);
  if (answer.getSelectedButton() !== ui.Button.OK) return;
  const row = Number(answer.getResponseText());
  const ss = SpreadsheetApp.getActive(), props = PropertiesService.getScriptProperties();
  const log = ss.getSheetByName('_Song_Relay_Log');
  if (!Number.isInteger(row) || row < 2 || !log || row > log.getLastRow()) throw new Error('SONG_RELAY_BAD_LOG_ROW');
  const record = log.getRange(row, 1, 1, 10).getValues()[0];
  if (!['FAILED', 'UNKNOWN', 'SENDING'].includes(record[4]) ||
      record[4] === 'SENDING' && Date.now() - new Date(record[6]).getTime() < 600000) {
    throw new Error('SONG_RELAY_NOT_REPLAYABLE');
  }
  const sourceId = String(record[9] || props.getProperty('SONG_FORM_LEGACY_RESPONSE_SHEET_ID') || props.getProperty('SONG_FORM_RESPONSE_SHEET_ID'));
  const sheet = ss.getSheets().find(s => String(s.getSheetId()) === sourceId);
  if (!sheet || !Number.isInteger(Number(record[3])) || record[3] < 2 || record[3] > sheet.getLastRow()) throw new Error('SONG_RELAY_BAD_RESPONSE_ROW');
  const titles = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getDisplayValues()[0];
  const values = sheet.getRange(Number(record[3]), 1, 1, titles.length).getDisplayValues()[0];
  const namedValues = {};
  titles.forEach((title, i) => {
    if (Object.prototype.hasOwnProperty.call(namedValues, title)) throw new Error('SONG_RELAY_DUPLICATE_FORM_COLUMN');
    namedValues[title] = [values[i]];
  });
  const event = {range: sheet.getRange(Number(record[3]), 1, 1, titles.length), namedValues: namedValues};
  const sprint = readSprintSongForm_(event, props, ss, true);
  if (String(record[1]) !== sprint.boardId || String(record[2]) !== sprint.sprintId) throw new Error('SONG_RELAY_KEY_MISMATCH');
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const latest = log.getRange(row, 1, 1, 9).getValues()[0];
    if (String(latest[4]) !== String(record[4]) || String(latest[6]) !== String(record[6])) throw new Error('SONG_RELAY_STATE_CHANGED');
    log.getRange(row, 5).setValue('REPLAY_READY');
    SpreadsheetApp.flush();
  } finally { lock.releaseLock(); }
  return handleSprintSongFormSubmit(event, true);
}

// Additional response tabs are used only during a Form migration.
function sprintSongResponseSheetIds_(props) {
  const primary = props.getProperty('SONG_FORM_RESPONSE_SHEET_ID') || '';
  return [primary].concat((props.getProperty('SONG_FORM_ADDITIONAL_RESPONSE_SHEET_IDS') || '').split(','))
    .map(id => id.trim()).filter(id => /^\d+$/.test(id));
}

// Run as Kaplan; preserves receiver credentials, board tokens, enabled state and ledger.
function prepareKaplanSprintSongRelay() {
  const ss = SpreadsheetApp.getActive(), props = PropertiesService.getScriptProperties();
  assertSprintSongRelayOwner_(props);
  const nextId = '1958283758';
  const response = ss.getSheets().find(s => String(s.getSheetId()) === nextId);
  const expected = ['Timestamp', 'relayToken', 'boardId', 'sprintId', 'closedSprintName', 'newSprintName', 'sprintStart', 'sprintEnd', 'sprintGoal'];
  if (!response || JSON.stringify(response.getRange(1, 1, 1, 9).getDisplayValues()[0]) !== JSON.stringify(expected)) {
    throw new Error('Kaplan Form response columns do not match.');
  }
  const legacyId = props.getProperty('SONG_FORM_RESPONSE_SHEET_ID');
  if (legacyId !== '1664690944' || !props.getProperty('WEBHOOK_SONG_FORM_URL') || !props.getProperty('WEBHOOK_SONG_FORM_SECRET')) {
    throw new Error('Existing relay configuration must be intact before migration.');
  }
  const triggers = ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'handleSprintSongFormSubmit');
  if (triggers.length > 1) throw new Error('Remove duplicate Kaplan relay triggers first.');
  props.setProperties({SONG_FORM_ADDITIONAL_RESPONSE_SHEET_IDS: nextId, SONG_FORM_LEGACY_RESPONSE_SHEET_ID: legacyId});
  if (!triggers.length) ScriptApp.newTrigger('handleSprintSongFormSubmit').forSpreadsheet(ss).onFormSubmit().create();
  Logger.log('Kaplan trigger ready; old and new response tabs accepted. Receiver and tokens unchanged.');
}

// Run after every shim has been saved and verified on the Kaplan Form.
function finalizeKaplanSprintSongRelay() {
  const ss = SpreadsheetApp.getActive(), props = PropertiesService.getScriptProperties();
  assertSprintSongRelayOwner_(props);
  const response = ss.getSheets().find(s => String(s.getSheetId()) === '1958283758');
  const expected = ['Timestamp', 'relayToken', 'boardId', 'sprintId', 'closedSprintName', 'newSprintName', 'sprintStart', 'sprintEnd', 'sprintGoal'];
  if (!response || JSON.stringify(response.getRange(1, 1, 1, 9).getDisplayValues()[0]) !== JSON.stringify(expected) ||
      props.getProperty('SONG_FORM_LEGACY_RESPONSE_SHEET_ID') !== '1664690944' ||
      props.getProperty('SONG_FORM_RELAY_ENABLED') !== 'true' ||
      ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'handleSprintSongFormSubmit').length !== 1) {
    throw new Error('Kaplan response, enabled relay and exactly one owned trigger must be ready.');
  }
  const log = ss.getSheetByName('_Song_Relay_Log');
  if (log && log.getDataRange().getValues().slice(1).some(r => ['RECEIVED', 'SENDING', 'REPLAY_READY'].includes(r[4]))) {
    throw new Error('A relay request is still pending; reconcile before finalizing.');
  }
  props.setProperties({SONG_FORM_RESPONSE_SHEET_ID: '1958283758', SONG_FORM_ADDITIONAL_RESPONSE_SHEET_IDS: ''});
  Logger.log('Kaplan response tab is primary and exclusive for live events. Historical replay and all credentials preserved.');
}
