"""Regression coverage for completed pipeline preview media retention."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import suno_studio as app


class FinishedMediaTests(unittest.TestCase):
    def test_finalize_keeps_all_song_and_image_variants_after_staging_cleanup(self):
        old_jobs, old_forms = app.JOBS, app.JOB_FORMS
        old_config = dict(app.CONFIG)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                final_root = root / "Final"
                stage = root / "Staging" / "job-media"
                stage.mkdir(parents=True)
                video = stage / "approved.mp4"
                video.write_bytes(b"published video")

                song_one = stage / "song-one.mp3"
                song_one.write_bytes(b"song one")
                song_two = stage / "song-two.mp3"
                song_two.write_bytes(b"song two")
                track_audio = root / "uploaded" / "song-two-track.mp3"
                track_audio.parent.mkdir()
                track_audio.write_bytes(b"track audio")
                image_one = stage / "generated.png"
                image_one.write_bytes(b"generated image")
                uploaded_image = root / "uploaded" / "uploaded.png"
                uploaded_image.write_bytes(b"uploaded image")

                app.CONFIG.update({
                    "output_dir": str(final_root),
                    "video_dir": "",
                    "staging_dir": str(root / "Staging"),
                    "rejects_dir": str(root / "Rejects"),
                })
                app.JOBS = {"job-media": {
                    "id": "job-media",
                    "pipeline": True,
                    "status": "paused_video",
                    "stage": "video",
                    "video_path": str(video),
                    "staging_folder": str(stage),
                    "current_fields": {"title": "Approved", "delivery_mode": "none"},
                    "selected_song": "song-one",
                    "selected_image": "image-one",
                    "song_variants": [
                        {"id": "song-one", "file": str(song_one),
                         "track": {"file": str(song_one)}},
                        {"id": "song-two", "file": str(song_two),
                         "track": {"file": str(track_audio)}},
                    ],
                    "image_variants": [
                        {"id": "image-one", "file": str(image_one)},
                        {"id": "image-two", "file": str(uploaded_image)},
                    ],
                }}
                app.JOB_FORMS = {}

                with mock.patch.object(app, "JOBS_PATH", root / "jobs.json"), \
                        mock.patch.object(app, "queue_delivery") as queue_delivery:
                    app.finalize_pipeline_job("job-media")
                    job = app.JOBS["job-media"]
                    saved_job = app.load_jobs()[0]["job-media"]

                self.assertEqual(job["status"], "completed")
                self.assertEqual(Path(job["final_path"]).read_bytes(), b"published video")
                self.assertFalse(stage.exists())
                self.assertFalse(queue_delivery.called)

                expected = {
                    job["song_variants"][0]["file"]: b"song one",
                    job["song_variants"][1]["file"]: b"song two",
                    job["song_variants"][1]["track"]["file"]: b"track audio",
                    job["image_variants"][0]["file"]: b"generated image",
                    job["image_variants"][1]["file"]: b"uploaded image",
                }
                self.assertEqual(len(expected), 5)
                for filename, contents in expected.items():
                    media_path = Path(filename)
                    self.assertTrue(media_path.is_file(), filename)
                    self.assertEqual(media_path.read_bytes(), contents)
                    self.assertTrue(media_path.is_relative_to(final_root.resolve()))
                self.assertEqual(
                    [v["file"] for v in saved_job["song_variants"]],
                    [v["file"] for v in job["song_variants"]],
                )
                self.assertEqual(
                    [v["track"]["file"] for v in saved_job["song_variants"]],
                    [v["track"]["file"] for v in job["song_variants"]],
                )
                self.assertEqual(
                    [v["file"] for v in saved_job["image_variants"]],
                    [v["file"] for v in job["image_variants"]],
                )
                self.assertEqual(job["final_path"], str(final_root / "approved.mp4"))
        finally:
            app.JOBS, app.JOB_FORMS = old_jobs, old_forms
            app.CONFIG.clear()
            app.CONFIG.update(old_config)

    def test_archive_failure_leaves_staging_media_and_video_in_place(self):
        old_jobs, old_forms = app.JOBS, app.JOB_FORMS
        old_config = dict(app.CONFIG)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                final_root = root / "Final"
                stage = root / "Staging" / "job-media"
                stage.mkdir(parents=True)
                video = stage / "approved.mp4"
                video.write_bytes(b"published video")
                audio = stage / "song.mp3"
                audio.write_bytes(b"song audio")
                image = stage / "art.png"
                image.write_bytes(b"gallery image")
                app.CONFIG.update({
                    "output_dir": str(final_root),
                    "video_dir": "",
                    "staging_dir": str(root / "Staging"),
                })
                app.JOBS = {"job-media": {
                    "id": "job-media",
                    "pipeline": True,
                    "status": "paused_video",
                    "stage": "video",
                    "video_path": str(video),
                    "staging_folder": str(stage),
                    "current_fields": {"delivery_mode": "none"},
                    "song_variants": [{"id": "song-one", "file": str(audio),
                                       "track": {"file": str(audio)}}],
                    "image_variants": [{"id": "image-one", "file": str(image)}],
                }}
                app.JOB_FORMS = {}
                original_copy = app.shutil.copy2

                def fail_on_image(source, destination):
                    if Path(source) == image:
                        raise OSError("archive volume unavailable")
                    return original_copy(source, destination)

                with mock.patch.object(app, "JOBS_PATH", root / "jobs.json"), \
                        mock.patch.object(app.shutil, "copy2", side_effect=fail_on_image):
                    with self.assertRaisesRegex(OSError, "archive volume unavailable"):
                        app.finalize_pipeline_job("job-media")

                self.assertTrue(stage.is_dir())
                self.assertEqual(video.read_bytes(), b"published video")
                self.assertEqual(audio.read_bytes(), b"song audio")
                self.assertEqual(image.read_bytes(), b"gallery image")
                self.assertFalse((final_root / "approved.mp4").exists())
        finally:
            app.JOBS, app.JOB_FORMS = old_jobs, old_forms
            app.CONFIG.clear()
            app.CONFIG.update(old_config)


if __name__ == "__main__":
    unittest.main()
