"""Safety checks for the Settings media purge preview and confirmation routes."""

import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import suno_studio as app


def request_json(method, path, payload=None):
    raw = json.dumps(payload or {}).encode("utf-8")

    class Request(app.Handler):
        def __init__(self):
            self.path = path
            self.headers = {"Content-Length": str(len(raw))}
            self.rfile = io.BytesIO(raw)
            self.wfile = io.BytesIO()
            self.status = None

        def send_response(self, status):
            self.status = status

        def send_header(self, _name, _value):
            pass

        def end_headers(self):
            pass

    request = Request()
    getattr(app.Handler, f"do_{method}")(request)
    return request.status, json.loads(request.wfile.getvalue())


@contextmanager
def purge_workspace():
    old_jobs, old_forms, old_config = app.JOBS, app.JOB_FORMS, dict(app.CONFIG)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "Final"
            video_dir = root / "Videos"
            staging = root / "Staging"
            rejects = root / "Rejects"
            for directory in (output, video_dir, staging, rejects):
                directory.mkdir()

            config_path = root / "config.json"
            config_bytes = b'{"aws_profile":"leave-this-secret-alone"}'
            config_path.write_bytes(config_bytes)

            completed_id = "completed-job"
            stage_job = staging / completed_id
            stage_job.mkdir()
            audio = stage_job / "song.mp3"
            audio.write_bytes(b"song")
            staged_video = stage_job / "temporary.mp4"
            staged_video.write_bytes(b"stage")

            archive_job = output / ".suno-studio-media" / completed_id
            archive_job.mkdir(parents=True)
            art = archive_job / "image.png"
            art.write_bytes(b"artwork")

            final_video = video_dir / "approved.mp4"
            final_video.write_bytes(b"final")
            unrelated = output / "keep.txt"
            unrelated.write_bytes(b"unrelated output file")

            reject_job = rejects / "2024-05-01" / "old-rejected-job"
            reject_job.mkdir(parents=True)
            rejected = reject_job / "reject.mp3"
            rejected.write_bytes(b"reject")

            orphan_archive = output / ".suno-studio-media" / "orphan-media"
            orphan_archive.mkdir()
            orphan_media = orphan_archive / "orphan.mp3"
            orphan_media.write_bytes(b"orphan")

            orphan_stage = staging / ("a" * 32)
            orphan_stage.mkdir()
            orphan_stage_file = orphan_stage / "old.wav"
            orphan_stage_file.write_bytes(b"orphan stage")
            unrelated_stage = staging / "manual-folder"
            unrelated_stage.mkdir()
            unrelated_stage_file = unrelated_stage / "keep.mp3"
            unrelated_stage_file.write_bytes(b"manual")

            outside = root / "outside.mp4"
            outside.write_bytes(b"outside")
            app.CONFIG.clear()
            app.CONFIG.update({
                "output_dir": str(output),
                "video_dir": str(video_dir),
                "staging_dir": str(staging),
                "rejects_dir": str(rejects),
            })
            app.JOBS = {
                completed_id: {
                    "id": completed_id,
                    "status": "completed",
                    "pipeline": True,
                    "staging_folder": str(stage_job),
                    "final_path": str(final_video),
                    "song_variants": [{"id": "song", "file": str(audio),
                                       "track": {"file": str(audio)}}],
                    "image_variants": [{"id": "image", "file": str(art)}],
                },
                "outside-job": {"id": "outside-job", "status": "done",
                                 "final_path": str(outside)},
            }
            app.JOB_FORMS = {}
            paths = {
                "root": root, "output": output, "video_dir": video_dir,
                "staging": staging, "rejects": rejects, "stage_job": stage_job,
                "audio": audio, "staged_video": staged_video, "archive_job": archive_job,
                "art": art, "final_video": final_video, "unrelated": unrelated,
                "reject_job": reject_job, "rejected": rejected,
                "orphan_archive": orphan_archive, "orphan_media": orphan_media,
                "orphan_stage": orphan_stage, "orphan_stage_file": orphan_stage_file,
                "unrelated_stage": unrelated_stage,
                "unrelated_stage_file": unrelated_stage_file,
                "outside": outside, "config_path": config_path,
                "config_bytes": config_bytes,
            }
            with mock.patch.object(app, "JOBS_PATH", root / "jobs.json"), \
                    mock.patch.object(app, "CONFIG_PATH", config_path):
                yield paths
    finally:
        app.JOBS, app.JOB_FORMS = old_jobs, old_forms
        app.CONFIG.clear()
        app.CONFIG.update(old_config)


class MediaPurgeTests(unittest.TestCase):
    def test_preview_is_read_only_and_reports_managed_media(self):
        with purge_workspace() as paths:
            status, preview = request_json("GET", "/api/media/purge")

            self.assertEqual(status, 200)
            self.assertFalse(preview["blocked"])
            self.assertEqual(preview["files"], 7)
            self.assertEqual(preview["bytes"], 45)
            self.assertEqual(preview["videos"], 2)
            self.assertEqual(preview["audio_artwork"], 5)
            self.assertEqual(preview["rejects"], 1)
            self.assertEqual(preview["jobs"], 1)
            self.assertTrue(preview["snapshot"])
            self.assertTrue(paths["audio"].is_file())
            self.assertTrue(paths["final_video"].is_file())
            self.assertTrue(paths["rejected"].is_file())
            self.assertTrue(paths["unrelated"].is_file())

    def test_confirmation_snapshot_purges_only_owned_files_and_job_cards(self):
        with purge_workspace() as paths, \
                mock.patch.object(app, "send_job_delivery") as send_delivery:
            _, preview = request_json("GET", "/api/media/purge")

            status, _ = request_json("POST", "/api/media/purge",
                                     {"snapshot": preview["snapshot"]})
            self.assertEqual(status, 400)
            self.assertTrue(paths["audio"].is_file())

            status, _ = request_json("POST", "/api/media/purge", {
                "confirmed": True, "snapshot": "stale-preview",
            })
            self.assertEqual(status, 409)
            self.assertTrue(paths["final_video"].is_file())

            paths["audio"].write_bytes(b"changed after preview")
            status, _ = request_json("POST", "/api/media/purge", {
                "confirmed": True, "snapshot": preview["snapshot"],
            })
            self.assertEqual(status, 409)
            self.assertTrue(paths["audio"].is_file())

            _, preview = request_json("GET", "/api/media/purge")

            status, result = request_json("POST", "/api/media/purge", {
                "confirmed": True, "snapshot": preview["snapshot"],
            })

            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            self.assertEqual(result["files"], 7)
            self.assertEqual(result["jobs"], 1)
            self.assertNotIn("completed-job", app.JOBS)
            self.assertIn("outside-job", app.JOBS)
            for key in ("stage_job", "archive_job", "reject_job", "orphan_archive",
                        "orphan_stage"):
                self.assertFalse(paths[key].exists(), key)
            for key in ("unrelated", "unrelated_stage_file", "outside"):
                self.assertTrue(paths[key].is_file(), key)
            self.assertEqual(paths["config_path"].read_bytes(), paths["config_bytes"])
            self.assertFalse(send_delivery.called)

    def test_unfinished_job_blocks_purge_without_touching_finished_or_retryable_media(self):
        with purge_workspace() as paths:
            pending_id = "pending-job"
            pending = paths["staging"] / pending_id
            pending.mkdir()
            pending_file = pending / "retry.mp3"
            pending_file.write_bytes(b"retryable")
            app.JOBS[pending_id] = {"id": pending_id, "status": "paused_image",
                                    "staging_folder": str(pending)}
            _, preview = request_json("GET", "/api/media/purge")

            self.assertTrue(preview["blocked"])
            self.assertIn("unfinished", preview["reason"].lower())
            status, result = request_json("POST", "/api/media/purge", {
                "confirmed": True, "snapshot": preview["snapshot"],
            })

            self.assertEqual(status, 409)
            self.assertFalse(result["ok"])
            self.assertTrue(paths["audio"].is_file())
            self.assertTrue(pending_file.is_file())
            self.assertIn("completed-job", app.JOBS)

    def test_symlink_escape_is_not_followed_or_counted(self):
        with purge_workspace() as paths:
            outside_dir = paths["root"] / "outside-dir"
            outside_dir.mkdir()
            outside_file = outside_dir / "keep.mp3"
            outside_file.write_bytes(b"outside target")
            link = paths["orphan_archive"] / "escape"
            link.symlink_to(outside_dir, target_is_directory=True)

            _, preview = request_json("GET", "/api/media/purge")
            self.assertEqual(preview["files"], 7)
            self.assertEqual(preview["bytes"], 45)
            status, _ = request_json("POST", "/api/media/purge", {
                "confirmed": True, "snapshot": preview["snapshot"],
            })

            self.assertEqual(status, 200)
            self.assertTrue(outside_file.is_file())
            self.assertFalse(link.is_symlink())

    def test_orphan_archive_reference_purges_its_exact_published_video(self):
        with purge_workspace() as paths:
            job_id = "b" * 32
            published = paths["video_dir"] / "cleared-card.mp4"
            published.write_bytes(b"published")
            app.JOBS[job_id] = {"id": job_id, "status": "running"}
            app.complete_video_job(job_id, published, published.parent, pipeline=False)
            with app.JOBS_LOCK:
                app.JOBS.pop(job_id, None)  # simulate Clear Finished
                app.JOB_FORMS.pop(job_id, None)
                app._save_jobs_locked()

            _, preview = request_json("GET", "/api/media/purge")
            self.assertGreaterEqual(preview["videos"], 3)
            self.assertGreaterEqual(preview["files"], 9)
            status, result = request_json("POST", "/api/media/purge", {
                "confirmed": True, "snapshot": preview["snapshot"],
            })

            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            self.assertFalse(published.exists())
            self.assertFalse((paths["output"] / ".suno-studio-media" / job_id).exists())

    def test_finalized_pipeline_with_stale_image_path_can_be_purged(self):
        old_jobs, old_forms, old_config = app.JOBS, app.JOB_FORMS, dict(app.CONFIG)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                output, video_dir = root / "Final", root / "Videos"
                staging = root / "Staging"
                for directory in (output, video_dir, staging):
                    directory.mkdir()
                job_id = "c" * 32
                stage = staging / job_id
                stage.mkdir()
                audio = stage / "song.mp3"
                image = stage / "art.png"
                video = stage / "render.mp4"
                audio.write_bytes(b"audio")
                image.write_bytes(b"image")
                video.write_bytes(b"video")

                app.CONFIG.clear()
                app.CONFIG.update({"output_dir": str(output),
                                  "video_dir": str(video_dir),
                                  "staging_dir": str(staging),
                                  "rejects_dir": str(root / "Rejects")})
                app.JOBS = {job_id: {
                    "id": job_id, "status": "running", "pipeline": True,
                    "staging_folder": str(stage), "video_path": str(video),
                    "video_image_file": str(image), "video_image_id": "image-1",
                    "current_fields": {"delivery_mode": "none"},
                    "song_variants": [{"id": "song-1", "file": str(audio),
                                       "track": {"file": str(audio)}}],
                    "image_variants": [{"id": "image-1", "file": str(image)}],
                    "selected_image": "image-1",
                }}
                app.JOB_FORMS = {}
                with mock.patch.object(app, "JOBS_PATH", root / "jobs.json"):
                    app.finalize_pipeline_job(job_id)
                    finalized = app.JOBS[job_id]
                    self.assertNotEqual(finalized["video_image_file"], str(image))
                    self.assertFalse(image.exists())
                    self.assertTrue(Path(finalized["image_variants"][0]["file"]).is_file())
                    self.assertTrue(Path(finalized["video_image_file"]).is_file())

                    _, preview = request_json("GET", "/api/media/purge")
                    self.assertFalse(preview["blocked"])
                    self.assertEqual(preview["jobs"], 1)
                    self.assertEqual(preview["skipped"], 0)
                    status, result = request_json("POST", "/api/media/purge", {
                        "confirmed": True, "snapshot": preview["snapshot"],
                    })

                self.assertEqual(status, 200)
                self.assertTrue(result["ok"])
                self.assertNotIn(job_id, app.JOBS)
                self.assertFalse(Path(finalized["final_path"]).exists())
                self.assertFalse((output / ".suno-studio-media" / job_id).exists())
        finally:
            app.JOBS, app.JOB_FORMS = old_jobs, old_forms
            app.CONFIG.clear()
            app.CONFIG.update(old_config)

    def test_empty_completed_card_can_be_purged_and_legacy_output_paths_are_owned(self):
        with purge_workspace() as paths:
            legacy = paths["video_dir"] / "legacy.mp4"
            legacy.write_bytes(b"legacy video")
            app.JOBS["legacy-job"] = {
                "id": "legacy-job", "status": "done",
                "output_path": str(legacy),
                "tracks": [{"file": str(legacy), "video": True}],
            }
            app.JOBS["empty-job"] = {"id": "empty-job", "status": "completed"}
            app.JOBS["cancelled-job"] = {"id": "cancelled-job", "status": "cancelled"}

            _, preview = request_json("GET", "/api/media/purge")
            self.assertEqual(preview["jobs"], 4)
            self.assertGreaterEqual(preview["videos"], 3)
            status, result = request_json("POST", "/api/media/purge", {
                "confirmed": True, "snapshot": preview["snapshot"],
            })

            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            self.assertNotIn("legacy-job", app.JOBS)
            self.assertNotIn("empty-job", app.JOBS)
            self.assertNotIn("cancelled-job", app.JOBS)
            self.assertFalse(legacy.exists())

    def test_owner_change_invalidates_the_preview_snapshot(self):
        with purge_workspace() as paths:
            _, preview = request_json("GET", "/api/media/purge")
            app.JOBS["second-owner"] = {
                "id": "second-owner", "status": "done",
                "song_variants": [{"file": str(paths["audio"])}],
            }

            status, result = request_json("POST", "/api/media/purge", {
                "confirmed": True, "snapshot": preview["snapshot"],
            })

            self.assertEqual(status, 409)
            self.assertFalse(result["ok"])
            self.assertTrue(paths["audio"].is_file())

    def test_recorded_text_path_is_never_treated_as_video_media(self):
        with purge_workspace() as paths:
            app.JOBS["corrupt-job"] = {
                "id": "corrupt-job", "status": "completed",
                "final_path": str(paths["unrelated"]),
            }
            archive = paths["output"] / ".suno-studio-media" / ("d" * 32)
            archive.mkdir(parents=True)
            (archive / ".video.json").write_text(
                json.dumps({"final_path": str(paths["unrelated"])}), encoding="utf-8")

            _, preview = request_json("GET", "/api/media/purge")
            status, result = request_json("POST", "/api/media/purge", {
                "confirmed": True, "snapshot": preview["snapshot"],
            })

            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            self.assertTrue(paths["unrelated"].is_file())
            self.assertIn("corrupt-job", app.JOBS)
            self.assertGreaterEqual(preview["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
