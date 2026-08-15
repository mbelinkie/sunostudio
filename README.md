# Suno Studio

Suno Studio is a local macOS-friendly web app for generating songs from
authored lyrics and rendering timed lyric videos. It runs on Python's standard
library: start it locally, enter the credentials for a supported generation
provider in its Settings screen, and keep the credentials in your local config
rather than the repository.

> This is an independent project. It is not affiliated with or endorsed by
> Suno, OpenAI, Google, or any generation-provider API.

## What it does

- generates custom-lyric or instrumental tracks through configured providers;
- can read song requests from a Gmail label in review-first mode;
- fetches/caches word timings and maps them back to the authored lyric lines;
- renders karaoke-style lyric videos with ffmpeg;
- includes subtitle and video diagnostic tools.

## Quick start

Requirements: macOS or another system with Python 3.9+.

```bash
python3 suno_studio.py
```

Then open <http://127.0.0.1:8765>. API keys, mail credentials, output paths,
and job state are stored outside the checkout in `~/.suno_studio/` and are not
part of this repository.

On macOS, `Start Suno Studio.command` can be double-clicked instead. For lyric
video rendering, install an ffmpeg build with the `subtitles` and `drawtext`
filters, then use **Diagnose Video.command** to verify it.

## Lyric alignment

The default section aligner treats authored lyric line breaks as the source of
truth and uses timed provider words only for timestamps. It retains a legacy
baseline and has an optional local stable-ts hybrid mode for bounded repairs.

```bash
python3 -m unittest -v test_lyric_alignment.py test_app_reliability.py
python3 subs_doctor.py /path/to/song.words.json --no-frames
```

See [lyrics-parsing.md](lyrics-parsing.md) for the alignment design and
diagnostics.

## Optional local stable-ts alignment

`stable_ts_hybrid.py` is intentionally separate from the standard-library app.
On the supported macOS setup, double-click `Install Local Lyric Alignment.command`
to create its isolated runtime, then select **Local stable-ts hybrid** in the
app's Settings.

## Repository policy

Only source, safe documentation, and tests are versioned. The following are
intentionally excluded: generated applications and zip files, media and
artwork, subtitle diagnostics, sample songs, local configuration, historical
backups, and organization-specific integration material.

No open-source license has been selected yet. Until one is added, the code is
shared publicly for viewing but no permission to reuse it is granted.

## Security

Please do not file credentials or private media in issues. See
[SECURITY.md](SECURITY.md) for reporting guidance.
