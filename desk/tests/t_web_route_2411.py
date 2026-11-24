"""Web search uses the selected profile route for every network request."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK.parent / "helene/core"), str(DESK)]
import webtool


class WebRouteTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.profile = Path(tmp) / "profile.json"
        self.enterContext(patch.dict(os.environ, HELENE_CONFIG=str(self.profile)))
        self.save()

    def save(self, **network):
        self.profile.write_text(json.dumps({"network": network}), encoding="utf-8")

    def test_default_bypasses_environment(self):
        with patch.dict(os.environ, HTTPS_PROXY="http://127.0.0.1:1"), \
             patch.object(webtool, "_check_url", return_value="1.1.1.1"), \
             patch("httpx.Client") as client:
            response = client.return_value.__enter__.return_value.stream.return_value.__enter__.return_value
            response.status_code = 200
            response.headers = {}
            response.extensions = {}
            response.iter_bytes.return_value = [b"results"]
            self.assertEqual(webtool._fetch_raw("https://www.python.org/")[1], b"results")
            self.assertFalse(client.call_args.kwargs["trust_env"])

    def test_profile_change_is_read_on_next_request(self):
        url = "https://www.python.org/"
        self.save(web_route="system")
        with patch("urllib.request.getproxies", return_value={"https": "http://localhost:8123"}), \
             patch("urllib.request.proxy_bypass", return_value=False):
            self.assertEqual(webtool._web_proxy(url), "http://localhost:8123")
        self.save(web_route="application", proxy={"mode": "proxy", "host": "localhost", "port": 8222})
        self.assertEqual(webtool._web_proxy(url), "http://localhost:8222")
        self.save(web_route="direct")
        self.assertEqual(webtool._web_proxy(url), "")

    def test_explicit_proxy_receives_checked_address_and_verifies_site_identity(self):
        site = "https://www.python.org/doc/"
        self.save(web_route="application", proxy={"mode": "proxy", "host": "localhost", "port": 8222})
        with patch.object(webtool, "_check_url", return_value="1.1.1.1"), \
             patch("urllib3.ProxyManager") as manager, patch("httpx.Client") as direct:
            response = MagicMock(status=200, headers={"Content-Type": "text/html"})
            response.stream.return_value = [b"public"]
            manager.return_value.request.return_value.__enter__.return_value = response
            self.assertEqual(webtool._fetch_raw(site)[1], b"public")
            self.assertEqual(manager.call_args.kwargs["assert_hostname"], "www.python.org")
            self.assertEqual(manager.call_args.kwargs["server_hostname"], "www.python.org")
            request = manager.return_value.request.call_args
            self.assertEqual(request.args[1], "https://1.1.1.1/doc/")
            self.assertEqual(request.kwargs["headers"]["Host"], "www.python.org")
            direct.assert_not_called()
