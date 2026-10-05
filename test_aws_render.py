import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import patch

import aws_render
import aws_worker
import setup_aws


CONFIG = {"region": "us-east-1", "bucket": "private-bucket", "cluster": "cluster",
          "render_task": "render-task", "delivery_task": "delivery-task",
          "subnets": ["subnet-1"], "security_group": "sg-1"}


class AWSRenderTests(unittest.TestCase):
    def test_cloud_zips_accept_release_file_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("Dockerfile.aws", "requirements-cloud.txt",
                         "aws_worker.py", "suno_studio.py", "aws_link.py"):
                path = root / name
                path.write_text("test")
                os.utime(path, (0, 0))
            client = unittest.mock.MagicMock()
            client.get_function_url_config.return_value = {
                "FunctionUrl": "https://link.example/", "AuthType": "NONE"}
            session = unittest.mock.MagicMock()
            session.client.return_value = client
            with patch.object(setup_aws, "ROOT", root):
                content = setup_aws._source_zip()
                setup_aws._link_function(session, "role", "bucket", "secret", "us-east-1")
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self.assertEqual(len(archive.namelist()), 4)
            link_content = client.update_function_code.call_args.kwargs["ZipFile"]
            with zipfile.ZipFile(io.BytesIO(link_content)) as archive:
                self.assertEqual(archive.namelist(), ["aws_link.py"])

    def test_worker_requests_slack_upload_slot_as_form_data(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok": true}'
        with patch.object(aws_worker.urllib.request, "urlopen", return_value=response) as opened:
            aws_worker._slack("files.getUploadURLExternal", "xoxb-test",
                              {"filename": "video.mp4", "length": 9})
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("Content-type"),
                         "application/x-www-form-urlencoded")
        self.assertEqual(urllib.parse.parse_qs(request.data.decode()),
                         {"filename": ["video.mp4"], "length": ["9"]})

    def test_manifest_contains_only_prepared_assets_and_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio, background, font = (root / name for name in ("audio.mp3", "art.png", "font.ttf"))
            for path in (audio, background, font):
                path.write_bytes(b"test")
            manifests = []

            def upload(_config, path, key):
                return {"uri": f"s3://private-bucket/{key}", "sha256": "test", "size": 4}

            def put(_config, key, value):
                manifests.append(value)
                return f"s3://private-bucket/{key}"

            with patch.object(aws_render, "_upload_file", upload), \
                    patch.object(aws_render, "_put_json_once", put), \
                    patch.object(aws_render, "_run_task", return_value="task-arn"):
                result = aws_render.dispatch_render(
                    CONFIG, "job12345", "attempt12345",
                    {"audio": audio, "background": background},
                    {"height": 1080, "drawtext_chain": f"fontfile='{font}'", "secret": "never"})
            self.assertEqual(result["task_arn"], "task-arn")
            self.assertNotIn("secret", manifests[0]["settings"])
            self.assertNotIn(str(font), json.dumps(manifests[0]))
            self.assertEqual(len(manifests[0]["drawtext_assets"]), 1)

    def test_three_day_link_never_contains_signing_secret(self):
        with patch.object(aws_render, "upload_delivery_object", return_value={
            "uri": "s3://private-bucket/delivery-objects/job12345/video.mp4"}):
            link = aws_render.email_link_for_file({**CONFIG, "link_url": "https://link.example/",
                "link_secret": "private-secret"}, "job12345", "ignored.mp4")
        self.assertEqual(link["expires_seconds"], 259200)
        self.assertNotIn("private-secret", link["url"])

    def test_upload_keys_change_when_attempt_input_changes(self):
        class S3:
            def __init__(self):
                self.keys = []

            def upload_file(self, path, bucket, key):
                self.keys.append((bucket, key, Path(path).read_bytes()))

        s3 = S3()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audio.mp3"
            path.write_bytes(b"first")
            with patch.object(aws_render, "_client", return_value=s3):
                first = aws_render._upload_file(CONFIG, path, "render-inputs/job12345/attempt12345/audio.mp3")
                path.write_bytes(b"second")
                second = aws_render._upload_file(CONFIG, path, "render-inputs/job12345/attempt12345/audio.mp3")
        self.assertNotEqual(first["uri"], second["uri"])
        self.assertEqual(s3.keys[0][1].split("/")[-1], f"audio-{hashlib.sha256(b'first').hexdigest()}.mp3")
        self.assertEqual(s3.keys[0][2], b"first")

    def test_profile_client_uses_named_boto_session(self):
        seen = {}

        class Boto:
            @staticmethod
            def Session(profile_name, region_name):
                seen["session"] = (profile_name, region_name)
                return Boto

            @staticmethod
            def client(service):
                seen["service"] = service
                return "client"

        with patch.dict("sys.modules", {"boto3": Boto}):
            self.assertEqual(aws_render._client("ecs", "us-east-1", "suno-prod"), "client")
        self.assertEqual(seen, {"session": ("suno-prod", "us-east-1"), "service": "ecs"})

    def test_ecs_dispatch_uses_stable_client_token_and_valid_started_by(self):
        class ECS:
            def __init__(self):
                self.requests = []

            def run_task(self, **kwargs):
                self.requests.append(kwargs)
                return {"tasks": [{"taskArn": "task-arn"}]}

        ecs = ECS()
        with patch.object(aws_render, "_client", return_value=ecs):
            config = aws_render._config(CONFIG)
            aws_render._run_task(config, "render", "attempt12345", "s3://private-bucket/manifest.json")
            aws_render._run_task(config, "render", "attempt12345", "s3://private-bucket/manifest.json")
        first, second = ecs.requests
        self.assertEqual(first["clientToken"], second["clientToken"])
        self.assertEqual(first["clientToken"], "attempt12345")
        self.assertLessEqual(len(first["clientToken"]), 64)
        self.assertEqual(first["startedBy"], "attempt12345")
        self.assertEqual(first["overrides"]["cpu"], "8192")
        self.assertEqual(first["overrides"]["memory"], "16384")
        with patch.object(aws_render, "_client", return_value=ecs):
            aws_render._run_task({**config, "render_size": "balanced"}, "render",
                                 "attempt12345", "s3://private-bucket/manifest.json")
        self.assertEqual(ecs.requests[-1]["overrides"]["cpu"], "4096")
        self.assertEqual(ecs.requests[-1]["overrides"]["memory"], "8192")

    def test_reconcile_finds_stopped_task_after_dispatch_interruption(self):
        class ECS:
            def __init__(self):
                self.calls = []

            def list_tasks(self, **kwargs):
                self.calls.append(kwargs)
                if "startedBy" in kwargs:
                    return {"taskArns": []}
                return {"taskArns": ["task-arn"]}

            def describe_tasks(self, **kwargs):
                return {"tasks": [{"taskArn": "task-arn", "startedBy": "attempt12345"}]}

        ecs = ECS()
        saved = {"attempt_id": "attempt12345", "result_uri": "s3://private-bucket/result.json"}
        with patch.object(aws_render, "_client", return_value=ecs):
            result = aws_render.reconcile_render(CONFIG, saved)
        self.assertEqual(result["task_arn"], "task-arn")
        self.assertEqual(ecs.calls[0]["startedBy"], "attempt12345")
        self.assertNotIn("desiredStatus", ecs.calls[0])
        self.assertEqual(ecs.calls[1]["desiredStatus"], "STOPPED")

    def test_reconcile_uses_result_when_ecs_stopped_task_has_expired(self):
        class ECS:
            def list_tasks(self, **kwargs):
                return {"taskArns": []}

        saved = {"attempt_id": "attempt12345", "result_uri": "s3://private-bucket/result.json"}
        result = {"attempt_id": "attempt12345", "status": "succeeded"}
        with patch.object(aws_render, "_client", return_value=ECS()), \
                patch.object(aws_render, "_get_json", return_value=result):
            recovered = aws_render.reconcile_render(CONFIG, saved)
        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["result"], result)

    def test_poll_rejects_a_result_for_a_different_attempt(self):
        execution = {"attempt_id": "attempt12345", "job_id": "job12345",
                     "result_uri": "s3://private-bucket/result.json"}
        result = {"attempt_id": "other-attempt", "job_id": "job12345", "status": "succeeded"}
        with patch.object(aws_render, "_get_json", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                aws_render.wait_or_poll_render(CONFIG, execution)

    def test_worker_rejects_manifest_uri_outside_private_bucket(self):
        with patch.dict("os.environ", {"SUNO_BUCKET": "private-bucket"}):
            with self.assertRaises(ValueError):
                aws_worker._split_uri("s3://other-bucket/render-inputs/job12345/attempt12345/manifest.json")

    def test_worker_checks_manifest_output_prefix_and_asset_hash(self):
        manifest = {
            "schema_version": 1, "mode": "render", "job_id": "job12345",
            "attempt_id": "attempt12345",
            "assets": {
                "audio": {"uri": "s3://private-bucket/render-inputs/job12345/attempt12345/audio.mp3",
                          "sha256": "a" * 64, "size": 4},
                "background": {"uri": "s3://private-bucket/render-inputs/job12345/attempt12345/background.png",
                               "sha256": "b" * 64, "size": 4},
            },
            "drawtext_assets": [], "settings": {},
            "output_uri": "s3://private-bucket/render-results/job12345/attempt12345/video.mp4",
            "result_uri": "s3://private-bucket/render-results/job12345/attempt12345/result.json",
        }
        uri = "s3://private-bucket/render-inputs/job12345/attempt12345/manifest.json"
        with patch.dict("os.environ", {"SUNO_BUCKET": "private-bucket"}), \
                patch.object(aws_worker, "_json", return_value=manifest):
            self.assertEqual(aws_worker._manifest(uri, "render"), manifest)
            manifest["output_uri"] = "s3://private-bucket/delivery-results/job12345/attempt12345/video.mp4"
            with self.assertRaises(ValueError):
                aws_worker._manifest(uri, "render")

    def test_worker_delivery_can_read_only_this_jobs_render_output(self):
        manifest = {
            "schema_version": 1, "mode": "delivery", "job_id": "job12345",
            "attempt_id": "delivery12345",
            "assets": {"video": {
                "uri": "s3://private-bucket/render-results/job12345/render12345/video.mp4",
                "sha256": "a" * 64, "size": 100}},
            "settings": {"channel_id": "C12345678"}, "output_uri": None,
            "result_uri": "s3://private-bucket/delivery-results/job12345/delivery12345/result.json",
        }
        uri = "s3://private-bucket/delivery-inputs/job12345/delivery12345/manifest.json"
        with patch.dict("os.environ", {"SUNO_BUCKET": "private-bucket"}), \
                patch.object(aws_worker, "_json", return_value=manifest):
            self.assertEqual(aws_worker._manifest(uri, "delivery"), manifest)
            manifest["assets"]["video"]["uri"] = "s3://private-bucket/render-results/job99999/render12345/video.mp4"
            with self.assertRaises(ValueError):
                aws_worker._manifest(uri, "delivery")

    def test_task_roles_are_scoped_to_their_object_prefixes(self):
        render, delivery = setup_aws._worker_access_policies("arn:aws:s3:::private-bucket", "secret-arn")
        render_resources = {statement["Resource"] for statement in render}
        delivery_resources = {statement["Resource"] for statement in delivery}
        self.assertEqual(render_resources, {
            "arn:aws:s3:::private-bucket/render-inputs/*",
            "arn:aws:s3:::private-bucket/render-results/*"})
        self.assertNotIn("arn:aws:s3:::private-bucket/render-inputs/*", delivery_resources)
        self.assertIn("arn:aws:s3:::private-bucket/delivery-results/*", delivery_resources)
        self.assertIn("secret-arn", delivery_resources)
        self.assertNotIn("s3:ListBucket", {action for statement in render + delivery
                                           for action in ([statement["Action"]]
                                                          if isinstance(statement["Action"], str)
                                                          else statement["Action"])})

    def test_existing_iam_role_trust_is_repaired(self):
        class IAM:
            def update_assume_role_policy(self, **kwargs):
                self.trust = json.loads(kwargs["PolicyDocument"])

            def get_role(self, **kwargs):
                return {"Role": {"Arn": "role-arn"}}

            def attach_role_policy(self, **kwargs):
                pass

            def put_role_policy(self, **kwargs):
                pass

        iam = IAM()
        self.assertEqual(setup_aws._role(iam, "suno-role", "ecs-tasks.amazonaws.com", []), "role-arn")
        self.assertEqual(iam.trust["Statement"][0]["Principal"],
                         {"Service": "ecs-tasks.amazonaws.com"})

    def test_setup_persists_the_selected_profile(self):
        class Session:
            region_name = "us-east-1"

            @staticmethod
            def client(name):
                if name == "sts":
                    return type("STS", (), {"get_caller_identity": lambda self: {
                        "Account": "123456789012"}})()
                return object()

        saved = {}
        args = type("Args", (), {"profile": "suno-prod", "region": "us-east-1",
                                  "account": "123456789012", "check": False,
                                  "no_slack": True})()
        with patch.object(setup_aws, "_aws", return_value=Session()), \
                patch.object(setup_aws, "_network", return_value=("vpc-1", ["subnet-1"], "sg-1")), \
                patch.object(setup_aws, "_quota", return_value=100), \
                patch.object(setup_aws, "_bucket", return_value="private-bucket"), \
                patch.object(setup_aws, "_ecr", return_value="repo"), \
                patch.object(setup_aws, "_secret", return_value=""), \
                patch.object(setup_aws, "_role", return_value="role-arn"), \
                patch.object(setup_aws, "_build_image", return_value="image"), \
                patch.object(setup_aws, "_cluster_and_tasks", return_value=("cluster", "render", "delivery")), \
                patch.object(setup_aws, "_read_config", return_value={}), \
                patch.object(setup_aws, "_link_function", return_value="https://link.example/"), \
                patch.object(setup_aws, "_save_config", side_effect=lambda value: saved.update(value)), \
                patch.object(setup_aws.time, "sleep"):
            setup_aws.provision(args)
        self.assertEqual(saved["aws_profile"], "suno-prod")
        self.assertEqual(saved["aws_account_id"], "123456789012")


if __name__ == "__main__":
    unittest.main()
