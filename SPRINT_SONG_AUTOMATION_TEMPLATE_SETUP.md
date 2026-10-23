# Sprint Song automation template setup

The JSON exports in this directory are disabled templates. They contain only
placeholder addresses and a placeholder Slack destination; replace those in a
private Jira automation configuration before enabling any rule.

Set these Script Properties in the bound spreadsheet project. Keep their
values out of source control and out of rule exports:

- `SONG_FORM_OWNER_EMAIL`: the operator permitted to run migration and token
  setup functions.
- `WEBHOOK_SONG_FORM_URL` and `WEBHOOK_SONG_FORM_SECRET`: the production
  form-relay receiver. Set the URL before running `configureSprintSongFormRelay`.
- `WEBHOOK_SONG_TEST_URL` and `WEBHOOK_SONG_TEST_SECRET`: an isolated test
  receiver, distinct from production.
- `SONG_TEST_DELIVERY_SLACK_CHANNEL`: an isolated test Slack channel. Use the
  same value in the imported debug rule in place of `C00000000`.

Run `configureSprintSongFormRelay` while the relay is disabled; it stores the
receiver secret and per-board tokens, then creates at most one submit trigger.
Review the imported Jira rules, replace `owner@example.invalid` and
`noreply@example.invalid`, and enable them only after a dry-run succeeds.
