# Suno Studio

Suno Studio is a local-first app for creating songs from your lyrics and making
timed lyric videos. Your approvals and permanent copy of each finished MP4
stay on your computer. Song and optional artwork requests go to the provider
you choose. Local video rendering is the default; optional AWS ECS Fargate
rendering can encode several videos at once.

> Suno Studio is an independent project. It is not affiliated with or endorsed
> by Suno, OpenAI, Google, or any song-generation provider.

## Download and start

1. Open [GitHub Releases](https://github.com/mbelinkie/sunostudio/releases/latest).
   Download `SunoStudio-6.0.1-macOS.zip` for the Mac app, or
   `SunoStudio-6.0.1-source.zip` for the source and Windows launcher.
2. Install [Python 3.9 or newer](https://www.python.org/downloads/) and an
   [FFmpeg build](https://ffmpeg.org/download.html) with `subtitles` and
   `drawtext` support. Both `ffmpeg` and `ffprobe` must be available. The
   [setup guide](SETUP.md) has checks for macOS and Windows.
3. Extract the ZIP. On macOS, move **Suno Studio 6.0.1.app** to Applications
   and double-click it. On Windows, extract the full source ZIP folder
   and double-click **Start Suno Studio.bat** inside it. Keep the launcher
   beside `suno_studio.py`.
4. Open <http://127.0.0.1:8765> if your browser does not open automatically.
   Choose **Getting started** for an in-app checklist, service explanations,
   and per-video cost examples. In Settings, choose a song provider and paste
   its API key. Use **Create a Song** with delivery set to **None** for a first
   run.

The macOS app archive is not signed or notarized. If Gatekeeper blocks it,
control-click the app, choose **Open**, and confirm the prompt. Both packages
require Python 3.9 or newer and FFmpeg; the macOS app bundle does not include
those runtimes.

The local app needs no Python packages or cloud account. Gmail, OpenAI, AWS,
and Slack are optional. See [SETUP.md](SETUP.md) for optional services and
troubleshooting.

## Creation and delivery

- Create a song from a title, style, and lyrics, then review the generated
  song, artwork, and video before approval.
- Keep the finished MP4 locally, send it to a Slack channel, or email a private
  download link that expires after three days. Delivery happens only after
  video approval. Fix delivery details and retry a failed send without
  regenerating the song or video.
- Gmail intake is optional and review-first. Email requests can include
  `Delivery: Slack`, `Delivery: Email`, or `Delivery: None`, plus `Slack
  Channel ID: C…` or `Recipient: name@example.com` as needed. An omitted
  delivery choice means **None**; a Slack channel is never guessed.
- The explicit bug-report form previews the limited report before submission.
  It sends the app version, platform, stage, and a scrubbed error summary to
  the maintainer's Sentry project and returns a reference ID. It does not
  attach lyrics, media, credentials, or automatic telemetry.

## Optional AWS rendering

Local rendering works without an AWS account. If you opt in, setup creates
resources in your AWS account and uses your AWS CLI SSO profile; Suno Studio
does not store AWS access keys. AWS rendering is selected explicitly in
Settings after setup, and local rendering remains the default.

The fastest measured render task uses 8 vCPU and 16 GiB by default. Settings can choose
2/4, 4/8, or 8/16 vCPU/GiB for future cloud renders without rebuilding the
image. Up to 25 simultaneous default-size renders need at least 200 available
Fargate vCPUs. For one 178-second sample, the 8-vCPU task finished in 1:56 and
3:05 across two runs; use that range as a measurement, not a promise. At
published Linux/x86 rates in us-east-1, those wall times imply roughly
$0.013–$0.020 in render compute per video; actual billing and costs vary.
S3 storage and requests, ECR image storage, CodeBuild, CloudWatch
Logs, and network transfer can add charges. Song and artwork providers charge
separately. See [SETUP.md](SETUP.md) for setup, retention, and cleanup details.

## Lyrics and diagnostics

The default section aligner treats authored lyric line breaks as the source of
truth and uses timed provider words for timestamps. The legacy aligner remains
available, as does the optional local stable-ts hybrid mode.

```bash
python3 -m unittest -v test_lyric_alignment.py test_app_reliability.py
python3 subs_doctor.py /path/to/song.words.json --no-frames
```

See [lyrics-parsing.md](lyrics-parsing.md) for alignment details. To diagnose
video tools, macOS users can run **Diagnose Video.command**; Windows users can
run `Check Video Tools.ps1` in PowerShell. The Windows check verifies that
FFmpeg and ffprobe are available and that required filters are listed. A
successful check does not guarantee every codec, font, or render will work.

For optional local stable-ts alignment on macOS, run
**Install Local Lyric Alignment.command**, then choose **Local stable-ts
hybrid** in Settings.

## Data and security

Settings, credentials, and job state are stored under `~/.suno_studio/` on
macOS/Linux and `%USERPROFILE%\.suno_studio\` on Windows. The files are local
to your account; do not commit, share, or place them in a public issue. Gmail
uses a Google app password, not your regular account password. AWS credentials
come from your AWS CLI SSO profile; see [SECURITY.md](SECURITY.md) for
reporting guidance.

## License

Suno Studio is released under the [MIT License](LICENSE).
