# Security policy

Do not include API keys, passwords, access tokens, generated media, or private
song requests in public issues.

For a suspected vulnerability, use GitHub's private security advisory flow for
this repository when available, or contact the repository owner privately with
a minimal reproduction and impact summary.

The in-app bug-report form sends only the app version, platform, stage, and a
scrubbed error summary after you review and submit it. It creates a Sentry
issue and returns a reference ID; it does not attach lyrics, media,
credentials, or automatic telemetry. For other reports, use the private
contact route above.

The app stores provider credentials in `~/.suno_studio/config.json` on
macOS/Linux and `%USERPROFILE%\.suno_studio\config.json` on Windows. Treat
that file as sensitive and never commit or share it. AWS access credentials
come from your AWS CLI SSO profile and are not stored by Suno Studio. AWS setup
does save resource identifiers and the private-link signing secret in the
local config. If you configure Slack delivery, the Slack token is stored in
AWS Secrets Manager for the delivery task. Do not create AWS access keys or
share the local config file.
