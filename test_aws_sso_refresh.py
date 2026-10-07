"""Exercise botocore SSO refresh through the app's real client factory."""

import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest import mock

import aws_render
import suno_studio as app

try:
    import botocore
    from botocore.credentials import SSOProvider
    from botocore.session import Session as BotocoreSession
    from botocore.tokens import SSOTokenProvider
    from botocore.utils import JSONFileCache, SSOTokenLoader
except ImportError:
    botocore = None


_START_URL = "https://sso.example.invalid/start"
_SESSION_NAME = "synthetic-session"
_PROFILE = "synthetic"
_OLD_ACCESS_TOKEN = "synthetic-old-sso-access-token"
_NEW_ACCESS_TOKEN = "synthetic-new-sso-access-token"
_REFRESH_TOKEN = "synthetic-refresh-token"
_ROLE_KEYS = ("SYNTHETICROLEKEY0001", "SYNTHETICROLEKEY0002")
_ROLE_TOKENS = ("synthetic-role-session-token-1", "synthetic-role-session-token-2")
_APP_SETTINGS = {
    "profile": _PROFILE,
    "region": "us-east-1",
    "bucket": "synthetic-bucket",
    "cluster": "synthetic-cluster",
    "render_task": "synthetic-render",
    "delivery_task": "synthetic-delivery",
    "subnets": ["subnet-synthetic"],
    "security_group": "sg-synthetic",
}


class _State:
    def __init__(self):
        self.invalid_grant = False
        self.oidc_refresh_requests = []
        self.sso_access_token_versions = []
        self.signed_requests = []
        self.unexpected_requests = []


class _AwsHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    @property
    def state(self):
        return self.server.state

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def _send(self, status, body, content_type, error_code=None):
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if error_code:
            self.send_header("x-amzn-errortype", error_code)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        if urlsplit(self.path).path != "/federation/credentials":
            self.state.unexpected_requests.append("GET")
            return self._send(404, {"message": "unexpected GET"}, "application/json")

        token = self.headers.get("x-amz-sso_bearer_token", "")
        self.state.sso_access_token_versions.append(
            "new" if token == _NEW_ACCESS_TOKEN else
            "old" if token == _OLD_ACCESS_TOKEN else "other"
        )
        if self.state.invalid_grant:
            return self._send(401, {"message": "synthetic session expired"},
                              "application/x-amz-json-1.1", "UnauthorizedException")

        index = len(self.state.sso_access_token_versions) - 1
        expiration = int((time.time() + (300 if index == 0 else 3600)) * 1000)
        credentials = {
            "accessKeyId": _ROLE_KEYS[min(index, 1)],
            "secretAccessKey": f"synthetic-role-secret-key-{index + 1:04d}-00000000000000000000",
            "sessionToken": _ROLE_TOKENS[min(index, 1)],
            "expiration": expiration,
        }
        self._send(200, {"roleCredentials": credentials}, "application/x-amz-json-1.1")

    def do_POST(self):
        path = urlsplit(self.path).path
        body = self._body()
        if path == "/token":
            request = json.loads(body or b"{}")
            self.state.oidc_refresh_requests.append(
                request.get("grantType") == "refresh_token"
                and request.get("refreshToken") == _REFRESH_TOKEN
            )
            if self.state.invalid_grant:
                return self._send(400, {"message": "synthetic refresh grant expired"},
                                  "application/json", "InvalidGrantException")
            return self._send(200, {
                "accessToken": _NEW_ACCESS_TOKEN,
                "tokenType": "Bearer",
                "expiresIn": 3600,
                "refreshToken": "synthetic-next-refresh-token",
            }, "application/json")

        if parse_qs(body.decode(errors="replace")).get("Action") == ["GetCallerIdentity"]:
            authorization = self.headers.get("Authorization", "")
            credential = ""
            if "Credential=" in authorization:
                credential = authorization.split("Credential=", 1)[1].split("/", 1)[0]
            session_token = self.headers.get("X-Amz-Security-Token", "")
            self.state.signed_requests.append((
                "first" if credential == _ROLE_KEYS[0] else
                "second" if credential == _ROLE_KEYS[1] else "other",
                "first" if session_token == _ROLE_TOKENS[0] else
                "second" if session_token == _ROLE_TOKENS[1] else "other",
            ))
            response = (
                '<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
                '<GetCallerIdentityResult><Arn>arn:aws:iam::123456789012:user/synthetic</Arn>'
                '<UserId>SYNTHETICUSER</UserId><Account>123456789012</Account>'
                '</GetCallerIdentityResult><ResponseMetadata><RequestId>synthetic-request</RequestId>'
                '</ResponseMetadata></GetCallerIdentityResponse>'
            ).encode()
            return self._send(200, response, "text/xml")

        self.state.unexpected_requests.append("POST")
        self._send(404, {"message": "unexpected POST"}, "application/json")


@unittest.skipIf(botocore is None, "botocore is required for the SSO refresh test")
class AwsSsoRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config"
        self.credentials_path = self.root / "credentials"
        self.cache_path = self.root / "sso-cache"
        self.credentials_path.write_text("")

        isolated_env = {key: value for key, value in os.environ.items()
                        if not key.startswith("AWS_")}
        isolated_env.update({
            "AWS_CONFIG_FILE": str(self.config_path),
            "AWS_SHARED_CREDENTIALS_FILE": str(self.credentials_path),
            "AWS_PROFILE": _PROFILE,
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_EC2_METADATA_DISABLED": "true",
        })
        self.env_patch = mock.patch.dict(os.environ, isolated_env, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        self.state = _State()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _AwsHandler)
        self.server.state = self.state
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.endpoint_url = f"http://127.0.0.1:{self.server.server_port}"

        self._create_client = BotocoreSession.create_client
        original_create_client = self._create_client

        def create_client(session, *args, **kwargs):
            kwargs["endpoint_url"] = self.endpoint_url
            return original_create_client(session, *args, **kwargs)

        self.client_patch = mock.patch.object(BotocoreSession, "create_client",
                                              new=create_client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

        self.cache_patches = [
            mock.patch.object(SSOProvider, "_SSO_TOKEN_CACHE_DIR", str(self.cache_path)),
            mock.patch.object(SSOTokenProvider, "_SSO_TOKEN_CACHE_DIR", str(self.cache_path)),
        ]
        for patcher in self.cache_patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def _write_profile(self, modern=True):
        if modern:
            profile = (
                "[profile synthetic]\n"
                "sso_session = synthetic-session\n"
                "sso_account_id = 123456789012\n"
                "sso_role_name = SyntheticRole\n"
                "region = us-east-1\n\n"
                "[sso-session synthetic-session]\n"
                f"sso_start_url = {_START_URL}\n"
                "sso_region = us-east-1\n"
                "sso_registration_scopes = sso:account:access\n"
            )
        else:
            profile = (
                "[profile synthetic]\n"
                f"sso_start_url = {_START_URL}\n"
                "sso_region = us-east-1\n"
                "sso_account_id = 123456789012\n"
                "sso_role_name = SyntheticRole\n"
                "region = us-east-1\n"
            )
        self.config_path.write_text(profile)

    def _write_token(self, expires_at, modern=True):
        self.cache_path.mkdir(parents=True, exist_ok=True)
        token = {
            "startUrl": _START_URL,
            "region": "us-east-1",
            "accessToken": _OLD_ACCESS_TOKEN,
            "expiresAt": expires_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if modern:
            token.update({
                "clientId": "synthetic-client-id",
                "clientSecret": "synthetic-client-secret",
                "refreshToken": _REFRESH_TOKEN,
                "registrationExpiresAt": (datetime.now(timezone.utc) + timedelta(days=1))
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        cache = JSONFileCache(str(self.cache_path))
        SSOTokenLoader(cache=cache).save_token(
            _START_URL, token, session_name=_SESSION_NAME if modern else None)

    def test_long_lived_app_client_signs_again_after_sso_token_and_role_refresh(self):
        self._write_profile(modern=True)
        self._write_token(datetime.now(timezone.utc) + timedelta(minutes=20), modern=True)

        client = aws_render._client("sts", "us-east-1", _PROFILE)
        provider = client._request_signer._credentials
        self.assertEqual(provider.method, "sso")

        self.assertEqual(client.get_caller_identity()["Account"], "123456789012")

        future = datetime.now(timezone.utc) + timedelta(minutes=15)
        provider._time_fetcher = lambda: future
        provider._refresh_using.__self__._token_provider._now = lambda: future
        self.assertEqual(client.get_caller_identity()["Account"], "123456789012")

        self.assertEqual(self.state.oidc_refresh_requests, [True])
        self.assertEqual(self.state.sso_access_token_versions, ["old", "new"])
        self.assertEqual(self.state.signed_requests, [("first", "first"), ("second", "second")])
        self.assertEqual(self.state.unexpected_requests, [])
        self.assertIs(client._request_signer._credentials, provider)

    def test_invalid_grant_after_expiry_fails_and_app_status_requests_signin(self):
        self._write_profile(modern=True)
        self._write_token(datetime.now(timezone.utc) - timedelta(minutes=1), modern=True)
        self.state.invalid_grant = True
        client = aws_render._client("sts", "us-east-1", _PROFILE)
        with self.assertRaises(botocore.exceptions.TokenRetrievalError):
            client.get_caller_identity()

        with mock.patch.object(app, "aws_settings", return_value=_APP_SETTINGS), \
                mock.patch.dict(app.CONFIG, {"render_backend": "aws",
                                             "aws_account_id": "123456789012"}):
            self.assertEqual(app.aws_signin_status(), "signin")

        self.assertEqual(self.state.oidc_refresh_requests, [True, True])
        self.assertEqual(self.state.sso_access_token_versions, [])
        self.assertEqual(self.state.signed_requests, [])
        self.assertEqual(self.state.unexpected_requests, [])

    def test_legacy_sso_profile_cannot_refresh_expired_access_token(self):
        self._write_profile(modern=False)
        self._write_token(datetime.now(timezone.utc) - timedelta(minutes=1), modern=False)
        with mock.patch.object(app, "aws_settings", return_value=_APP_SETTINGS), \
                mock.patch.dict(app.CONFIG, {"render_backend": "aws",
                                             "aws_account_id": "123456789012"}):
            self.assertEqual(app.aws_signin_status(), "signin")

        self.assertEqual(self.state.oidc_refresh_requests, [])
        self.assertEqual(self.state.sso_access_token_versions, [])
        self.assertEqual(self.state.signed_requests, [])
        self.assertEqual(self.state.unexpected_requests, [])


if __name__ == "__main__":
    unittest.main()
