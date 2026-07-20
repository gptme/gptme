"""
Authentication command for gptme providers.

Usage:
    gptme-auth login               # Login to gptme cloud (RFC 8628 Device Flow)
    gptme-auth login --url URL     # Login to a custom gptme instance
    gptme-auth logout              # Remove stored gptme credentials
    gptme-auth status              # Show current login status
    gptme-auth openai-subscription # Authenticate for OpenAI subscription
    gptme-auth grok-subscription   # Check SuperGrok subscription auth status
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
    """Check authentication status for your SuperGrok subscription.

    This provider reuses the credentials stored by the grok CLI.
    Run `grok auth login` first if you haven't authenticated yet.
    """
    from pathlib import Path

    grok_auth_path = Path.home() / ".grok" / "auth.json"
    if not grok_auth_path.exists():
        console.print("\n[red bold]✗ No grok CLI credentials found[/red bold]")
        console.print(
            "  Please install the grok CLI and run: [cyan]grok auth login[/cyan]"
        )
        console.print("  Install from: https://x.ai/grok")
        sys.exit(1)

    try:
        from ..llm.llm_grok_subscription import get_auth

        auth = get_auth()
        import time

        remaining_min = (auth.expires_at - time.time()) / 60
        console.print("\n[green bold]✓ Grok subscription authenticated[/green bold]")
        console.print(f"  Token expires in: {remaining_min:.0f} minutes")
        console.print("\nYou can now use: [cyan]grok-subscription/grok-4.5[/cyan]")
    except Exception as e:
        console.print("\n[red bold]✗ Authentication check failed[/red bold]")
        console.print(f"  Error: {e}")
        console.print("  Try running: [cyan]grok auth login[/cyan]")
        sys.exit(1)


if __name__ == "__main__":
    main()
