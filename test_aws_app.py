"""App-level checks for AWS dispatch recovery and explicit report sending."""

import json
import sys
import types
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

import suno_studio as app
import test_app_reliability
import setup_aws


class AwsAppTests(unittest.TestCase):
    def test_header_render_status_checks_selected_account(self):
        with mock.patch.dict(app.CONFIG, {"render_backend": "local"}):
            self.assertEqual(app.aws_signin_status(), "local")

        sts = mock.Mock()
        sts.get_caller_identity.return_value = {"Account": "123456789012"}
        session = mock.Mock()
        session.client.return_value = sts
        boto3 = types.ModuleType("boto3")
        boto3.Session = mock.Mock(return_value=session)
        botocore = types.ModuleType("botocore")
        botocore.__path__ = []
        botocore_config = types.ModuleType("botocore.config")
        botocore_config.Config = mock.Mock()
        with mock.patch.dict(app.CONFIG, {"render_backend": "aws",
                                          "aws_account_id": "123456789012"}), \
                mock.patch.object(app, "aws_ready", return_value=True), \
                mock.patch.object(app, "aws_settings", return_value={
                    "profile": "suno", "region": "us-east-1"}), \
                mock.patch.dict(sys.modules, {"boto3": boto3, "botocore": botocore,
                                              "botocore.config": botocore_config}):
            self.assertEqual(app.aws_signin_status(), "ready")
            self.assertEqual(boto3.Session.call_args.kwargs["profile_name"], "suno")
            sts.get_caller_identity.return_value = {"Account": "999999999999"}
            self.assertEqual(app.aws_signin_status(), "wrong_account")
            sts.get_caller_identity.side_effect = RuntimeError("expired")
            self.assertEqual(app.aws_signin_status(), "signin")

    def test_external_aws_update_is_used_by_running_app(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps({"aws_render_task": "render:new",
                                        "aws_delivery_task": "delivery:new"}))
            with mock.patch.object(app, "CONFIG_PATH", path), \
                    mock.patch.object(app, "CONFIG", {
                        "aws_render_task": "render:old", "aws_delivery_task": "delivery:old",
                        "aws_render_size": "economy"}):
                settings = app.aws_settings()
            self.assertEqual(settings["render_task"], "render:new")
            self.assertEqual(settings["delivery_task"], "delivery:new")
            self.assertEqual(settings["render_size"], "economy")

    def test_settings_save_preserves_externally_updated_aws_tasks(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config.json"
            path.write_text(json.dumps({"aws_render_task": "render:new",
                                        "aws_delivery_task": "delivery:new"}))
            settings = {"aws_render_task": "render:old", "aws_delivery_task": "delivery:old",
                        "video_height": 720, "aws_render_size": "economy"}
            with mock.patch.object(app, "CONFIG_DIR", root), \
                    mock.patch.object(app, "CONFIG_PATH", path):
                app.save_config(settings)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["aws_render_task"], "render:new")
            self.assertEqual(saved["aws_delivery_task"], "delivery:new")
            self.assertEqual(saved["video_height"], 720)
            self.assertEqual(saved["aws_render_size"], "economy")

    def test_in_app_setup_requires_checked_identity_for_exact_profile_and_region(self):
        with mock.patch.dict(app.AWS_SETUP, {"status": "idle", "checked": None,
                                            "lines": []}), \
                mock.patch.object(app.threading, "Thread") as worker:
            with self.assertRaisesRegex(ValueError, "Check this AWS account"):
                app.start_aws_setup("setup", "my-profile", "us-east-1")
            worker.assert_not_called()
            app.AWS_SETUP["checked"] = ("my-profile", "us-east-1", "123456789012")
            with self.assertRaisesRegex(ValueError, "Check this AWS account"):
                app.start_aws_setup("setup", "my-profile", "us-west-2")
            app.start_aws_setup("setup", "my-profile", "us-east-1")
            self.assertEqual(worker.call_args.kwargs["args"],
                             ("setup", "my-profile", "us-east-1", "123456789012"))
            snapshot = app.aws_setup_snapshot()
            self.assertEqual(snapshot["account"], "123456789012")
            self.assertEqual(snapshot["checked_profile"], "my-profile")

    def test_in_app_setup_can_pass_slack_token_without_prompt(self):
        secret = mock.Mock()
        secret.describe_secret.side_effect = type("Missing", (Exception,), {
            "response": {"Error": {"Code": "ResourceNotFoundException"}}})()
        secret.create_secret.return_value = {"ARN": "arn:secret"}
        session = mock.Mock()
        session.client.return_value = secret
        with mock.patch.object(setup_aws, "getpass", side_effect=AssertionError("prompted")):
            arn = setup_aws._secret(session, "suno-studio/slack-token", False, "xoxb-test")
        self.assertEqual(arn, "arn:secret")
        self.assertEqual(secret.create_secret.call_args.kwargs["SecretString"], "xoxb-test")

    def test_in_app_account_check_uses_existing_provisioner_without_creating(self):
        process = mock.Mock(stdout=iter([
            "AWS account 123456789012; region us-east-1; default VPC vpc-1; subnets subnet-1\n",
            "Fargate On-Demand vCPU quota: 140\n"]))
        process.wait.return_value = 0
        with mock.patch.dict(app.AWS_SETUP, {"status": "checking", "checked": None,
                                            "lines": []}), \
                mock.patch.object(app, "install_aws_support"), \
                mock.patch.object(app, "aws_support_python", return_value=Path("/tmp/python")), \
                mock.patch.object(app.subprocess, "Popen", return_value=process) as launched:
            app.run_aws_setup("check", "my-profile", "us-east-1")
            command = launched.call_args.args[0]
            self.assertIn("--check", command)
            self.assertEqual(command[-3:], ["--profile", "my-profile", "--check"])
            self.assertEqual(app.AWS_SETUP["checked"],
                             ("my-profile", "us-east-1", "123456789012"))
            self.assertEqual(app.AWS_SETUP["status"], "checked")

    def test_render_size_settings_reach_aws_dispatch_without_rebuild(self):
        with mock.patch.dict(app.CONFIG, {"aws_render_size": "large"}), \
                mock.patch.object(app, "save_config"):
            result = test_app_reliability.post_json("/api/config", {"aws_render_size": "economy"})
            self.assertTrue(result["ok"])
            self.assertEqual(app.aws_settings()["render_size"], "economy")
            test_app_reliability.post_json("/api/config", {"aws_render_size": "invalid"})
            self.assertEqual(app.aws_settings()["render_size"], "economy")

    def test_aws_check_does_not_create_security_group(self):
        class EC2:
            def describe_vpcs(self, **_kwargs):
                return {"Vpcs": [{"VpcId": "vpc-1"}]}

            def describe_subnets(self, **_kwargs):
                return {"Subnets": [{"SubnetId": "subnet-1"}]}

            def describe_security_groups(self, **_kwargs):
                return {"SecurityGroups": []}

            def create_security_group(self, **_kwargs):
                raise AssertionError("read-only check created a security group")

        session = mock.Mock()
        session.client.return_value = EC2()
        self.assertEqual(setup_aws._network(session, create=False),
                         ("vpc-1", ["subnet-1"], ""))

    def test_retry_video_uses_selected_song_and_art_without_song_request(self):
        with TemporaryDirectory() as root:
            image = Path(root) / "chosen.png"
            image.write_bytes(b"art")
            job = {"pipeline": True, "status": "error", "stage": "video",
                   "song_variants": [{"id": "song-a", "file": "/tmp/chosen.mp3",
                                      "track": {"file": "/tmp/chosen.mp3"}}],
                   "selected_song": "song-a",
                   "image_variants": [{"id": "image-a", "file": str(image)}],
                   "selected_image": "image-a"}
            with mock.patch.object(app, "job_snapshot", return_value=job), \
                    mock.patch.object(app, "set_job"), \
                    mock.patch.object(app.threading, "Thread") as thread:
                app.pipeline_action("job12345", "retry_video")
            self.assertEqual(thread.call_args.kwargs["target"], app.run_video_job)
            track = thread.call_args.kwargs["args"][1]
            self.assertEqual(track["file"], "/tmp/chosen.mp3")
            self.assertEqual(track["pipeline_image"], str(image))
            self.assertIn("Retry video</button>", app.PAGE)

    def test_render_recovers_saved_task_without_dispatching_again(self):
        saved = {"cloud_execution": {"job_id": "job12345", "attempt_id": "attempt12345",
                                      "task_arn": "arn:task", "result_uri": "s3://bucket/result.json"}}
        updates = []

        def set_job(_job_id, **changes):
            saved.update(changes)
            updates.append(changes)

        successful = {**saved["cloud_execution"], "status": "succeeded",
                      "result": {"status": "succeeded"}}
        with mock.patch.object(app, "aws_settings", return_value={"bucket": "bucket"}), \
                mock.patch.object(app, "job_snapshot", return_value=saved), \
                mock.patch.object(app, "set_job", side_effect=set_job), \
                mock.patch("aws_render._config"), \
                mock.patch("aws_render.reconcile_render") as reconcile, \
                mock.patch("aws_render.dispatch_render") as dispatch, \
                mock.patch("aws_render.wait_or_poll_render", return_value=successful), \
                mock.patch("aws_render.download_verified") as download:
            app.render_video_aws("job12345", {}, {}, "/tmp/video.mp4")
        reconcile.assert_not_called()
        dispatch.assert_not_called()
        download.assert_called_once()
        self.assertEqual(updates[-1]["cloud_execution"]["status"], "succeeded")

    def test_prelaunch_aws_failure_retries_same_pipeline_attempt(self):
        with TemporaryDirectory() as root:
            audio, image, output = (Path(root) / name for name in
                                    ("song.mp3", "chosen.png", "video.mp4"))
            audio.write_bytes(b"audio")
            image.write_bytes(b"art")
            job = {"id": "job12345", "pipeline": True, "status": "error", "stage": "video",
                   "song_variants": [{"id": "song-a", "file": str(audio),
                                      "track": {"file": str(audio)}}],
                   "selected_song": "song-a",
                   "image_variants": [{"id": "image-a", "file": str(image)}],
                   "selected_image": "image-a"}
            dispatched_attempts = []

            def set_job(_job_id, **changes):
                job.update(changes)

            def dispatch(_config, job_id, attempt, _assets, _settings, on_dispatch=None):
                dispatched_attempts.append(attempt)
                if len(dispatched_attempts) == 1:
                    raise PermissionError("expired AWS credentials during upload")
                on_dispatch()
                return {"mode": "render", "job_id": job_id, "attempt_id": attempt,
                        "task_arn": "arn:task", "result_uri": "s3://bucket/result.json",
                        "output_uri": "s3://bucket/video.mp4", "status": "running"}

            successful = {"status": "succeeded", "result": {"status": "succeeded"}}

            class ImmediateThread:
                def __init__(self, target, args=(), kwargs=None, daemon=None):
                    self.target, self.args, self.kwargs = target, args, kwargs or {}

                def start(self):
                    self.target(*self.args, **self.kwargs)

            def retry_worker(job_id, track, recovering=False):
                self.assertEqual(track["file"], str(audio))
                self.assertEqual(track["pipeline_image"], str(image))
                app.render_video_aws(job_id, {"audio": audio, "background": image},
                                     {"height": 720}, output)

            with mock.patch.object(app, "aws_settings", return_value={"bucket": "bucket"}), \
                    mock.patch.object(app, "job_snapshot", return_value=job), \
                    mock.patch.object(app, "set_job", side_effect=set_job), \
                    mock.patch.object(app, "load_jobs",
                                      side_effect=lambda: ({job["id"]: job}, {})), \
                    mock.patch("aws_render._config"), \
                    mock.patch("aws_render.reconcile_render",
                               side_effect=lambda _config, state: state) as reconcile, \
                    mock.patch("aws_render.dispatch_render", side_effect=dispatch), \
                    mock.patch("aws_render.wait_or_poll_render", return_value=successful) as poll, \
                    mock.patch("aws_render.download_verified"), \
                    mock.patch.object(app, "run_video_job", side_effect=retry_worker), \
                    mock.patch.object(app.threading, "Thread", ImmediateThread), \
                    mock.patch.object(app, "make_provider") as provider:
                with self.assertRaisesRegex(PermissionError, "expired AWS credentials"):
                    app.render_video_aws("job12345", {"audio": audio, "background": image},
                                         {"height": 720}, output)

                saved_attempt = job["cloud_execution"]
                self.assertEqual(saved_attempt["status"], "preparing")
                self.assertNotIn("task_arn", saved_attempt)
                app.pipeline_action("job12345", "retry_video")

            self.assertEqual(dispatched_attempts, [saved_attempt["attempt_id"]] * 2)
            self.assertEqual(job["cloud_execution"]["status"], "succeeded")
            self.assertEqual(poll.call_count, 1)
            reconcile.assert_not_called()
            provider.assert_not_called()

    def test_retry_video_keeps_unconfirmed_dispatch_blocked(self):
        attempt = {"attempt_id": "attempt12345", "status": "dispatching",
                   "result_uri": "s3://bucket/result.json"}
        job = {"id": "job12345", "pipeline": True, "status": "error", "stage": "video",
               "cloud_execution": attempt}
        with mock.patch.object(app, "job_snapshot", return_value=job), \
                mock.patch.object(app, "aws_settings", return_value={}), \
                mock.patch("aws_render.wait_or_poll_render", return_value={
                    **attempt, "status": "running"}), \
                mock.patch("aws_render.reconcile_render", return_value=attempt), \
                mock.patch.object(app, "start_pipeline_video") as start:
            with self.assertRaisesRegex(RuntimeError, "AWS attempt is unconfirmed"):
                app.pipeline_action("job12345", "retry_video")
        start.assert_not_called()

    def test_preparing_attempt_with_task_arn_recovers_existing_task(self):
        attempt = {"attempt_id": "attempt12345", "status": "preparing",
                   "task_arn": "arn:task", "result_uri": "s3://bucket/result.json"}
        job = {"id": "job12345", "pipeline": True, "status": "error", "stage": "video",
               "cloud_execution": attempt,
               "song_variants": [{"id": "song-a", "file": "/tmp/song.mp3"}],
               "selected_song": "song-a",
               "image_variants": [{"id": "image-a", "file": "/tmp/image.png"}],
               "selected_image": "image-a"}
        with mock.patch.object(app, "job_snapshot", return_value=job), \
                mock.patch.object(app, "aws_settings", return_value={}), \
                mock.patch("aws_render.wait_or_poll_render", return_value={
                    **attempt, "status": "running"}), \
                mock.patch("aws_render.reconcile_render") as reconcile, \
                mock.patch.object(app, "set_job"), \
                mock.patch.object(app.threading, "Thread") as thread, \
                mock.patch.object(app, "start_pipeline_video") as start:
            app.pipeline_action("job12345", "retry_video")
        reconcile.assert_not_called()
        start.assert_not_called()
        self.assertTrue(thread.call_args.kwargs["args"][2])

    def test_render_does_not_start_task_when_dispatch_journal_is_not_durable(self):
        with TemporaryDirectory() as root:
            audio, image = Path(root) / "song.mp3", Path(root) / "art.png"
            audio.write_bytes(b"audio")
            image.write_bytes(b"art")
            job = {"id": "job12345", "created": 1}
            with mock.patch.object(app, "JOBS", {"job12345": job}), \
                    mock.patch.object(app, "JOBS_PATH", Path(root) / "jobs.json"), \
                    mock.patch.object(app, "aws_settings", return_value={"bucket": "bucket"}), \
                    mock.patch.object(app, "atomic_write_json", side_effect=OSError("disk full")), \
                    mock.patch("aws_render._config", side_effect=lambda config: config), \
                    mock.patch("aws_render._upload_file", return_value={"uri": "s3://bucket/asset"}), \
                    mock.patch("aws_render._put_json_once", return_value="s3://bucket/manifest.json"), \
                    mock.patch("aws_render._run_task") as run_task:
                with self.assertRaisesRegex(RuntimeError, "journal could not be saved"):
                    app.render_video_aws("job12345", {"audio": audio, "background": image},
                                         {}, Path(root) / "out.mp4")
                self.assertNotIn("job12345", app.load_jobs()[0])
                self.assertEqual(job["cloud_execution"]["status"], "preparing")
            run_task.assert_not_called()

    def test_bug_report_sends_only_preview_allowlist(self):
        with mock.patch("bug_reports.send_report", return_value="event123") as sent:
            result = test_app_reliability.post_json("/api/bug/submit", {
                "stage": "video", "error_summary": "Video encoding failed",
                "lyrics": "private words", "token": "secret"})
        self.assertEqual(result["reference"], "event123")
        self.assertEqual(set(sent.call_args.args[0]),
                         {"version", "platform", "stage", "error_summary"})
        self.assertNotIn("private words", str(sent.call_args))

    def test_ambiguous_aws_slack_dispatch_requires_review(self):
        job = {"id": "job12345", "final_path": "/tmp/video.mp4",
               "delivery_destination": "C12345678", "render_backend": "aws"}
        saved = {}

        def set_job(_job_id, **changes):
            saved.update(changes)

        with mock.patch.object(app, "aws_settings", return_value={"bucket": "bucket"}), \
                mock.patch.object(app, "set_job", side_effect=set_job), \
                mock.patch("aws_render.dispatch_slack_delivery", side_effect=TimeoutError), \
                mock.patch("aws_render.reconcile_render", side_effect=lambda _cfg, state: state):
            with self.assertRaises(app.SlackDeliveryError) as raised:
                app.cloud_slack_for_job(job)
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(saved["cloud_delivery"]["status"], "dispatching")

    def test_unconfirmed_aws_slack_retry_does_not_launch_new_task(self):
        attempt = {"attempt_id": "attempt12345", "status": "dispatching",
                   "result_uri": "s3://bucket/result.json"}
        job = {"id": "job12345", "status": "completed", "final_path": "/tmp/video.mp4",
               "delivery_status": "error", "render_backend": "aws",
               "cloud_delivery": attempt,
               "current_fields": {"delivery_mode": "slack", "slack_channel_id": "C12345678"}}
        with mock.patch.object(app, "job_snapshot", return_value=job), \
                mock.patch.object(app, "set_job") as update, \
                mock.patch.object(app.threading, "Thread") as thread, \
                mock.patch.object(app, "aws_settings", return_value={}), \
                mock.patch("aws_render.wait_or_poll_render", return_value={
                    **attempt, "status": "running"}), \
                mock.patch("aws_render.reconcile_render", return_value=attempt):
            self.assertFalse(app.queue_delivery("job12345"))
        thread.assert_not_called()
        self.assertEqual(update.call_args.kwargs["delivery_status"], "needs_review")

    def test_saved_uncertain_slack_result_requires_review_even_if_status_says_error(self):
        attempt = {"attempt_id": "attempt12345", "task_arn": "arn:task",
                   "status": "uncertain", "result_uri": "s3://bucket/result.json"}
        job = {"id": "job12345", "status": "completed", "final_path": "/tmp/video.mp4",
               "delivery_status": "error", "render_backend": "aws",
               "cloud_delivery": attempt,
               "current_fields": {"delivery_mode": "slack", "slack_channel_id": "C12345678"}}
        with mock.patch.object(app, "job_snapshot", return_value=job), \
                mock.patch.object(app, "set_job") as update, \
                mock.patch.object(app.threading, "Thread") as thread, \
                mock.patch.object(app, "aws_settings", return_value={}), \
                mock.patch("aws_render.wait_or_poll_render", return_value=attempt):
            self.assertFalse(app.queue_delivery("job12345"))
        thread.assert_not_called()
        self.assertEqual(update.call_args.kwargs["delivery_status"], "needs_review")

    def test_stopped_aws_slack_task_requires_review(self):
        job = {"id": "job12345", "final_path": "/tmp/video.mp4",
               "delivery_destination": "C12345678", "cloud_delivery": {
                   "attempt_id": "attempt12345", "task_arn": "arn:task",
                   "result_uri": "s3://bucket/result.json"}}
        with mock.patch.object(app, "aws_settings", return_value={}), \
                mock.patch.object(app, "set_job"), \
                mock.patch("aws_render.reconcile_render", side_effect=lambda _cfg, state: state), \
                mock.patch("aws_render.wait_or_poll_render", return_value={
                    "status": "failed", "result": {"error": "task stopped without result"}}):
            with self.assertRaises(app.SlackDeliveryError) as raised:
                app.cloud_slack_for_job(job)
        self.assertTrue(raised.exception.uncertain)


if __name__ == "__main__":
    unittest.main()
