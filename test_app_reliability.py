import io
import inspect
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import suno_studio as app


class ReliabilityTests(unittest.TestCase):
    def test_generation_keeps_only_first_provider_version(self):
        tracks = [{"id": "first"}, {"id": "second"}]
        self.assertEqual(app.primary_track_only(tracks), [{"id": "first"}])
        self.assertEqual(app.primary_track_only([]), [])

    def test_art_accent_tracks_dominant_saturated_hue(self):
        orange = bytes([232, 104, 24] * 100 + [18, 18, 18] * 80)
        blue = bytes([30, 105, 225] * 100 + [245, 245, 245] * 80)

        def channels(value):
            return tuple(int(value[i:i + 2], 16) for i in (2, 4, 6))

        orange_rgb = channels(app.dominant_art_accent(orange))
        blue_rgb = channels(app.dominant_art_accent(blue))
        self.assertGreater(orange_rgb[0], orange_rgb[1])
        self.assertGreater(orange_rgb[1], orange_rgb[2])
        self.assertGreater(blue_rgb[2], blue_rgb[1])
        self.assertGreater(blue_rgb[1], blue_rgb[0])

    def test_render_forwards_art_accent_to_visualizer(self):
        filters = {"showfreqs"}
        with mock.patch.object(app, "ffmpeg_filters", return_value=filters), \
                mock.patch.object(app, "audio_duration", return_value=1.0), \
                mock.patch.object(app, "run_ffmpeg") as run:
            app.render_lyric_video("ffmpeg", "song.mp3", "background.png",
                                   None, "video.mp4", height=1080, fps=30,
                                   vis="bars", accent="0xF27F36",
                                   shimmer=False, interlude_mode=False)
        args = run.call_args.args[1]
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("colors=0xF27F36", graph)
        self.assertEqual(args[-3:], ["-f", "mp4", "video.mp4"])

    def test_email_title_marker_survives_leading_zero_width_character(self):
        body = ("\u200c===TITLE===\nThe Med Launch Magic\n\n"
                "===STYLE===\n70s soul, horn section, group vocals, warm\n\n"
                "===SPRINT===\nLearning Path 26.3.2\n\n"
                "===EMAIL===\nauthor@example.com\n")
        form = app.parse_request("Learning Path 26.3.2", body)
        self.assertEqual(form["title"], "The Med Launch Magic")
        self.assertEqual(form["tagline"], "Learning Path 26.3.2")
        self.assertEqual(form["recipient"], "author@example.com")
        self.assertEqual(app.compose_basename(form["tagline"], form["title"]),
                         "Learning Path 26.3.2 - The Med Launch Magic")

    def test_pipeline_filename_uses_the_approved_sprint_and_title(self):
        self.assertEqual(app.compose_basename(
            "Modernization 26.3.3", "The Modernization Rhythm"),
            "Modernization 26.3.3 - The Modernization Rhythm")

    def test_final_approval_can_update_delivery_email(self):
        old_jobs = app.JOBS
        try:
            app.JOBS = {"job": {"id": "job", "pipeline": True,
                                "status": "paused_video", "stage": "video",
                                "current_fields": {"title": "A", "recipient": "old@example.com"}}}
            with mock.patch.object(app, "_save_jobs_locked"), \
                    mock.patch.object(app, "finalize_pipeline_job") as publish:
                app.pipeline_action("job", "approve_video",
                                    {"recipient": "New Recipient <new@example.com>"})
            self.assertEqual(app.JOBS["job"]["current_fields"]["recipient"], "new@example.com")
            publish.assert_called_once_with("job")
        finally:
            app.JOBS = old_jobs

    def test_final_publication_routes_by_the_approved_delivery_email(self):
        old_config = dict(app.CONFIG)
        try:
            with tempfile.TemporaryDirectory() as td:
                source = Path(td) / "video.mp4"
                source.write_bytes(b"complete video")
                app.CONFIG["output_dir"] = str(Path(td) / "Final")
                app.CONFIG["video_dir"] = str(Path(td) / "Sprint Songs")
                published = Path(app.move_to_final(source, "job", "Recipient <send@example.com>"))
                self.assertEqual(published.parents[1].name, "Sprint Songs")
                self.assertEqual(published.parent.name, "send@example.com")
                self.assertTrue(published.is_file())
        finally:
            app.CONFIG.clear(); app.CONFIG.update(old_config)

    def test_final_video_root_falls_back_to_output_folder(self):
        old_config = dict(app.CONFIG)
        try:
            app.CONFIG.update({"output_dir": "/tmp/Final", "video_dir": ""})
            self.assertEqual(app.final_video_root(), Path("/tmp/Final"))
        finally:
            app.CONFIG.clear(); app.CONFIG.update(old_config)

    def test_completed_job_reveal_uses_final_file_not_deleted_staging_folder(self):
        self.assertIn("const revealPath = j.final_path || j.folder;", app.PAGE)
        self.assertIn('["open", "-R", str(target)]', inspect.getsource(app.Handler.do_POST))

    def test_sender_allowlist_uses_exact_mailbox_or_domain(self):
        old = app.CONFIG.get("allowed_senders")
        try:
            app.CONFIG["allowed_senders"] = "trusted@example.com, @example.org"
            self.assertTrue(app.sender_allowed("Trusted <trusted@example.com>"))
            self.assertTrue(app.sender_allowed("Person <person@example.org>"))
            self.assertFalse(app.sender_allowed(
                "trusted@example.com <attacker@evil.example>"))
            self.assertFalse(app.sender_allowed("person@example.org.evil.example"))
        finally:
            app.CONFIG["allowed_senders"] = old

    def test_atomic_output_allocators_do_not_reuse_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = app.allocate_unique_dir(root / "song")
            second = app.allocate_unique_dir(root / "song")
            self.assertEqual(first.name, "song")
            self.assertEqual(second.name, "song (2)")
            one = app.reserve_unique_path(root / "video.mp4")
            two = app.reserve_unique_path(root / "video.mp4")
            self.assertEqual(one.name, "video.mp4")
            self.assertEqual(two.name, "video (2).mp4")

    def test_atomic_json_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            app.atomic_write_json(path, {"ok": [1, 2, 3]}, mode=0o600)
            self.assertEqual(json.loads(path.read_text()), {"ok": [1, 2, 3]})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_job_journal_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            old_path, old_jobs, old_forms = app.JOBS_PATH, app.JOBS, app.JOB_FORMS
            try:
                app.JOBS_PATH = Path(td) / "jobs.json"
                app.JOBS = {"j": {"id": "j", "created": 1, "status": "running",
                                  "task_id": "task", "kind": "generation"}}
                app.JOB_FORMS = {"j": {"title": "Saved"}}
                with app.JOBS_LOCK:
                    app._save_jobs_locked()
                jobs, forms = app.load_jobs()
                self.assertEqual(jobs["j"]["task_id"], "task")
                self.assertEqual(forms["j"]["title"], "Saved")
            finally:
                app.JOBS_PATH, app.JOBS, app.JOB_FORMS = old_path, old_jobs, old_forms

    def test_video_job_persists_track_for_restart(self):
        old_jobs, old_forms = app.JOBS, app.JOB_FORMS
        try:
            app.JOBS, app.JOB_FORMS = {}, {}
            track = {"file": "/tmp/song.mp3", "song_title": "Recoverable"}
            with mock.patch.object(app, "_save_jobs_locked"), \
                    mock.patch.object(app.threading, "Thread"):
                job_id = app.start_video_job(track)
            self.assertEqual(app.JOB_FORMS[job_id]["track"], track)
            self.assertEqual(app.JOBS[job_id]["phase"], "queued")
            self.assertIsNone(app.JOBS[job_id]["progress"])
        finally:
            app.JOBS, app.JOB_FORMS = old_jobs, old_forms

    def test_pipeline_video_stays_in_job_and_uses_exact_selected_image(self):
        old_jobs = app.JOBS
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                first, selected = root / "first.png", root / "selected.png"
                first.write_bytes(b"first")
                selected.write_bytes(b"selected")
                app.JOBS = {"pipeline": {
                    "id": "pipeline", "pipeline": True, "status": "paused_image",
                    "selected_song": "song-1", "selected_image": "image-2",
                    "song_variants": [{"id": "song-1", "file": str(root / "song.mp3"),
                                       "track": {"file": str(root / "song.mp3")}}],
                    "image_variants": [{"id": "image-1", "file": str(first)},
                                       {"id": "image-2", "file": str(selected)}],
                }}
                with mock.patch.object(app, "_save_jobs_locked"), \
                        mock.patch.object(app.threading, "Thread") as thread:
                    app.start_pipeline_video("pipeline")
                self.assertEqual(set(app.JOBS), {"pipeline"})
                self.assertEqual(app.JOBS["pipeline"]["video_image_id"], "image-2")
                self.assertEqual(app.JOBS["pipeline"]["video_image_file"], str(selected))
                self.assertEqual(thread.call_args.kwargs["target"], app.run_video_job)
                self.assertEqual(thread.call_args.kwargs["args"][0], "pipeline")
                self.assertEqual(thread.call_args.kwargs["args"][1]["pipeline_image"], str(selected))
        finally:
            app.JOBS = old_jobs

    def test_pipeline_ui_uses_collapsible_stages_without_standalone_video_button(self):
        self.assertIn('class="job"', app.PAGE)
        self.assertIn("Edit Song Details", app.PAGE)
        self.assertIn("Video Generation", app.PAGE)
        self.assertIn("Waiting for Video Approval", app.PAGE)
        self.assertIn("Cancel And Remove", app.PAGE)
        self.assertIn("image-selected-preview", app.PAGE)
        self.assertIn('id="imageviewer"', app.PAGE)
        self.assertNotIn("Make Lyric Video", app.PAGE)

    def test_cancel_and_remove_forgets_card_but_preserves_rejected_artifacts(self):
        old_jobs, old_forms, old_cancels = app.JOBS, app.JOB_FORMS, app.JOB_CANCELS
        old_config = dict(app.CONFIG)
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                staging, rejects = root / "staging-job", root / "rejects"
                staging.mkdir()
                (staging / "art.png").write_bytes(b"recoverable")
                app.CONFIG["rejects_dir"] = str(rejects)
                app.JOBS = {"job": {"id": "job", "pipeline": True,
                                     "status": "paused_image",
                                     "staging_folder": str(staging)}}
                app.JOB_FORMS = {"job": {"title": "Cancelled"}}
                app.JOB_CANCELS = {}
                with mock.patch.object(app, "_save_jobs_locked"):
                    app.pipeline_action("job", "cancel_remove")
                self.assertNotIn("job", app.JOBS)
                self.assertNotIn("job", app.JOB_FORMS)
                self.assertTrue(app.JOB_CANCELS["job"].is_set())
                preserved = list(rejects.rglob("art.png"))
                self.assertEqual(len(preserved), 1)
                self.assertEqual(preserved[0].read_bytes(), b"recoverable")
        finally:
            app.JOBS, app.JOB_FORMS, app.JOB_CANCELS = old_jobs, old_forms, old_cancels
            app.CONFIG.clear()
            app.CONFIG.update(old_config)

    def test_inbox_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            old_path, old_inbox = app.INBOX_PATH, app.INBOX
            try:
                app.INBOX_PATH = Path(td) / "inbox.json"
                app.INBOX = {"i": {"id": "i", "mid": "message", "form": {"title": "Saved"}}}
                with app.INBOX_LOCK:
                    app._save_inbox_locked()
                self.assertEqual(app.load_inbox()["i"]["form"]["title"], "Saved")
            finally:
                app.INBOX_PATH, app.INBOX = old_path, old_inbox

    def test_restart_recovery_resumes_only_safe_states(self):
        old_jobs, old_forms = app.JOBS, app.JOB_FORMS
        try:
            app.JOBS = {
                "running": {"id": "running", "kind": "generation", "status": "running",
                            "task_id": "provider-task"},
                "submitting": {"id": "submitting", "kind": "generation",
                               "status": "submitting", "task_id": ""},
                "video": {"id": "video", "kind": "video", "status": "running"},
            }
            app.JOB_FORMS = {"running": {"title": "Resume me"},
                             "submitting": {"title": "Do not resubmit"},
                             "video": {"track": {"file": "/tmp/song.mp3"}}}
            with mock.patch.object(app, "_save_jobs_locked"), \
                    mock.patch.object(app.threading, "Thread") as thread:
                app.resume_persisted_jobs()
            self.assertEqual(thread.call_count, 2)
            generation_call, video_call = thread.call_args_list
            self.assertEqual(generation_call.kwargs["target"], app.run_job)
            self.assertEqual(generation_call.kwargs["args"][0], "running")
            self.assertEqual(video_call.kwargs["target"], app.run_video_job)
            self.assertEqual(video_call.kwargs["args"],
                             ("video", {"file": "/tmp/song.mp3"}, True))
            self.assertEqual(app.JOBS["submitting"]["status"], "error")
            self.assertIn("duplicate credits", app.JOBS["submitting"]["message"])
            self.assertEqual(app.JOBS["video"]["status"], "queued")
            self.assertIn("recovering", app.JOBS["video"]["message"])
        finally:
            app.JOBS, app.JOB_FORMS = old_jobs, old_forms

    def test_ffmpeg_progress_parser_and_callback(self):
        self.assertEqual(app.ffmpeg_progress_seconds("01:02:03.500000"), 3723.5)
        self.assertIsNone(app.ffmpeg_progress_seconds("not-a-time"))
        process = mock.Mock()
        process.stdout = io.StringIO(
            "out_time=00:00:01.000000\nprogress=continue\n"
            "out_time=00:00:02.000000\nprogress=end\n")
        process.wait.return_value = 0
        process.pid = 4321
        updates = []
        pids = []
        with mock.patch.object(app.subprocess, "Popen", return_value=process):
            result = app.run_ffmpeg(
                "ffmpeg", ["out.mp4"], "video render",
                progress_callback=updates.append, duration=2.0,
                process_callback=pids.append)
        self.assertEqual(result.returncode, 0)
        self.assertIn(0.5, updates)
        self.assertEqual(updates[-1], 1.0)
        self.assertEqual(pids, [4321])

    def test_completed_video_recovery_requires_size_and_matching_duration(self):
        with tempfile.TemporaryDirectory() as td:
            video = Path(td) / "video.mp4"
            video.write_bytes(b"x" * 100_001)
            with mock.patch.object(app, "audio_duration", return_value=59.5):
                self.assertTrue(app.completed_video_matches("ffmpeg", video, 60.0))
            with mock.patch.object(app, "audio_duration", return_value=50.0):
                self.assertFalse(app.completed_video_matches("ffmpeg", video, 60.0))
            self.assertEqual(app.video_part_path(video).name, "video.mp4.part")

    def test_stale_scratch_cleanup_is_scoped_to_exact_video_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stale = root / ".suno-12345678-old"
            other = root / ".suno-87654321-keep"
            stale.mkdir(); other.mkdir()
            (stale / "request.json").write_text("x")
            (other / "request.json").write_text("x")
            app.cleanup_video_scratch(root, "12345678abcdef")
            self.assertFalse(stale.exists())
            self.assertTrue(other.exists())

    def test_interrupted_encoder_termination_verifies_command_identity(self):
        matching = mock.Mock(
            returncode=0,
            stdout="/usr/local/bin/ffmpeg -i song.mp3 /tmp/song.part.mp4")
        with mock.patch.object(app.subprocess, "run", return_value=matching), \
                mock.patch.object(app.os, "kill",
                                  side_effect=[None, ProcessLookupError]) as kill:
            self.assertTrue(app.terminate_interrupted_encoder(
                4321, "/tmp/song.part.mp4"))
            self.assertEqual(kill.call_args_list[0].args,
                             (4321, app.signal.SIGTERM))

        unrelated = mock.Mock(returncode=0, stdout="/usr/bin/python unrelated.py")
        with mock.patch.object(app.subprocess, "run", return_value=unrelated), \
                mock.patch.object(app.os, "kill") as kill:
            self.assertFalse(app.terminate_interrupted_encoder(
                4321, "/tmp/song.part.mp4"))
            kill.assert_not_called()

    def test_file_server_streams_standard_and_suffix_ranges(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            media = root / "sample.bin"
            media.write_bytes(bytes(range(100)))
            old_output, old_video = app.CONFIG.get("output_dir"), app.CONFIG.get("video_dir")
            app.CONFIG["output_dir"], app.CONFIG["video_dir"] = td, ""
            try:
                def request(range_value):
                    handler = object.__new__(app.Handler)
                    handler.headers = {"Range": range_value}
                    handler.wfile = io.BytesIO()
                    result = {"status": None, "headers": {}}
                    handler.send_response = lambda code: result.update(status=code)
                    handler.send_header = lambda key, value: result["headers"].__setitem__(key, value)
                    handler.end_headers = lambda: None
                    handler._serve_file(str(media))
                    return result, handler.wfile.getvalue()

                result, body = request("bytes=10-19")
                self.assertEqual(result["status"], 206)
                self.assertEqual(body, bytes(range(10, 20)))

                result, body = request("bytes=-5")
                self.assertEqual(result["status"], 206)
                self.assertEqual(body, bytes(range(95, 100)))

                result, body = request("bytes=200-300")
                self.assertEqual(result["status"], 416)
                self.assertEqual(body, b"")
            finally:
                app.CONFIG["output_dir"], app.CONFIG["video_dir"] = old_output, old_video

    def test_file_server_allows_pipeline_staging_audio(self):
        """Queued songs remain playable before an approval gate publishes them."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stage = root / "Staging" / "job"; stage.mkdir(parents=True)
            media = stage / "song.mp3"; media.write_bytes(b"ID3test audio")
            old = dict(app.CONFIG)
            try:
                app.CONFIG.update({"output_dir": str(root / "Final"),
                                   "video_dir": "", "staging_dir": str(root / "Staging")})
                handler = object.__new__(app.Handler)
                handler.headers = {}
                handler.wfile = io.BytesIO()
                result = {"status": None}
                handler.send_response = lambda code: result.update(status=code)
                handler.send_header = lambda *args: None
                handler.end_headers = lambda: None
                handler._serve_file(str(media))
                self.assertEqual(result["status"], 200)
                self.assertEqual(handler.wfile.getvalue(), media.read_bytes())
            finally:
                app.CONFIG.clear(); app.CONFIG.update(old)

    def test_image_request_does_not_retry_a_timeout_as_size_fallback(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(app, "api_json", side_effect=TimeoutError("timed out")) as api:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                app.openai_image("key", "prompt", Path(td) / "art.png", timeout=1)
            self.assertEqual(api.call_count, 1)

    def test_transient_network_failures_are_retryable_for_existing_provider_tasks(self):
        self.assertTrue(app.is_transient_network_failure(TimeoutError("read operation timed out")))
        self.assertTrue(app.is_transient_network_failure(RuntimeError("curl exit 28")))
        self.assertFalse(app.is_transient_network_failure(RuntimeError("invalid API key")))

    def test_retry_song_poll_reuses_existing_provider_task_without_resubmitting(self):
        old_jobs, old_forms = app.JOBS, app.JOB_FORMS
        try:
            app.JOBS = {"job": {"id": "job", "pipeline": True, "status": "error",
                                "stage": "song", "task_id": "existing-task",
                                "current_fields": {"title": "Resume me"}}}
            app.JOB_FORMS = {"job": {"title": "Resume me"}}
            with mock.patch.object(app, "_save_jobs_locked"), \
                    mock.patch.object(app.threading, "Thread") as thread:
                app.pipeline_action("job", "retry_song_poll")
            self.assertEqual(app.JOBS["job"]["task_id"], "existing-task")
            self.assertEqual(app.JOBS["job"]["status"], "queued")
            self.assertEqual(thread.call_args.kwargs["target"], app.run_job)
            self.assertEqual(thread.call_args.kwargs["args"], ("job", {"title": "Resume me"}))
        finally:
            app.JOBS, app.JOB_FORMS = old_jobs, old_forms

    def test_openai_images_try_urllib_before_curl_in_a_pipeline_worker(self):
        try:
            app._set_request_context("image-job")
            with mock.patch.object(app, "http_json", return_value={"data": []}) as http, \
                    mock.patch.object(app, "curl_json") as curl:
                self.assertEqual(app.api_json("POST", "https://api.openai.com/v1/images/generations",
                                              "key", {"prompt": "test"}), {"data": []})
            http.assert_called_once()
            curl.assert_not_called()
        finally:
            app._clear_request_context()

    def test_generated_art_is_direct_sharpened_image_without_gradient_or_blur(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            art = root / "art.png"
            art.write_bytes(b"placeholder")
            out = root / "background.png"
            with mock.patch.object(app, "ffmpeg_filters", return_value={"unsharp"}), \
                    mock.patch.object(app, "run_ffmpeg") as run:
                app.make_background("ffmpeg", out, "", "", cover=art,
                                    height=1080, art=True, scratch_dir=root)
            args = run.call_args.args[1]
            graph = args[args.index("-filter_complex") + 1]
            self.assertIn("unsharp=", graph)
            self.assertNotIn("gblur", graph)
            self.assertNotIn("blend=", graph)
            self.assertEqual(args.count("-i"), 1)

    def test_shimmer_is_optional_and_zoom_is_removed(self):
        filters = {"noise", "lutyuv", "drawbox", "gblur", "blend"}

        def render(enabled):
            with mock.patch.object(app, "ffmpeg_filters", return_value=filters), \
                    mock.patch.object(app, "audio_duration", return_value=1.0), \
                    mock.patch.object(app, "run_ffmpeg") as run:
                app.render_lyric_video("ffmpeg", "song.mp3", "background.png",
                                       None, "video.mp4", height=1080, fps=30,
                                       vis="off", shimmer=enabled)
            args = run.call_args.args[1]
            return args[args.index("-filter_complex") + 1]

        animated = render(True)
        still = render(False)
        self.assertIn("noise=", animated)
        self.assertIn("drawbox=", animated)
        self.assertIn("blend=c0_expr=", animated)
        self.assertIn("c1_expr='A'", animated)
        self.assertNotIn("zoompan", animated)
        self.assertNotIn("noise=", still)
        self.assertNotIn("zoompan", still)

    def test_interlude_windows_use_internal_ass_gaps_only(self):
        ass = """[Events]
Dialogue: 0,0:00:10.00,0:00:12.00,Now,,0,0,0,,First
Dialogue: 0,0:00:11.80,0:00:13.00,Now,,0,0,0,,Overlap
Dialogue: 0,0:00:16.50,0:00:18.00,Now,,0,0,0,,Short gap
Dialogue: 0,0:00:25.00,0:00:27.00,Now,,0,0,0,,After interlude
Dialogue: 0,0:00:00.00,0:01:00.00,Banner,,0,0,0,,Ignore banner
"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lyrics.ass"
            path.write_text(ass, encoding="utf-8")
            self.assertEqual(app.ass_interlude_windows(path), [(18.0, 25.0)])

    def test_interlude_mode_adds_gold_glints_only_when_enabled(self):
        filters = {"subtitles", "noise", "lutyuv", "drawbox", "gblur",
                   "geq", "tmix", "color", "alphamerge",
                   "colorchannelmixer", "overlay"}

        def render(enabled):
            with mock.patch.object(app, "ffmpeg_filters", return_value=filters), \
                    mock.patch.object(app, "ass_interlude_windows",
                                      return_value=[(12.0, 20.0)]), \
                    mock.patch.object(app, "audio_duration", return_value=30.0), \
                    mock.patch.object(app, "run_ffmpeg") as run:
                app.render_lyric_video("ffmpeg", "song.mp3", "background.png",
                                       "lyrics.ass", "video.mp4", height=1080,
                                       fps=30, vis="off", shimmer=False,
                                       interlude_mode=enabled)
            args = run.call_args.args[1]
            return args[args.index("-filter_complex") + 1]

        enabled = render(True)
        disabled = render(False)
        self.assertIn("color=c=0xFFD166", enabled)
        self.assertIn("[interlude_src]scale=160:90", enabled)
        self.assertIn("alphamerge", enabled)
        self.assertIn("lt(lum(X,Y),112)", enabled)
        self.assertIn("format=gray,fps=1", enabled)
        self.assertIn("tmix=frames=3", enabled)
        self.assertIn("scale=0.65", enabled)
        self.assertIn("fps=30,geq=lum=", enabled)
        self.assertIn("geq=lum=", enabled)
        self.assertIn("(T-12.000)/1.200", enabled)
        self.assertIn("(20.000-T)/1.200", enabled)
        self.assertIn("enable='between(t,12.000,20.000)'", enabled)
        self.assertNotIn("interlude_gold", disabled)

    def test_lyric_windows_merge_close_lines_but_preserve_instrumental_gap(self):
        ass = """[Events]
Dialogue: 0,0:00:10.00,0:00:12.00,Now,,0,0,0,,First
Dialogue: 0,0:00:12.50,0:00:14.00,Now,,0,0,0,,Second
Dialogue: 0,0:00:25.00,0:00:27.00,Now,,0,0,0,,After gap
Dialogue: 0,0:00:00.00,0:01:00.00,Banner,,0,0,0,,Ignore
"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lyrics.ass"
            path.write_text(ass, encoding="utf-8")
            self.assertEqual(app.ass_lyric_windows(path),
                             [(10.0, 14.0), (25.0, 27.0)])

    def test_lyric_focus_band_is_feathered_ninety_percent_and_timed(self):
        filters = {"subtitles", "crop", "geq", "alphamerge", "overlay", "eq"}
        with mock.patch.object(app, "ffmpeg_filters", return_value=filters), \
                mock.patch.object(app, "ass_lyric_windows",
                                  return_value=[(10.0, 14.0)]), \
                mock.patch.object(app, "audio_duration", return_value=20.0), \
                mock.patch.object(app, "run_ffmpeg") as run:
            app.render_lyric_video(
                "ffmpeg", "song.mp3", "background.png", "lyrics.ass", "video.mp4",
                height=1080, fps=30, vis="off", shimmer=False,
                interlude_mode=False, lyric_focus_band=True, focus_opacity=0.90)
        args = run.call_args.args[1]
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("crop=1920:162:0:691", graph)
        self.assertIn("255*0.900", graph)
        self.assertIn("Y/20.0", graph)
        self.assertIn("(T-9.550)/0.450", graph)
        self.assertIn("(14.450-T)/0.450", graph)
        self.assertIn("eq=brightness=-0.34", graph)

    def test_ai_focus_art_becomes_second_video_input_without_moving_audio(self):
        filters = {"subtitles", "crop", "geq", "alphamerge", "overlay"}
        with tempfile.TemporaryDirectory() as td:
            focus = Path(td) / "focus.png"
            focus.write_bytes(b"image")
            with mock.patch.object(app, "ffmpeg_filters", return_value=filters), \
                    mock.patch.object(app, "ass_lyric_windows", return_value=[(1.0, 2.0)]), \
                    mock.patch.object(app, "audio_duration", return_value=3.0), \
                    mock.patch.object(app, "run_ffmpeg") as run:
                app.render_lyric_video(
                    "ffmpeg", "song.mp3", "background.png", "lyrics.ass", "video.mp4",
                    vis="off", shimmer=False, interlude_mode=False,
                    focus_bg_png=focus)
        args = run.call_args.args[1]
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("[1:v]scale=1920:1080", graph)
        self.assertEqual(args[args.index("-map") + 1], "[vout]")
        self.assertIn("2:a", args)

    def test_lyric_band_mask_targets_exact_lower_strip(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(app, "image_dimensions", return_value=(1792, 1024)), \
                mock.patch.object(app, "run_ffmpeg") as run:
            dest = Path(td) / "mask.png"
            app.make_lyric_band_mask("ffmpeg", "art.png", dest)
        args = run.call_args.args[1]
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("s=1792x1024", graph)
        self.assertIn("drawbox=x=0:y=655:w=iw:h=154", graph)

    def test_cloud_repair_key_uses_environment_not_request_or_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = root / "song.mp3"
            audio.write_bytes(b"audio")
            helper = root / "stable_ts_hybrid.py"
            helper.write_text("# helper")
            python = root / "python"
            python.write_text("binary")
            captured = {}

            def fake_run(command, **kwargs):
                request_path, output_path = map(Path, command[-2:])
                captured["command"] = command
                captured["request"] = request_path.read_text()
                captured["env"] = kwargs.get("env") or {}
                Path(output_path).write_text(json.dumps({
                    "alignedWords": [{"word": "\nhello", "startS": 1.0,
                                      "endS": 2.0}],
                    "rendered_source_lines": 1, "authored_lines": 1,
                    "repairs": [], "warnings": []}))
                return mock.Mock(returncode=0, stdout="ok", stderr="")

            with mock.patch.dict(app.CONFIG, {
                    "hybrid_repair": "cloud", "openai_key": "top-secret-key"}), \
                    mock.patch.object(app, "stable_ts_runtime_python", return_value=python), \
                    mock.patch.object(app, "stable_ts_helper_path", return_value=helper), \
                    mock.patch.object(app.subprocess, "run", side_effect=fake_run):
                result = app.build_stable_ts_hybrid(
                    audio, "hello", [], "/usr/local/bin/ffmpeg",
                    scratch_dir=root / "scratch")

            self.assertTrue(result["alignedWords"])
            self.assertNotIn("top-secret-key", captured["request"])
            self.assertNotIn("top-secret-key", " ".join(map(str, captured["command"])))
            self.assertEqual(captured["env"]["SUNO_STUDIO_OPENAI_KEY"], "top-secret-key")
            self.assertEqual(captured["env"]["PATH"].split(os.pathsep)[0],
                             "/usr/local/bin")
            self.assertTrue(json.loads(captured["request"])["cloudRepair"])


if __name__ == "__main__":
    unittest.main()
