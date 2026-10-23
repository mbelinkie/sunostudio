/* Manual tests. Dates/settings come from Jira and Boards Index. */
// Configure SONG_TEST_DELIVERY_SLACK_CHANNEL in Script Properties before running a live test.
function sprintSongTestDeliverySlackChannel_(props) {
  const channel = String(props.getProperty('SONG_TEST_DELIVERY_SLACK_CHANNEL') || '').trim();
  if (!/^C[A-Z0-9]{8,}$/.test(channel)) throw new Error('SONG_TEST_DELIVERY_SLACK_CHANNEL is missing or invalid.');
  return channel;
}

function debugSprintSong() {
  const options = {
    team: 'Sim',
    sprintId: '26013',
    namesInSongs: 'n',
    dryRun: true
  };
  return runSprintSongDebug_(options);
}

// Project Settings becomes read-only above 50 properties; prompts keep secrets out of source.
function configureSprintSongDebug() {
  const ui = SpreadsheetApp.getUi();
  const urlInput = ui.prompt('Song test setup', 'Separate Jira test webhook URL:', ui.ButtonSet.OK_CANCEL);
  if (urlInput.getSelectedButton() !== ui.Button.OK) return;
  const url = urlInput.getResponseText().trim();
  const props = PropertiesService.getScriptProperties();
  if (!/^https:\/\/[^\s?#]+$/.test(url) || url.replace(/\/+$/, '') ===
      (props.getProperty('WEBHOOK_SONG_URL') || '').trim().replace(/\/+$/, '')) {
    throw new Error('Use the separate HTTPS test webhook URL.');
  }
  const secretInput = ui.prompt('Song test setup', 'Test webhook secret:', ui.ButtonSet.OK_CANCEL);
  if (secretInput.getSelectedButton() !== ui.Button.OK) return;
  const secret = secretInput.getResponseText().trim();
  if (!secret || /\s/.test(secret)) throw new Error('Test webhook secret is missing or invalid.');
  props.setProperties({WEBHOOK_SONG_TEST_URL: url, WEBHOOK_SONG_TEST_SECRET: secret});
  Logger.log('Song test webhook configured.');
}

function runSprintSongDebug_(options) {
  if (!options || typeof options.dryRun !== 'boolean') throw new Error('Set dryRun to true or false.');
  if (!['y', 'n'].includes(options.namesInSongs)) throw new Error('Set namesInSongs to y or n.');
  if (!/^\d+$/.test(String(options.sprintId || ''))) throw new Error('Use the numeric Jira sprint ID.');
  const props = PropertiesService.getScriptProperties();
  const sheet = SpreadsheetApp.getActive().getSheetByName('Boards Index');
  if (!sheet) throw new Error('Boards Index is missing.');
  const rows = sheet.getDataRange().getDisplayValues();
  const headers = rows[0].map(h => String(h).trim());
  const teamCol = headers.indexOf('TEAM'), boardCol = headers.indexOf('BOARD ID');
  if (teamCol < 0 || boardCol < 0) throw new Error('TEAM / BOARD ID columns missing.');
  const team = String(options.team || '').trim().toLowerCase();
  const matches = rows.slice(1).filter(r => String(r[teamCol]).trim().toLowerCase() === team ||
    (/^\d+$/.test(team) && String(r[boardCol]).trim() === team));
  if (!team || matches.length !== 1) throw new Error('Team or board ID must match exactly one Boards Index row.');
  const boardId = String(matches[0][boardCol]).trim();
  const config = lookupSprintSongBoard(rows, boardId, true);
  if (!config.ok) throw new Error(config.error);
  const testStartedAt = new Date().toISOString();
  const sprint = diagnosticJira_('/rest/agile/1.0/sprint/' + options.sprintId);
  if (String(sprint.id) !== String(options.sprintId) || !['active', 'closed'].includes(sprint.state)) {
    throw new Error('Use an active or completed sprint.');
  }
  if (String(sprint.originBoardId) !== boardId && !diagnosticAgilePages_(
      '/rest/agile/1.0/board/' + boardId + '/sprint?state=active,closed'
    ).some(s => String(s.id) === String(sprint.id))) {
    throw new Error('That sprint does not belong to the selected board.');
  }
  const end = sprint.state === 'closed' ? (sprint.completeDate || sprint.endDate) : testStartedAt;
  const meta = {boardId: boardId, sprintId: String(sprint.id), closedSprintName: sprint.name,
    sprintStart: sprint.startDate, sprintEnd: end, sprintGoal: sprint.goal || ''};
  const testOptions = {namesInSongs: options.namesInSongs, testRunId: Utilities.getUuid()};
  const payload = buildSprintSongPayload_(rows, meta, testOptions,
    readSprintSongRoster_(SpreadsheetApp.getActive()));
  payload.deliveryMode = 'slack';
  payload.deliverySlackChannelId = sprintSongTestDeliverySlackChannel_(props);
  payload.testStartedAt = testStartedAt;
  payload.sprintState = sprint.state;
  Logger.log('SONG TEST %s: %s', payload.testRunId, JSON.stringify(payload, null, 2));
  if (options.dryRun) return {status: 'PREVIEW', payload: payload};
  return sendSprintSongDebug_(payload);
}

function sendSprintSongDebug_(payload) {
  const props = PropertiesService.getScriptProperties();
  const channel = sprintSongTestDeliverySlackChannel_(props);
  if (payload.source !== 'boards-index-debug' || payload.isTest !== true ||
      payload.configVersion !== 2 || payload.deliveryMode !== 'slack' || payload.emailAddress !== '' ||
      payload.deliverySlackChannelId !== channel ||
      payload.duplicateKey !== 'test:' + payload.testRunId) throw new Error('Invalid test payload.');
  const url = (props.getProperty('WEBHOOK_SONG_TEST_URL') || '').trim();
  const secret = (props.getProperty('WEBHOOK_SONG_TEST_SECRET') || '').trim();
  if (!url || !secret) throw new Error('Configure the separate test rule: WEBHOOK_SONG_TEST_URL / WEBHOOK_SONG_TEST_SECRET.');
  if (!/^https:\/\/[^\s?#]+$/.test(url)) throw new Error('Test webhook must use HTTPS.');
  const productionUrl = (props.getProperty('WEBHOOK_SONG_URL') || '').trim().replace(/\/+$/, '');
  if (url.replace(/\/+$/, '') === productionUrl) throw new Error('Test webhook must differ from the production webhook.');
  const result = postSongWebhook_(url, secret, payload);
  // Do not log response text: exceptions can include credential-bearing URLs.
  Logger.log('SONG TEST %s: %s (HTTP %s)', payload.testRunId, result.ok ? 'ACCEPTED' : 'FAILED', result.code || 'unavailable');
  if (!result.ok) throw new Error('Test webhook failed. Check the test rule audit log; no automatic retry.');
  return {status: 'ACCEPTED', testRunId: payload.testRunId, httpCode: result.code};
}
