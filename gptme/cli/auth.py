"""
Authentication command for gptme providers.

Usage:
    gptme-auth login               # Login to gptme.ai (RFC 8628 Device Flow)
    gptme-auth login --url URL     # Login to a custom gptme instance
    gptme-auth logout              # Remove stored gptme.ai credentials
    gptme-auth status              # Show current login status
    gptme-auth openai-subscription # Authenticate for OpenAI subscription
"""

import json
import logging
import os
import sys
import time
import webbrowser
from pathlib import Path

import click
import httpx
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

# Default gptme.ai service URL
DEFAULT_GPTME_AI_URL = "https://gptme.ai"

# Token storage path (consistent with openai-subscription pattern)
_TOKEN_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gptme" / "auth"
)


def _get_token_path(service_url: str) -> Path:
    """Get the path to store tokens for a given service URL."""
    import hashlib

    url_hash = hashlib.sha256(service_url.encode()).hexdigest()[:12]
    return _TOKEN_DIR / f"gptme-ai-{url_hash}.json"


def _save_token(service_url: str, access_token: str, sub: str | None = None) -> None:
    """Save an access token to disk."""
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    token_path = _get_token_path(service_url)
    data = {
        "service_url": service_url,
        "access_token": access_token,
        "sub": sub,
        "saved_at": time.time(),
    }
    token_path.write_text(json.dumps(data, indent=2))
    token_path.chmod(0o600)
    logger.debug(f"Saved gptme.ai token to {token_path}")


def _load_token(service_url: str) -> dict | None:
    """Load a stored access token, or None if not found."""
    token_path = _get_token_path(service_url)
    if not token_path.exists():
        return None
    try:
        return json.loads(token_path.read_text())
    except Exception:
        return None


def _remove_token(service_url: str) -> bool:
    """Remove a stored token. Returns True if it existed."""
    token_path = _get_token_path(service_url)
    if token_path.exists():
        token_path.unlink()
        return True
    return False


@click.group()
def main():
    """Authenticate with various gptme providers."""


@main.command("login")
@click.option(
    "--url",
    default=DEFAULT_GPTME_AI_URL,
    show_default=True,
    help="gptme service URL to authenticate with.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Don't open the browser automatically.",
)
def auth_login(url: str, no_browser: bool):
    """Login to gptme.ai using RFC 8628 Device Flow.

    Initiates an OAuth Device Authorization Grant flow:

    \b
    1. Requests a device code from the gptme service
    2. Prompts you to visit a URL and enter a code in your browser
    3. Polls until you approve (or the code expires)
    4. Saves the token for future use

    Works great for SSH sessions and headless environments.
    """
    base_url = url.rstrip("/")
    authorize_url = f"{base_url}/api/v1/auth/device/authorize"
    token_url = f"{base_url}/api/v1/auth/device/token"

    console.print(f"\n[bold]Logging in to {base_url}[/bold]\n")

    # Step 1: Request device authorization
    try:
        resp = httpx.post(authorize_url, timeout=15)
        resp.raise_for_status()
    except httpx.ConnectError:
        console.print(f"[red]✗ Could not connect to {base_url}[/red]")
        console.print("  Is the service running? Check your --url argument.")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        console.print(
            f"[red]✗ Authorization request failed: {e.response.status_code}[/red]"
        )
        sys.exit(1)

    data = resp.json()
    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data["verification_uri"]
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
            poll_resp = httpx.post(
                token_url,
                json={
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=15,
            )
        except httpx.ConnectError:
            console.print("\n[red]✗ Lost connection to service[/red]")
            sys.exit(1)

        if poll_resp.status_code == 200:
            token_data = poll_resp.json()
            access_token = token_data["access_token"]
            sub = token_data.get("sub")

            _save_token(base_url, access_token, sub)

            console.print("\n")
            console.print("[green bold]✓ Authorization successful![/green bold]")
            if sub:
                console.print(f"  Logged in as: {sub}")
            console.print(f"  Token saved for {base_url}")
            console.print("\n  You can now use: [cyan]gptme --provider gptme-ai[/cyan]")
            return

        error_data = poll_resp.json()
        error = error_data.get("error", "unknown_error")

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
    default=DEFAULT_GPTME_AI_URL,
    show_default=True,
    help="gptme service URL to log out from.",
)
def auth_logout(url: str):
    """Remove stored credentials for gptme.ai."""
    base_url = url.rstrip("/")
    removed = _remove_token(base_url)
    if removed:
        console.print(f"[green]✓ Logged out from {base_url}[/green]")
    else:
        console.print(f"[yellow]No credentials stored for {base_url}[/yellow]")


@main.command("status")
@click.option(
    "--url",
    default=DEFAULT_GPTME_AI_URL,
    show_default=True,
    help="gptme service URL to check.",
)
def auth_status(url: str):
    """Show current login status for gptme.ai."""
    base_url = url.rstrip("/")
    token_data = _load_token(base_url)

    if not token_data:
        console.print(f"[yellow]Not logged in to {base_url}[/yellow]")
        console.print("  Run: [cyan]gptme-auth login[/cyan]")
        return

    sub = token_data.get("sub", "unknown")
    saved_at = token_data.get("saved_at")
    if saved_at:
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(saved_at, tz=timezone.utc)
        age = f" (saved {dt.strftime('%Y-%m-%d %H:%M UTC')})"
    else:
        age = ""

    console.print(f"[green]✓ Logged in to {base_url}[/green]")
    console.print(f"  User: {sub}{age}")


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


if __name__ == "__main__":
    main()
