"""
Authentication command for gptme providers.

Usage:
    gptme-auth gptme-ai               # Authenticate with gptme.ai (Device Flow)
    gptme-auth openai-subscription     # Authenticate for OpenAI subscription
"""

import logging
import sys

import click
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


@click.group()
def main():
    """Authenticate with various gptme providers."""


@main.command("gptme-ai")
@click.option(
    "--server",
    default="https://api.gptme.ai",
    help="gptme.ai server URL",
    show_default=True,
)
def auth_gptme_ai(server: str):
    """Authenticate with gptme.ai using RFC 8628 Device Flow.

    Opens a verification URL where you sign in, then polls for completion.
    Tokens are stored locally for future use.
    """
    try:
        from ..llm.llm_gptme_ai import device_flow_authenticate

        console.print("\n[bold]gptme.ai Authentication[/bold]\n")
        console.print(
            "This will start a Device Flow: you'll get a code to enter in your browser.\n"
        )

        result = device_flow_authenticate(server)

        console.print("\n[green bold]✓ Authentication successful![/green bold]")
        console.print(f"  Server: {result.get('server_url', server)}")
        console.print(
            "\nYou can now use models like: [cyan]gptme-ai/claude-sonnet-4-6[/cyan]"
        )

    except Exception as e:
        console.print("\n[red bold]✗ Authentication failed[/red bold]")
        console.print(f"  Error: {e}")
        logger.debug("Full error:", exc_info=True)
        sys.exit(1)


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
