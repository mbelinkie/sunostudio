# First run and optional services

## Release downloads

Download `SunoStudio-6.0-macOS.zip` or
`SunoStudio-6.0-source.zip` from the [GitHub Releases
page](https://github.com/mbelinkie/sunostudio/releases/latest). Extract the
whole archive before launching. The macOS ZIP contains the `.app` and this
guide beside it; the source ZIP contains the `.bat` launcher beside
`suno_studio.py`.

Maintainers can rebuild both archives from the repository folder with
`python3 build_release.py`. The script checks that the requested version
matches `APP_VERSION` and writes the two ZIPs under `dist/`.

## Make your first song locally

1. Open [GitHub Releases](https://github.com/mbelinkie/sunostudio/releases/latest).
   Download `SunoStudio-6.0-macOS.zip` for the Mac app or
   `SunoStudio-6.0-source.zip` for the source and Windows launcher.
   Extract the full ZIP. Run the Windows launcher from inside its extracted
   folder; it must stay beside `suno_studio.py`.
2. Install [Python 3.9 or later](https://www.python.org/downloads/). Suno
   Studio's local app uses the Python standard library; no `pip install` is
   needed. Install [FFmpeg](https://ffmpeg.org/download.html) with `subtitles`
   and `drawtext` support before making a video; see **Video tools** below.
3. Start the app:

   - macOS: move `Suno Studio 6.0.app` to Applications and double-click it. The
     app ZIP is not signed or notarized; if Gatekeeper blocks it, control-click
     the app, choose **Open**, and confirm. The app bundle requires Python and
     FFmpeg installed on the Mac.
   - Windows: double-click `Start Suno Studio.bat`.
   - Or run `python3 suno_studio.py` on macOS/Linux, or `py -3
     suno_studio.py` in Windows PowerShell.

   Open <http://127.0.0.1:8765> if it does not open automatically.
4. In Settings, choose a supported song provider and enter its API key. This
   is the only third-party key needed to generate songs. The title, style, and
   lyrics are sent to that provider to make the song. Provider accounts may
   charge for generation.
5. Use the manual form to enter a title, style, and lyrics. Delivery defaults
   to **None**, so the song and video can be created without Gmail, Slack, or
   AWS configuration.
6. Review and approve the song, artwork, and video. The completed MP4 is saved
   on your computer.

## Video tools

Install `ffmpeg` and `ffprobe` and make sure both can be run from a terminal.
The FFmpeg build needs the `drawtext`, `subtitles`, `scale`, and `overlay`
filters. Subtitle rendering also needs a usable font and FFmpeg built with its
subtitle/font support.

- macOS: run `Diagnose Video.command` for an actual filter/render check.
- Windows: open PowerShell in the app folder and run
  `./Check Video Tools.ps1`. This checks that FFmpeg and ffprobe are on PATH
  and that the needed filters are listed. It does not test every font or
  encoder; a real render is the final check.

On Windows, install a current FFmpeg distribution that includes libass and
drawtext, add its `bin` folder to your user or system `PATH`, then open a new
PowerShell window before running the check. The Windows launcher and checker
are provided as setup guidance; Windows rendering has not been validated on a
clean Windows installation.

## Optional services

| Service | Needed for | Setup |
| --- | --- | --- |
| Song provider | Song generation | Enter the provider API key in local Settings. |
| OpenAI | AI-generated artwork | Enter an OpenAI API key in Settings and choose AI artwork. Local artwork remains available without it. |
| Gmail | Reading request emails or email delivery | Configure a Gmail address and Google app password in Settings. Use an app password, not your regular password. Email intake is optional; private-link email delivery also needs AWS setup. |
| Slack | Slack delivery | Choose Slack and provide a channel ID such as `C0123456789`. AWS setup can store the Slack token in Secrets Manager for the delivery task. |
| AWS | Parallel cloud video rendering, Slack delivery, and private email links | Configure an AWS CLI SSO profile, then run `setup_aws.py`. This is optional; local rendering stays the default. |

Email intake accepts the following fields in either supported request layout:

```text
Delivery: Slack | Email | None
Slack Channel ID: C0123456789
Recipient: name@example.com
```

In the section-block layout, use `===DELIVERY===`, `===SLACK CHANNEL ID===`,
and `===RECIPIENT===`, with each value on the line below its marker.

The delivery choice defaults to None when omitted. Slack delivery requires an
explicit channel ID. Email is sent through the configured Gmail account after
video approval; Slack receives the approved MP4 directly. For emailed videos,
the app sends a private download link that expires after three days. If
delivery details are missing or sending fails, correct the details and retry
delivery without making the video again.

## AWS Fargate setup, cost, retention, and cleanup

Cloud rendering is optional and billed to your AWS account. Install AWS CLI
v2, use an AWS IAM Identity Center (SSO) profile with permission to provision
the resources below, and run these commands from the repository folder. The
SSO setup prompts for your organization's start URL, SSO region, account, and
role:

If you downloaded the macOS app ZIP, also download and extract
`SunoStudio-6.0-source.zip` from the same GitHub release for the AWS
setup scripts. On macOS, run the commands from Terminal in that extracted
folder. After creating its `.venv` and completing AWS setup, launch the app
with that folder's `Start Suno Studio.command`; it uses the virtual environment
with AWS support installed. Quit the standalone `.app` first; otherwise the
source launcher opens its already-running window. The standalone `.app` does
not use this `.venv`.

```bash
aws configure sso --profile suno-studio
aws sso login --profile suno-studio
aws sts get-caller-identity --profile suno-studio
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-cloud.txt
.venv/bin/python setup_aws.py --profile suno-studio --region us-east-1 --check
.venv/bin/python setup_aws.py --profile suno-studio --region us-east-1
```

On Windows, create the environment with `py -3 -m venv .venv`, then use
`.venv\Scripts\python.exe` in place of `.venv/bin/python`. The Mac and Windows
launchers automatically use `.venv` when it exists. If you start the app from
a terminal, run it through the same `.venv` Python for AWS support. The
`--check` command reads the current AWS identity, default
VPC, and Fargate quota without provisioning resources. Setup builds the worker
image in AWS CodeBuild, then creates a private S3 bucket, an ECR image
repository, ECS Fargate render and delivery task definitions, a Lambda private
link endpoint, IAM roles, a CloudWatch log group, and an outbound-only security
group in the default VPC. It stores resource IDs and the private-link signing
secret in your local app config; AWS access keys are not stored there. The
setup uses the named SSO profile you pass with `--profile`. Add `--no-slack` to
skip the Slack token prompt; otherwise the token is stored in AWS Secrets
Manager. After setup, select AWS in Settings to opt into cloud rendering.
Local rendering remains the default.

The app uses the saved AWS CLI profile after setup. That profile needs
`s3:PutObject` on `render-inputs/*`, `delivery-inputs/*`, and
`delivery-objects/*`; `s3:GetObject` on `render-inputs/*`,
`delivery-inputs/*`, `render-results/*`, and `delivery-results/*`; and
`s3:AbortMultipartUpload` on the three upload prefixes for interrupted large
uploads. It also needs `ecs:RunTask`, `ecs:ListTasks`, `ecs:DescribeTasks`, and
`ecs:StopTask` for the Suno Studio cluster, task definitions, and tasks, plus
`iam:PassRole` for the `suno-studio-ecs-execution`, `suno-studio-render`, and
`suno-studio-delivery` roles. For a tightly scoped SSO policy, include both
the cluster ARN and
`arn:aws:ecs:<region>:<account>:container-instance/suno-studio/*` in the
`ecs:ListTasks` resource list; AWS checks both for this call. The worker's task
role reads the Slack token from Secrets Manager; the local app does not need
that permission.
Email links are signed locally and handled by the provisioned Lambda, so the
local app does not call the Lambda API. If Slack was skipped during setup,
rerun setup and enter the bot token before choosing AWS Slack delivery.

The render task uses 4 vCPU and 8 GiB; delivery uses 1 vCPU and 2 GiB. Up to 25
renders can run at once if your account has at least 100 Fargate On-Demand
vCPUs available and other AWS quotas permit. As an example, using published
Linux/x86 Fargate rates for us-east-1, five minutes at 4 vCPU and 8 GiB costs
about $0.0165 in render-task compute:

```text
(4 × $0.04048 + 8 × $0.004445) × 5/60 hours ≈ $0.0165
```

This is a compute estimate, not a per-song total. Check [AWS Fargate
pricing](https://aws.amazon.com/fargate/pricing/) for current regional rates.
S3 storage and requests, ECR image storage, the initial or updated CodeBuild
image build, CloudWatch Logs, Lambda requests, and data transfer can add
charges. Song and artwork providers charge separately. Fargate tasks run only
for render or delivery jobs; there is no always-on rendering service.

S3 objects are private and expire by lifecycle rule: render and delivery
inputs and build archives after 7 days, emailed video objects after 4 days,
and render and delivery results after 30 days. The email link expires after 3
days and redirects through Lambda to a short-lived S3 download URL. Your
approved MP4 remains on your computer. ECR images and CloudWatch logs remain
until you remove them, so they can continue to incur storage charges.

There is currently no one-command cleanup option in `setup_aws.py`. To remove
the cloud setup, first switch the app's render backend to Local, stop any
running Suno Studio ECS tasks, and download anything you want to keep from S3.
In the AWS Console, select the account and region used for setup, then remove
only the Suno Studio resources that this setup created:

1. In ECS, stop running tasks, deregister task-definition revisions for
   `suno-studio-render` and `suno-studio-delivery`, then delete the
   `suno-studio` cluster.
2. Delete the Lambda function `suno-studio-private-link`.
3. Empty and delete the S3 bucket named
   `suno-studio-ACCOUNT_ID-REGION`. This permanently deletes cloud inputs,
   results, build archives, and emailed video objects.
4. Delete the ECR repository `suno-studio` and its images, then the CodeBuild
   project `suno-studio-build`.
5. If present, delete the Secrets Manager secret
   `suno-studio/slack-token`.
6. Delete the CloudWatch log groups `/ecs/suno-studio`,
   `/aws/codebuild/suno-studio`, and `/aws/lambda/suno-studio-private-link`.
7. Delete the IAM roles `suno-studio-ecs-execution`, `suno-studio-render`,
   `suno-studio-delivery`, `suno-studio-link`, and `suno-studio-build`. Detach
   managed policies and remove inline policies first if IAM requires it.
8. Delete the EC2 security group `suno-studio-egress` only if it was created
   for Suno Studio and is not used by other resources.

Do not delete the default VPC, its subnets, or your SSO configuration. Check
the AWS Console afterward for remaining Suno Studio resources and charges.

## Local files and bug reports

Local credentials and job state are kept under `~/.suno_studio/` on macOS or
Linux, and `%USERPROFILE%\.suno_studio\` on Windows. Treat `config.json` as
private; do not copy it into the repository, cloud storage, or a public issue.

Bug reports are submitted only when you preview and press Submit. The form
sends the app version, platform, the stage you select, and a scrubbed error
summary to the maintainer's Sentry project. The DSN is embedded in the app;
`SUNO_STUDIO_SENTRY_DSN` can override it for testing or a release. The report
does not include lyrics, media, credentials, or automatic telemetry. Users do
not need a Sentry account or DSN.
