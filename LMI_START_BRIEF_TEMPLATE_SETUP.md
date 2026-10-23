# LMI start-brief template setup

This package is a source template. Configure the bound Apps Script project
before enabling its relay; do not commit production values, recipient lists,
or webhook URLs.

Set these Script Properties:

- `LMI_START_RECIPIENTS`: comma-separated delivery recipients.
- `LMI_START_BOARD_URL`: board link included in the rendered brief.
- `LMI_START_FORM_URL`: Form endpoint used by the private preview submission.
- `WEBHOOK_LMI_START_URL` and `WEBHOOK_LMI_START_SECRET`: callback receiver
  credentials.
- `SONG_FORM_TOKEN_1018`: the board's authenticated relay token.

Use `inspectLmiStartRelayConfiguration` to confirm only configuration presence.
Run `runLmiStartRelayPreview` before setting `LMI_START_MODE` to `production`.
The preview does not send email.
