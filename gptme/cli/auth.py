"""
Authentication command for gptme providers.

Usage:
    gptme-auth openai-subscription    # Authenticate for OpenAI subscription
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
    pass


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
