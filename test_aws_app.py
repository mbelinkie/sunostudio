"""App-level checks for AWS dispatch recovery and explicit report sending."""

import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

import suno_studio as app
import test_app_reliability
import setup_aws


class AwsAppTests(unittest.TestCase):
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
