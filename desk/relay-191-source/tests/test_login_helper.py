"""Offline OAuth callback contracts; never contacts an identity provider."""
import base64
import http.client
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

path = Path(__file__).resolve().parents[1] / "src/login/login_with_chatgpt.py"
spec = importlib.util.spec_from_file_location("relay_login_helper", path)
helper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helper
spec.loader.exec_module(helper)


def jwt(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"e30.{body}.c2ln"


def bundle():
    return helper.AuthBundle(None, helper.TokenData(
        jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "mock-account", "chatgpt_plan_type": "pro"}}),
        "opaque-access-token", "mock-refresh-token", "mock-account"), "2026-01-01T00:00:00Z")


class LoginContracts(unittest.TestCase):
    def test_subscription_signin_exchanges_only_the_authorization_code(self):
        handler = object.__new__(helper._ApiKeyHTTPHandler)
        handler.server = types.SimpleNamespace(token_endpoint="https://identity.invalid/oauth/token",
            redirect_uri="http://localhost:1455/auth/callback", client_id="mock-client",
            pkce=types.SimpleNamespace(code_verifier="mock-verifier"))
        auth = bundle()
        response = dict(id_token=auth.token_data.id_token, access_token=auth.token_data.access_token,
                        refresh_token=auth.token_data.refresh_token)
        with mock.patch.object(helper, "_urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            actual, location = handler._exchange_code("mock-code")
        self.assertEqual(send.call_count, 1)
        self.assertIsNone(actual.api_key)
        self.assertEqual(actual.token_data.account_id, "mock-account")
        self.assertEqual(location, "http://localhost:1455/success")
        self.assertNotIn("id_token", location)

    def test_incomplete_credentials_are_rejected_before_persistence(self):
        handler = object.__new__(helper._ApiKeyHTTPHandler)
        handler.server = types.SimpleNamespace(token_endpoint="https://identity.invalid/token",
            redirect_uri="http://localhost:1455/auth/callback", client_id="mock-client",
            pkce=types.SimpleNamespace(code_verifier="mock-verifier"))
        response = dict(id_token=jwt({}), access_token="opaque", refresh_token="refresh")
        with mock.patch.object(helper, "_urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                handler._exchange_code("mock-code")

    def test_failed_atomic_replace_preserves_old_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / "auth.json"
            auth_file.write_bytes(b"previous-account")
            with mock.patch.object(helper.os, "replace", side_effect=OSError("simulated disk failure")):
                self.assertFalse(helper._write_auth_file(auth=bundle(), codex_home=directory))
            self.assertEqual(auth_file.read_bytes(), b"previous-account")
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["auth.json"])

    def test_bad_callback_or_favicon_does_not_cancel_valid_login(self):
        with tempfile.TemporaryDirectory() as directory:
            server = helper._ApiKeyHTTPServer(("127.0.0.1", 0), helper._ApiKeyHTTPHandler,
                                              codex_home=directory, client_id="mock-client")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            def request(path):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                connection.request("GET", path)
                response = connection.getresponse()
                code, location = response.status, response.getheader("Location")
                response.read(); connection.close()
                return code, location
            try:
                self.assertEqual(request("/success")[0], 400)
                self.assertEqual(request("/favicon.ico")[0], 404)
                self.assertEqual(request("/auth/callback?state=wrong&code=mock")[0], 400)
                self.assertTrue(thread.is_alive())
                with mock.patch.object(helper._ApiKeyHTTPHandler, "_exchange_code",
                                       return_value=(bundle(), f"http://localhost:{port}/success")):
                    code, location = request(f"/auth/callback?state={server.state}&code=mock")
                self.assertEqual(code, 302)
                self.assertNotIn("token", location)
                self.assertEqual(request("/success")[0], 200)
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(json.loads((Path(directory)/"auth.json").read_text())["tokens"]["account_id"], "mock-account")
            finally:
                server.shutdown(); server.server_close(); thread.join(2)


if __name__ == "__main__":
    unittest.main()
