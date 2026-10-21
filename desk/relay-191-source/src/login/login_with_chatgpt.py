"""Script that spawns a local webserver for retrieving an OpenAI API key.

- Listens on 127.0.0.1:1455
- Opens http://localhost:1455/auth/callback in the browser
- If the user successfully navigates the auth flow,
  $CODEX_HOME/auth.json will be written with the API key.
- User will be redirected to http://localhost:1455/success upon success.

The script should exit with a non-zero code if the user fails to navigate the
auth flow.

To test this script locally without overwriting your existing auth.json file:

```
rm -rf /tmp/codex_home && mkdir /tmp/codex_home
CODEX_HOME=/tmp/codex_home python3 codex-rs/login/src/login_with_chatgpt.py
```
"""

from __future__ import annotations

import argparse
import base64
import datetime
import errno
import hashlib
import http.server
import io
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from typing import Any, Dict  # for type hints

# Required port for OAuth client.
REQUIRED_PORT = 1455
URL_BASE = f"http://localhost:{REQUIRED_PORT}"
DEFAULT_ISSUER = "https://auth.openai.com"

EXIT_CODE_WHEN_ADDRESS_ALREADY_IN_USE = 13

CA_CONTEXT = None
try:
    import ssl
    import certifi as _certifi

    CA_CONTEXT = ssl.create_default_context(cafile=_certifi.where())
except Exception:
    pass


def _urlopen(request, *, context=None, timeout=30):
    mode = os.getenv('RELAY_NETWORK_MODE', 'system')
    if mode not in ('system', 'direct', 'proxy'):
        raise ValueError('Invalid relay network mode')
    if mode != 'proxy':
        handlers = [urllib.request.HTTPSHandler(context=context)]
        if mode == 'direct':
            handlers.append(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener(*handlers).open(request, timeout=timeout)
    import urllib3
    proxy = os.getenv('RELAY_PROXY_URL', '')
    parsed = urllib.parse.urlsplit(proxy)
    if parsed.scheme not in ('http', 'https', 'socks5', 'socks5h') or not parsed.hostname:
        raise ValueError('Selected proxy is invalid')
    manager = None
    try:
        if parsed.scheme.startswith('socks5'):
            from urllib3.contrib.socks import SOCKSProxyManager
            manager = SOCKSProxyManager(proxy, ssl_context=context)
        else:
            host = '[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname
            clean = parsed.scheme + '://' + host + (':' + str(parsed.port) if parsed.port else '')
            auth = {}
            if parsed.username is not None:
                pair = urllib.parse.unquote(parsed.username) + ':' + urllib.parse.unquote(parsed.password or '')
                auth = urllib3.make_headers(proxy_basic_auth=pair)
            manager = urllib3.ProxyManager(clean, proxy_headers=auth, ssl_context=context, proxy_ssl_context=context)
        response = manager.request(request.get_method(), request.full_url, body=request.data,
            headers=dict(request.header_items()), redirect=False, retries=False,
            timeout=urllib3.Timeout(connect=10, read=timeout))
        data, status, headers = response.data, response.status, response.headers
    except Exception:
        raise urllib.error.URLError('Selected proxy connection failed') from None
    finally:
        if manager is not None:
            manager.clear()
    if not 200 <= status < 300:
        raise urllib.error.HTTPError(request.full_url, status, 'OAuth HTTP error', headers, io.BytesIO(data))
    return io.BytesIO(data)


@dataclass
class TokenData:
    id_token: str
    access_token: str
    refresh_token: str
    account_id: str


@dataclass
class AuthBundle:
    """Aggregates authentication data produced after successful OAuth flow."""

    api_key: str | None
    token_data: TokenData
    last_refresh: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve API key via local HTTP flow")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the browser",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable request logging")
    args = parser.parse_args()

    codex_home = os.environ.get("CODEX_HOME")
    if not codex_home:
        eprint("ERROR: CODEX_HOME environment variable is not set")
        sys.exit(1)

    client_id = os.getenv("CODEX_CLIENT_ID")
    if not client_id:
        eprint("ERROR: CODEX_CLIENT_ID environment variable is not set")
        sys.exit(1)

    # Spawn server.
    try:
        httpd = _ApiKeyHTTPServer(
            ("127.0.0.1", REQUIRED_PORT),
            _ApiKeyHTTPHandler,
            codex_home=codex_home,
            client_id=client_id,
            verbose=args.verbose,
        )
    except OSError as e:
        eprint(f"ERROR: {e}")
        if e.errno == errno.EADDRINUSE:
            # Caller might want to handle this case specially.
            sys.exit(EXIT_CODE_WHEN_ADDRESS_ALREADY_IN_USE)
        else:
            sys.exit(1)

    auth_url = httpd.auth_url()

    with httpd:
        eprint(f"Starting local login server on {URL_BASE}")
        if not args.no_browser:
            try:
                webbrowser.open(auth_url, new=1, autoraise=True)
            except Exception as e:
                eprint(f"Failed to open browser: {e}")

        eprint(
            f". If your browser did not open, navigate to this URL to authenticate: \n\n{auth_url}"
        )

        # Run the server in the main thread until `shutdown()` is called by the
        # request handler.
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            eprint("\nKeyboard interrupt received, exiting.")

        # Server has been shut down by the request handler. Exit with the code
        # it set (0 on success, non-zero on failure).
        sys.exit(httpd.exit_code)


class _ApiKeyHTTPHandler(http.server.BaseHTTPRequestHandler):
    """A minimal request handler that captures an *api key* from query/post."""

    # We store the result in the server instance itself.
    server: "_ApiKeyHTTPServer"  # type: ignore[override]  - helpful annotation

    def do_GET(self) -> None:  # noqa: N802 – required by BaseHTTPRequestHandler
        path = urllib.parse.urlparse(self.path).path

        if path == "/success":
            if self.server.exit_code != 0:
                self.send_error(400, "Login has not completed")
                return
            # Serve confirmation page then gracefully shut down the server so
            # the main thread can exit with the previously captured exit code.
            self._send_html(LOGIN_SUCCESS_HTML)

            # Ensure the data is flushed to the client before we stop.
            try:
                self.wfile.flush()
            except Exception as e:
                eprint(f"Failed to flush response: {e}")

            self.request_shutdown()
        elif path == "/auth/callback":
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)

            # Validate state -------------------------------------------------
            if params.get("state", [None])[0] != self.server.state:
                self.send_error(400, "State parameter mismatch")
                return

            # Standard OAuth flow -----------------------------------------
            code = params.get("code", [None])[0]
            if not code:
                self.send_error(400, "Missing authorization code")
                return

            try:
                auth_bundle, success_url = self._exchange_code(code)
            except Exception as exc:  # noqa: BLE001 – propagate to client
                self.send_error(500, f"Token exchange failed: {exc}")
                return

            # Persist API key along with additional token metadata.
            if _write_auth_file(
                auth=auth_bundle,
                codex_home=self.server.codex_home,
            ):
                self.server.exit_code = 0
                self._send_redirect(success_url)
            else:
                self.send_error(500, "Unable to persist auth file")
        else:
            self.send_error(404, "Endpoint not supported")

    def do_POST(self) -> None:  # noqa: N802 – required by BaseHTTPRequestHandler
        self.send_error(404, "Endpoint not supported")

    def send_error(self, code, message=None, explain=None) -> None:
        """Send an error response and stop the server.

        We avoid calling `sys.exit()` directly from the request-handling thread
        so that the response has a chance to be written to the socket. Instead
        we shut the server down; the main thread will then exit with the
        appropriate status code.
        """
        super().send_error(code, message, explain)
        try:
            self.wfile.flush()
        except Exception as e:
            eprint(f"Failed to flush response: {e}")

        if code >= 500:
            self.request_shutdown()

    def _send_redirect(self, url: str) -> None:
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _send_html(self, body: str) -> None:
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    # Silence logging for cleanliness unless --verbose flag is used.
    def log_message(self, fmt: str, *args):  # type: ignore[override]
        if getattr(self.server, "verbose", False):  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    def _exchange_code(self, code: str) -> tuple[AuthBundle, str]:
        """Exchange the authorization code for subscription tokens.

        Returns (AuthBundle, success_url).
        """

        # 1. Authorization-code -> (id_token, access_token, refresh_token)
        data = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.server.redirect_uri,
                "client_id": self.server.client_id,
                "code_verifier": self.server.pkce.code_verifier,
            }
        ).encode()

        token_data: TokenData

        with _urlopen(
            urllib.request.Request(
                self.server.token_endpoint,
                data=data,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ),
            context=CA_CONTEXT, timeout=30,
        ) as resp:
            payload = json.loads(resp.read().decode())

            # Extract chatgpt_account_id from id_token
            id_token_parts = payload["id_token"].split(".")
            if len(id_token_parts) != 3:
                raise ValueError("Invalid ID token")
            id_token_claims = _decode_jwt_segment(id_token_parts[1])
            auth_claims = id_token_claims.get("https://api.openai.com/auth", {})
            chatgpt_account_id = auth_claims.get("chatgpt_account_id", "")

            token_data = TokenData(
                id_token=payload["id_token"],
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                account_id=chatgpt_account_id,
            )

        # Subscription sign-in never creates a Platform API key or redeems
        # billing credits. A successful callback also never puts tokens in a URL.
        access_claims = (parse_id_token_claims(token_data.access_token) or {}).get(
            "https://api.openai.com/auth", {})
        token_data.account_id = token_data.account_id or access_claims.get("chatgpt_account_id", "")
        if not token_data.account_id or not token_data.access_token or not token_data.refresh_token:
            raise ValueError("Incomplete ChatGPT subscription credentials")

        # Persist refresh_token/id_token for future use (redeem credits etc.)
        last_refresh_str = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

        auth_bundle = AuthBundle(
            api_key=None,
            token_data=token_data,
            last_refresh=last_refresh_str,
        )

        return (auth_bundle, f"{URL_BASE}/success")

    def request_shutdown(self) -> None:
        # shutdown() must be invoked from another thread to avoid
        # deadlocking the serve_forever() loop, which is running in this
        # same thread. A short-lived helper thread does the trick.
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def _write_auth_file(*, auth: AuthBundle, codex_home: str) -> bool:
    """Persist *api_key* to $CODEX_HOME/auth.json.

    Returns True on success, False otherwise.  Any error is printed to
    *stderr* so that the Rust layer can surface the problem.
    """
    if not os.path.isdir(codex_home):
        try:
            os.makedirs(codex_home, exist_ok=True)
        except Exception as exc:  # pragma: no cover – unlikely
            eprint(f"ERROR: unable to create CODEX_HOME directory: {exc}")
            return False

    auth_path = os.path.join(codex_home, "auth.json")
    auth_json_contents = {
        "OPENAI_API_KEY": auth.api_key,
        "tokens": {
            "id_token": auth.token_data.id_token,
            "access_token": auth.token_data.access_token,
            "refresh_token": auth.token_data.refresh_token,
            "account_id": auth.token_data.account_id,
        },
        "last_refresh": auth.last_refresh,
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=codex_home,
                                         prefix=".auth-", delete=False) as fp:
            temporary = fp.name
            if hasattr(os, "fchmod"):
                os.fchmod(fp.fileno(), 0o600)
            json.dump(auth_json_contents, fp, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temporary, auth_path)
    except Exception as exc:
        eprint(f"ERROR: unable to write auth file: {exc}")
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        return False

    return True


@dataclass
class PkceCodes:
    code_verifier: str
    code_challenge: str


class _ApiKeyHTTPServer(http.server.HTTPServer):
    """HTTPServer with shutdown helper & self-contained OAuth configuration."""

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[http.server.BaseHTTPRequestHandler],
        *,
        codex_home: str,
        client_id: str,
        verbose: bool = False,
    ) -> None:
        super().__init__(server_address, request_handler_class, bind_and_activate=True)

        self.exit_code = 1
        self.codex_home = codex_home
        self.verbose: bool = verbose

        self.issuer: str = DEFAULT_ISSUER
        self.token_endpoint: str = f"{self.issuer}/oauth/token"
        self.client_id: str = client_id
        port = self.server_port
        self.redirect_uri: str = f"http://localhost:{port}/auth/callback"
        self.pkce: PkceCodes = _generate_pkce()
        self.state: str = secrets.token_hex(32)

    def auth_url(self) -> str:
        """Return fully-formed OpenID authorization URL."""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid profile email offline_access",
            "code_challenge": self.pkce.code_challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": self.state,
        }
        return f"{self.issuer}/oauth/authorize?" + urllib.parse.urlencode(params)


def _generate_pkce() -> PkceCodes:
    """Generate PKCE *code_verifier* and *code_challenge* (S256)."""
    code_verifier = secrets.token_hex(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PkceCodes(code_verifier, code_challenge)


def eprint(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)


# Parse ID-token claims (if provided)
#
# interface IDTokenClaims {
#   "exp": number; // specifically, an int
#   "https://api.openai.com/auth": {
#     organization_id: string;
#     project_id: string;
#     completed_platform_onboarding: boolean;
#     is_org_owner: boolean;
#     chatgpt_subscription_active_start: string;
#     chatgpt_subscription_active_until: string;
#     chatgpt_plan_type: string;
#   };
# }
def parse_id_token_claims(id_token: str) -> Dict[str, Any] | None:
    if id_token:
        parts = id_token.split(".")
        if len(parts) == 3:
            return _decode_jwt_segment(parts[1])
    return None


def _decode_jwt_segment(segment: str) -> Dict[str, Any]:
    """Return the decoded JSON payload from a JWT segment.

    Adds required padding for urlsafe_b64decode.
    """
    padded = segment + "=" * (-len(segment) % 4)
    try:
        data = base64.urlsafe_b64decode(padded.encode())
        return json.loads(data.decode())
    except Exception:
        return {}


def _current_timestamp_ms() -> int:
    return int(time.time() * 1000)


LOGIN_SUCCESS_HTML = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Praxis Relay - signed in</title><style>body{font:17px system-ui;background:#f4f2fa;color:#43385a;display:grid;place-items:center;min-height:90vh}main{background:white;padding:48px;border-radius:18px}h1{font-size:26px}p{color:#8a8098}</style>
<main><h1>You're signed in to Praxis Relay.</h1><p>You can close this tab and return to the app.</p></main></html>"""


if __name__ == "__main__":
    main()
