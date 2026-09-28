import hashlib
import hmac
import os
import time
import unittest
from unittest.mock import patch

import aws_link


class LinkTests(unittest.TestCase):
    def test_three_day_token_and_private_prefix(self):
        key = "delivery-objects/12345678/video.mp4"
        expiry = str(int(time.time()) + 259200)
        sig = hmac.new(b"test-secret", f"{key}\n{expiry}".encode(), hashlib.sha256).hexdigest()

        class S3:
            def generate_presigned_url(self, method, Params, ExpiresIn):
                self_params = (method, Params, ExpiresIn)
                self_test.assertEqual(self_params[0], "get_object")
                self_test.assertEqual(self_params[1]["Key"], key)
                self_test.assertEqual(self_params[2], 600)
                return "https://private-download.example/test"

        class Boto:
            def client(self, name):
                self_test.assertEqual(name, "s3")
                return S3()

        self_test = self
        with patch.dict(os.environ, {"SUNO_BUCKET": "private-bucket", "LINK_SECRET": "test-secret"}), \
                patch.dict("sys.modules", {"boto3": Boto()}):
            event = {"queryStringParameters": {"key": key, "expires": expiry, "sig": sig}}
            self.assertEqual(aws_link.handler(event, None)["statusCode"], 302)
            event["queryStringParameters"]["key"] = "render-inputs/12345678/video.mp4"
            self.assertEqual(aws_link.handler(event, None)["statusCode"], 403)

    def test_malformed_expiry_and_signature_return_forbidden(self):
        with patch.dict(os.environ, {"SUNO_BUCKET": "private-bucket", "LINK_SECRET": "test-secret"}), \
                patch.dict("sys.modules", {"boto3": object()}):
            for params in (
                {"key": "delivery-objects/12345678/video.mp4", "expires": "9" * 5000,
                 "sig": "0" * 64},
                {"key": "delivery-objects/12345678/video.mp4", "expires": "²",
                 "sig": "0" * 64},
                {"key": "delivery-objects/12345678/../video.mp4", "expires": "1",
                 "sig": "0" * 64},
                {"key": "delivery-objects/12345678/video.mp4", "expires": str(int(time.time()) + 259201),
                 "sig": "0" * 64},
                {"key": "delivery-objects/12345678/video.mp4", "expires": str(int(time.time()) + 60),
                 "sig": "0" * 64},
            ):
                self.assertEqual(aws_link.handler({"queryStringParameters": params}, None)["statusCode"], 403)
            self.assertEqual(aws_link.handler({"queryStringParameters": {"expires": ["1"]}}, None)["statusCode"], 403)


if __name__ == "__main__":
    unittest.main()
