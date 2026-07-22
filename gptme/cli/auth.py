"""
Authentication command for gptme providers.

Usage:
    gptme-auth login               # Login to gptme cloud (RFC 8628 Device Flow)
    gptme-auth login --url URL     # Login to a custom gptme instance
    gptme-auth logout              # Remove stored gptme credentials
    gptme-auth status              # Show current login status
    gptme-auth openai-subscription # Authenticate for OpenAI subscription
    gptme-auth grok-subscription   # Authenticate for SuperGrok subscription
"""

import json
import logging
import sys
import time
import webbrowser

import click
import requests
import requests.exceptions
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


@click.group()
def main():
    """Authenticate with various gptme providers."""


@main.command("login")
@click.option(
    "--url",
    default="https://kpkxgnfpyntahyhckhgm.supabase.co",
    show_default=True,
    help="gptme service URL (used for LLM API and token storage).",
)
@click.option(
    "--auth-url",
    default=None,
    show_default=False,
    help=(
        "Override the device-auth endpoint base URL. "
        "Defaults to the Supabase edge function used by gptme.ai."
    ),
)
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Don't open the browser automatically.",
)
def auth_login(url: str, auth_url: str | None, no_browser: bool):
    """Login to gptme cloud using RFC 8628 Device Flow.

    Initiates an OAuth Device Authorization Grant flow:

    \b
    1. Requests a device code from the gptme service
    2. Prompts you to visit a URL and enter a code in your browser
    3. Polls until you approve (or the code expires)
    4. Saves the token for future use

    Works great for SSH sessions and headless environments.
    """
    from ..llm.llm_gptme import (
        DEFAULT_BASE_URL,
        DEFAULT_DEVICE_AUTH_URL,
        DEFAULT_SERVICE_URL,
    )

    base_url = url.rstrip("/")
    auth_base = (auth_url or DEFAULT_DEVICE_AUTH_URL).rstrip("/")
    authorize_url = f"{auth_base}/authorize"
    token_url = f"{auth_base}/token"

    console.print(f"\n[bold]Logging in to {base_url}[/bold]\n")

    # Step 1: Request device authorization
    try:
        resp = requests.post(authorize_url, json={"client_id": "gptme-cli"}, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        console.print(f"[red]✗ Could not connect to {auth_base}[/red]")
        console.print("  Check your --auth-url argument.")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        console.print(f"[red]✗ Authorization request failed: {status}[/red]")
        sys.exit(1)

    try:
        data = resp.json()
        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
    except (json.JSONDecodeError, KeyError) as e:
        console.print(f"[red]✗ Unexpected response from server: {e}[/red]")
        sys.exit(1)

    verification_uri_complete = data.get(
        "verification_uri_complete", f"{verification_uri}?code={user_code}"
    )
    expires_in = data.get("expires_in", 900)
    interval = data.get("interval", 5)

    # Step 2: Show the user what to do
    console.print("  Open this URL in your browser:")
    console.print(f"\n  [bold cyan]{verification_uri_complete}[/bold cyan]\n")
    console.print(f"  Or go to [cyan]{verification_uri}[/cyan] and enter code:")
    console.print(f"\n  [bold yellow]{user_code}[/bold yellow]\n")
    console.print(f"  Code expires in {expires_in // 60} minutes.\n")

    if not no_browser:
        try:
            webbrowser.open(verification_uri_complete)
            console.print("  [dim](Opened browser automatically)[/dim]\n")
        except Exception:
            pass  # Browser open is best-effort

    console.print("  Waiting for authorization", end="")

    # Step 3: Poll for token
    deadline = time.monotonic() + expires_in
    current_interval = interval

    while time.monotonic() < deadline:
        time.sleep(current_interval)
        console.print(".", end="")  # progress dots while polling

        try:
            poll_resp = requests.post(
                token_url,
                json={
                    "client_id": "gptme-cli",
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=15,
            )
        except requests.exceptions.ConnectionError:
            console.print("\n[red]✗ Lost connection to service[/red]")
            sys.exit(1)

        if poll_resp.status_code == 200:
            try:
                token_data = poll_resp.json()
                access_token = token_data["access_token"]
            except (json.JSONDecodeError, KeyError) as e:
                console.print(f"\n[red]✗ Unexpected token response: {e}[/red]")
                sys.exit(1)
            sub = token_data.get("sub")

            from ..llm.llm_gptme import _save_token

            token_entry: dict = {
                "access_token": access_token,
                "expires_at": time.time() + token_data.get("expires_in", 86400),
                "server_url": base_url,
                "sub": sub,
            }
            # Only store base_url for the default Supabase service.
            # Custom server tokens rely on the server_url+/v1 fallback in get_base_url().
            if base_url == DEFAULT_SERVICE_URL.rstrip("/"):
                token_entry["base_url"] = DEFAULT_BASE_URL
            _save_token(token_entry, base_url)

            console.print("\n")
            console.print("[green bold]✓ Authorization successful![/green bold]")
            if sub:
                console.print(f"  Logged in as: {sub}")
            console.print(f"  Token saved for {base_url}")
            console.print(
                "\n  You can now use: [cyan]gptme -m gptme/claude-sonnet-4-6[/cyan]"
            )
            return

        try:
            error_data = poll_resp.json()
            error = error_data.get("error", "unknown_error")
        except json.JSONDecodeError:
            console.print(
                f"\n[red]✗ Unexpected server response (HTTP {poll_resp.status_code})[/red]"
            )
            sys.exit(1)

        if error == "authorization_pending":
            continue  # normal, keep polling
        if error == "slow_down":
            current_interval = error_data.get("interval", current_interval + 5)
            continue
        if error == "access_denied":
            console.print("\n")
            console.print("[red]✗ Authorization was denied.[/red]")
            sys.exit(1)
        elif error == "expired_token":
            console.print("\n")
            console.print("[red]✗ Device code expired. Please try again.[/red]")
            sys.exit(1)
        else:
            console.print(f"\n[red]✗ Unexpected error: {error}[/red]")
            sys.exit(1)

    console.print("\n")
    console.print("[red]✗ Timed out waiting for authorization.[/red]")
    sys.exit(1)


@main.command("logout")
@click.option(
    "--url",
    default="https://kpkxgnfpyntahyhckhgm.supabase.co",
    show_default=True,
    help="gptme service URL to log out from.",
)
def auth_logout(url: str):
    """Remove stored credentials for gptme cloud."""
    from ..llm.llm_gptme import (
        _LEGACY_SERVICE_URLS,
        DEFAULT_SERVICE_URL,
        _get_token_path,
    )

    base_url = url.rstrip("/")
    token_path = _get_token_path(base_url)
    if token_path.exists():
        token_path.unlink()
        console.print(f"[green]✓ Logged out from {base_url}[/green]")
    elif base_url == DEFAULT_SERVICE_URL:
        # Migration fallback: check legacy service URL paths so users authenticated
        # before the Supabase migration can actually log out.
        for legacy_url in _LEGACY_SERVICE_URLS:
            legacy_path = _get_token_path(legacy_url)
            if legacy_path.exists():
                legacy_path.unlink()
                console.print(
                    f"[green]✓ Logged out (removed legacy token from {legacy_url})[/green]"
                )
                return
        console.print(f"[yellow]No credentials stored for {base_url}[/yellow]")
    else:
        console.print(f"[yellow]No credentials stored for {base_url}[/yellow]")


@main.command("status")
@click.option(
    "--url",
    default="https://kpkxgnfpyntahyhckhgm.supabase.co",
    show_default=True,
    help="gptme service URL to check.",
)
def auth_status(url: str):
    """Show current login status for gptme cloud."""
    from ..llm.llm_gptme import DEFAULT_SERVICE_URL, _load_token

    base_url = url.rstrip("/")
    token_data = _load_token(base_url)
    # Migration fallback: when checking the default Supabase URL and no new
    # token exists, _load_token() (no args) also searches legacy paths like
    # fleet.gptme.ai tokens saved before the Supabase URL migration.
    if not token_data and base_url == DEFAULT_SERVICE_URL:
        token_data = _load_token()

    if not token_data:
        console.print(f"[yellow]Not logged in to {base_url}[/yellow]")
        console.print("  Run: [cyan]gptme-auth login[/cyan]")
        return

    sub = token_data.get("sub", "unknown")
    expires_at = token_data.get("expires_at")
    if expires_at:
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        expiry = f" (expires {dt.strftime('%Y-%m-%d %H:%M UTC')})"
    else:
        expiry = ""

    console.print(f"[green]✓ Logged in to {base_url}[/green]")
    console.print(f"  User: {sub}{expiry}")


@main.command("openai-subscription")
def auth_openai_subscription():
    """Authenticate with OpenAI using your ChatGPT Plus/Pro subscription.

    This opens a browser for you to log in with your OpenAI account.
    After successful login, tokens are stored locally for future use.
    """
    try:
        from ..llm.llm_openai_subscription import oauth_authenticate

        console.print("\n[bold]OpenAI Subscription Authentication[/bold]\n")
        console.print("This will open your browser to log in with your OpenAI account.")
        console.print(
            "Your ChatGPT Plus/Pro subscription will be used for API access.\n"
        )

        auth = oauth_authenticate()

        console.print("\n[green bold]✓ Authentication successful![/green bold]")
        console.print(f"  Account ID: {auth.account_id[:20]}...")
        console.print(
            "\nYou can now use models like: [cyan]openai-subscription/gpt-5.2[/cyan]"
        )

    except Exception as e:
        console.print("\n[red bold]✗ Authentication failed[/red bold]")
        console.print(f"  Error: {e}")
        logger.debug("Full error:", exc_info=True)
        sys.exit(1)


@main.command("grok-subscription")
def auth_grok_subscription():
    """Authenticate with xAI using your SuperGrok subscription.

    If you have the grok CLI installed and have run ``grok login``, gptme
    will automatically reuse those tokens — no extra steps needed.

    If you don't have the grok CLI, this command guides you through the same
    OAuth flow to obtain tokens stored at ~/.config/gptme/oauth/grok_subscription.json.
    """
    from ..llm.llm_grok_subscription import (
        OAUTH_AUTH_URL,
        OAUTH_CALLBACK_PORT,
        OAUTH_CLIENT_ID,
        OAUTH_SCOPES,
        OAUTH_TOKEN_URL,
        _get_grok_cli_auth_path,
        _load_grok_cli_tokens,
        _save_tokens,
    )

    # If grok CLI tokens already exist and are valid, skip the OAuth flow
    cli_auth = _load_grok_cli_tokens()
    if cli_auth is not None:
        import time

        if time.time() < cli_auth.expires_at - 300:
            console.print("\n[bold]Grok Subscription Authentication[/bold]\n")
            console.print(
                f"[green]✓ Found valid grok CLI tokens at {_get_grok_cli_auth_path()}[/green]"
            )
            console.print(
                "\nYou can now use models like: [cyan]grok-subscription/grok-4.5[/cyan]"
            )
            return

    # Full OAuth PKCE flow (for users without grok CLI)
    import base64
    import hashlib
    import http.server
    import secrets
    import threading
    import time
    import webbrowser
    from urllib.parse import parse_qs, urlencode, urlparse

    console.print("\n[bold]Grok Subscription Authentication[/bold]\n")
    console.print("This will open your browser to log in with your xAI account.")
    console.print("Your SuperGrok subscription will be used for API access.\n")

    # PKCE
    code_verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)

    callback_url = f"http://localhost:{OAUTH_CALLBACK_PORT}/auth/callback"

    auth_params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{OAUTH_AUTH_URL}?{urlencode(auth_params)}"

    # Local callback server
    result: dict = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            received_state = params.get("state", [None])[0]
            if received_state != state:
                result["error"] = "Invalid state (possible CSRF)"
                self._respond(400, "Security error: invalid state.")
                return
            if "code" in params:
                result["code"] = params["code"][0]
                self._respond(
                    200,
                    "Authentication successful. You can close this window.",
                )
            elif "error" in params:
                result["error"] = params.get("error_description", params["error"])[0]
                self._respond(400, f"Error: {result['error']}")

        def _respond(self, status, msg):
            body = f"<html><body><p>{msg}</p></body></html>".encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    try:
        server = http.server.HTTPServer(("127.0.0.1", OAUTH_CALLBACK_PORT), _Handler)
        server.timeout = 120
    except OSError as e:
        console.print(
            f"[red]✗ Could not start callback server on port {OAUTH_CALLBACK_PORT}: {e}[/red]"
        )
        sys.exit(1)

    console.print("   Opening browser for xAI authentication...")
    console.print(f"   If browser doesn't open, visit:\n   {auth_url}")

    def _open():
        time.sleep(0.5)
        webbrowser.open(auth_url)

    threading.Thread(target=_open, daemon=True).start()
    console.print(f"   Waiting for callback on port {OAUTH_CALLBACK_PORT}...")

    _auth_deadline = time.time() + 300  # 5-minute overall timeout
    try:
        while "code" not in result and "error" not in result:
            if time.time() > _auth_deadline:
                console.print(
                    "\n[red bold]✗ Authentication timed out after 5 minutes.[/red bold]"
                )
                sys.exit(1)
            server.handle_request()
    finally:
        server.server_close()

    if "error" in result:
        console.print(
            f"\n[red bold]✗ Authentication failed: {result['error']}[/red bold]"
        )
        sys.exit(1)

    console.print("   Exchanging authorization code for tokens...")
    try:
        token_resp = requests.post(
            OAUTH_TOKEN_URL,
            data={
                "client_id": OAUTH_CLIENT_ID,
                "grant_type": "authorization_code",
                "code": result["code"],
                "redirect_uri": callback_url,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if token_resp.status_code != 200:
            raise ValueError(
                f"Token exchange failed: {token_resp.status_code} {token_resp.text[:200]}"
            )
        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not access_token:
            raise ValueError("No access token in response")
        expires_in = tokens.get("expires_in", 21600)

        from ..llm.llm_grok_subscription import SubscriptionAuth

        auth = SubscriptionAuth(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
        )
        _save_tokens(auth)
    except Exception as e:
        console.print(f"\n[red bold]✗ Token exchange failed: {e}[/red bold]")
        sys.exit(1)

    console.print("\n[green bold]✓ Authentication successful![/green bold]")
    console.print(
        "\nYou can now use models like: [cyan]grok-subscription/grok-4.5[/cyan]"
    )


@main.command("openrouter")
def auth_openrouter():
    """Authenticate with OpenRouter via PKCE OAuth and store a permanent API key.

    Opens a browser so you can sign in to openrouter.ai.  After successful
    sign-in, a permanent API key (``sk-or-v1-…``) is exchanged and saved to
    the gptme config as ``env.OPENROUTER_API_KEY``.

    You can then use any model on OpenRouter::

        gptme --model openrouter/anthropic/claude-sonnet-4-6 "Hello"
    """
    try:
        from ..llm.llm_openrouter_subscription import oauth_get_api_key
    except ImportError as exc:
        console.print(f"[red]✗ Could not import OpenRouter OAuth module: {exc}[/red]")
        sys.exit(1)

    console.print("\n[bold]OpenRouter Authentication[/bold]\n")
    console.print("This will open your browser to sign in to openrouter.ai.")
    console.print(
        "After sign-in, a permanent API key will be created and saved to your"
        " gptme config.\n"
    )

    try:
        api_key = oauth_get_api_key()
    except RuntimeError as exc:
        console.print(f"\n[red bold]✗ Authentication failed: {exc}[/red bold]")
        sys.exit(1)

    # Persist to config
    from ..config import set_config_value

    set_config_value("env.OPENROUTER_API_KEY", api_key, local=True)

    console.print("\n[green bold]✓ Authentication successful![/green bold]")
    console.print(f"  API key saved to config (key: {api_key[:16]}…)")
    console.print(
        "\nYou can now use any OpenRouter model, for example:\n"
        "  [cyan]gptme --model openrouter/anthropic/claude-sonnet-4-6 'Hello'[/cyan]"
    )


if __name__ == "__main__":
    main()
