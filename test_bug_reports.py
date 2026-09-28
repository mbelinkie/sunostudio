import json
import os
import unittest
from unittest.mock import patch

import bug_reports


class BugReportTest(unittest.TestCase):
    def test_explicit_report_contains_only_safe_fields(self):
        preview = bug_reports.report_preview(
            "video", "my private lyric and API key; ffmpeg failed", "5.0")
        self.assertEqual(preview["error_summary"], "Video encoding failed")

        captured = {}

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def fake_urlopen(request, timeout):
            captured.update(json.loads(request.data))
            self.assertEqual(timeout, 10)
            return Response()

        with patch.dict(os.environ, {"SUNO_STUDIO_SENTRY_DSN":
                                  "https://publickey@o1.ingest.sentry.io/123"}), \
                patch.object(bug_reports.urllib.request, "urlopen", fake_urlopen):
            report_id = bug_reports.send_report(preview)

        self.assertEqual(report_id, captured["event_id"])
        self.assertEqual(captured["message"], "Video encoding failed")
        self.assertNotIn("private lyric", json.dumps(captured))
        self.assertEqual(set(captured), {
            "event_id", "timestamp", "platform", "level", "release",
            "message", "tags", "fingerprint",
        })


if __name__ == "__main__":
    unittest.main()
