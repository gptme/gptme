"""Elicitation hook system for requesting structured user input.

Elicitation is the process by which gptme (as agent) requests input from the
user in structured ways beyond simple text prompts. This module provides:

1. ``ElicitationRequest`` - Describes what the agent needs from the user
2. ``ElicitationResponse`` - Contains the user's response
3. ``ElicitationHook`` - Protocol for elicitation backends
4. ``elicit()`` - The main function for requesting user input

Elicitation types:
- ``text``: Free-form text input (default chat loop behavior)
- ``choice``: Single selection from a list of options
- ``multi_choice``: Multiple selections from a list of options
- ``secret``: Hidden input that is NOT added to LLM conversation context
- ``confirmation``: Simple yes/no question
- ``form``: Multiple fields collected in one interaction

Usage::

    from gptme.hooks.elicitation import elicit, ElicitationRequest

    # Ask for a secret (e.g. API key) without leaking it into LLM context
    response = elicit(ElicitationRequest(
        type="secret",
        prompt="Enter your OpenAI API key:",
    ))
    if not response.cancelled:
        store_api_key(response.value)

    # Ask user to choose from options
    response = elicit(ElicitationRequest(
        type="choice",
        prompt="Which framework should we use?",
        options=["FastAPI", "Django", "Flask"],
    ))
    if not response.cancelled:
        print(f"User chose: {response.value}")

Relationship to confirmations:
    Confirmations (``tool.confirm`` hooks) are a specialized form of elicitation
    used specifically for tool execution decisions. They share hook machinery but
    have different semantics and the ``ConfirmationResult`` type.

    This elicitation system is for *agent-initiated* requests where the agent
    needs information to proceed (API keys, user preferences, form data, etc.).

MCP Elicitation:
    MCP elicitation (``elicitation/requestInput``) is *server-initiated* - an
    MCP server requests input from gptme as the MCP client. That is a separate
    flow handled in the MCP client module. This module handles *gptme-native*
    agent-initiated elicitation.
"""

import getpass
import logging
from dataclasses import dataclass
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

ElicitationType = Literal[
    "text", "choice", "multi_choice", "secret", "confirmation", "form"
]


@dataclass
class FormField:
    """A single field in a form elicitation."""

    name: str
    prompt: str
    type: Literal["text", "secret", "choice", "boolean", "number"] = "text"
    options: list[str] | None = None  # For "choice" type
    required: bool = True
    default: str | None = None


@dataclass
class ElicitationRequest:
    """A request for structured user input.

    Attributes:
        type: The type of elicitation (text, choice, multi_choice, secret,
              confirmation, form).
        prompt: The question or instruction to show the user.
        options: For ``choice`` and ``multi_choice``, the list of options.
        fields: For ``form``, the list of fields to collect.
        default: Default value (shown to user, used if they press Enter).
        sensitive: If True, the response is NOT added to LLM conversation
                   context. Always True for ``secret`` type.
        description: Additional context/help text for the user.
    """

    type: ElicitationType
    prompt: str
    options: list[str] | None = None
    fields: list[FormField] | None = None
    default: str | None = None
    sensitive: bool = False
    description: str | None = None

    def __post_init__(self):
        # Secrets are always sensitive
        if self.type == "secret":
            self.sensitive = True


@dataclass
class ElicitationResponse:
    """The user's response to an elicitation request.

    Attributes:
        value: The user's input (string for text/choice/secret, JSON for form).
               None if cancelled.
        values: For ``multi_choice``, list of selected options. None otherwise.
        cancelled: True if the user cancelled (e.g. Ctrl+C).
        sensitive: Whether the value should be kept out of LLM context.
    """

    value: str | None = None
    values: list[str] | None = None
    cancelled: bool = False
    sensitive: bool = False

    @classmethod
    def cancel(cls) -> "ElicitationResponse":
        """Create a cancelled response."""
        return cls(cancelled=True)

    @classmethod
    def text(cls, value: str, sensitive: bool = False) -> "ElicitationResponse":
        """Create a text/secret/confirmation response."""
        return cls(value=value, sensitive=sensitive)

    @classmethod
    def multi(cls, values: list[str]) -> "ElicitationResponse":
        """Create a multi-choice response."""
        return cls(values=values)


class ElicitationHook(Protocol):
    """Protocol for elicitation backends.

    Elicitation hooks handle user input requests from the agent. Different
    backends can be registered for different environments:
    - CLI: Terminal-based prompts (text, questionary for choice/form)
    - Server: WebSocket/SSE for web UI
    - Test: Mock responses for automated testing

    The hook should return ``None`` to fall through to the next registered hook.
    This allows tool-specific hooks to override the default behavior.

    Args:
        request: The elicitation request from the agent.

    Returns:
        ``ElicitationResponse`` if handled, ``None`` to fall through.
    """

    def __call__(self, request: ElicitationRequest) -> ElicitationResponse | None: ...


# ============================================================================
# CLI Elicitation Handler
# ============================================================================


def cli_elicit(request: ElicitationRequest) -> ElicitationResponse:
    """Handle elicitation via CLI terminal input.

    This is the default handler for interactive CLI sessions. It uses
    ``getpass`` for secrets and ``questionary`` for rich selection UIs.

    Args:
        request: The elicitation request.

    Returns:
        ElicitationResponse with the user's input, or cancelled on interrupt.
    """
    try:
        if request.type == "secret":
            return _cli_secret(request)
        elif request.type == "choice":
            return _cli_choice(request)
        elif request.type == "multi_choice":
            return _cli_multi_choice(request)
        elif request.type == "confirmation":
            return _cli_confirmation(request)
        elif request.type == "form":
            return _cli_form(request)
        else:
            # Default: free-form text
            return _cli_text(request)
    except (KeyboardInterrupt, EOFError):
        return ElicitationResponse.cancel()


def _cli_text(request: ElicitationRequest) -> ElicitationResponse:
    """CLI handler for text elicitation."""
    prompt = request.prompt
    if request.default:
        prompt = f"{prompt} [{request.default}]"
    prompt = f"{prompt}: "
    value = input(prompt).strip() or request.default or ""
    return ElicitationResponse.text(value)


def _cli_secret(request: ElicitationRequest) -> ElicitationResponse:
    """CLI handler for secret elicitation using getpass."""
    prompt = f"{request.prompt}: "
    value = getpass.getpass(prompt)
    return ElicitationResponse.text(value, sensitive=True)


def _cli_choice(request: ElicitationRequest) -> ElicitationResponse:
    """CLI handler for single-choice elicitation."""
    if not request.options:
        logger.warning("choice elicitation without options, falling back to text")
        return _cli_text(request)

    try:
        import questionary

        value = questionary.select(
            request.prompt,
            choices=request.options,
            use_shortcuts=True,
        ).ask()

        if value is None:
            return ElicitationResponse.cancel()
        return ElicitationResponse.text(value)

    except ImportError:
        # Fallback: numbered list
        print(f"\n{request.prompt}")
        for i, opt in enumerate(request.options, 1):
            print(f"  {i}. {opt}")
        while True:
            raw = input("Enter number: ").strip()
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(request.options):
                    return ElicitationResponse.text(request.options[idx])
            except ValueError:
                pass
            print(f"Please enter a number between 1 and {len(request.options)}")


def _cli_multi_choice(request: ElicitationRequest) -> ElicitationResponse:
    """CLI handler for multi-choice elicitation."""
    if not request.options:
        logger.warning("multi_choice elicitation without options, falling back to text")
        return _cli_text(request)

    try:
        import questionary

        values = questionary.checkbox(
            request.prompt,
            choices=request.options,
        ).ask()

        if values is None:
            return ElicitationResponse.cancel()
        return ElicitationResponse.multi(values)

    except ImportError:
        # Fallback: comma-separated
        print(f"\n{request.prompt}")
        for i, opt in enumerate(request.options, 1):
            print(f"  {i}. {opt}")
        raw = input("Enter numbers (comma-separated): ").strip()
        selected = []
        for part in raw.split(","):
            try:
                idx = int(part.strip()) - 1
                if 0 <= idx < len(request.options):
                    selected.append(request.options[idx])
            except ValueError:
                pass
        return ElicitationResponse.multi(selected)


def _cli_confirmation(request: ElicitationRequest) -> ElicitationResponse:
    """CLI handler for yes/no confirmation."""
    try:
        import questionary

        result = questionary.confirm(request.prompt, default=True).ask()
        if result is None:
            return ElicitationResponse.cancel()
        return ElicitationResponse.text("yes" if result else "no")

    except ImportError:
        prompt = f"{request.prompt} [Y/n]: "
        raw = input(prompt).strip().lower()
        if raw in ("n", "no"):
            return ElicitationResponse.text("no")
        return ElicitationResponse.text("yes")


def _cli_form(request: ElicitationRequest) -> ElicitationResponse:
    """CLI handler for multi-field form elicitation."""
    import json

    fields = request.fields or []
    if not fields:
        logger.warning("form elicitation without fields, falling back to text")
        return _cli_text(request)

    if request.prompt:
        print(f"\n{request.prompt}")

    results: dict[str, object] = {}
    for f in fields:
        # Map FormField types to ElicitationType
        # "boolean" and "number" use "confirmation" and "text" respectively
        if f.type == "secret":
            elicit_type: ElicitationType = "secret"
        elif f.type == "choice":
            elicit_type = "choice"
        elif f.type == "boolean":
            elicit_type = "confirmation"
        else:
            elicit_type = "text"

        sub_request = ElicitationRequest(
            type=elicit_type,
            prompt=f.prompt,
            options=f.options,
            default=f.default,
        )

        response = cli_elicit(sub_request)
        if response.cancelled:
            return ElicitationResponse.cancel()

        val: object = response.value
        if f.type == "boolean":
            val = response.value in ("yes", "true", "1", "y")
        elif f.type == "number":
            try:
                v = response.value or ""
                val = float(v) if "." in v else int(v)
            except (ValueError, TypeError):
                val = response.value

        results[f.name] = val

    return ElicitationResponse.text(json.dumps(results))


# ============================================================================
# Main elicit() function
# ============================================================================


def elicit(request: ElicitationRequest) -> ElicitationResponse:
    """Request structured input from the user via the elicitation hook system.

    This is the main entry point for agent-initiated elicitation. It tries
    registered elicitation hooks in priority order (highest first), falling
    through hooks that return ``None``.

    If no hook handles the request, falls back to CLI-based input when
    stdin is a TTY, otherwise returns a cancelled response.

    Args:
        request: The elicitation request describing what input is needed.

    Returns:
        ElicitationResponse with the user's input, or cancelled.

    Note:
        For ``secret`` type requests, the response's ``sensitive`` attribute
        is True and the value should NOT be added to LLM conversation context.
        The ``elicit`` tool in ``gptme.tools.elicit`` handles this automatically.
    """
    from . import HookType, get_hooks

    # Try registered elicitation hooks in priority order
    hooks = [h for h in get_hooks(HookType.ELICIT) if h.enabled]
    for hook in hooks:
        try:
            from typing import cast

            hook_func = cast(ElicitationHook, hook.func)
            result = hook_func(request)
            if result is not None:
                logger.debug(f"Elicitation hook '{hook.name}' handled request")
                return result
            logger.debug(f"Elicitation hook '{hook.name}' returned None, trying next")
        except Exception:
            logger.exception(f"Error in elicitation hook '{hook.name}'")

    # No hook handled it - fallback to CLI if interactive
    import sys

    if sys.stdin.isatty():
        logger.debug("No elicitation hook, falling back to CLI")
        return cli_elicit(request)

    # Non-interactive environment with no hook
    logger.warning("No elicitation hook and non-interactive stdin - cancelling")
    return ElicitationResponse.cancel()


def register_cli_elicitation_hook() -> None:
    """Register the CLI elicitation hook for interactive terminal use."""
    from . import HookType, register_hook

    def cli_hook(request: ElicitationRequest) -> ElicitationResponse | None:
        return cli_elicit(request)

    register_hook(
        name="cli_elicit",
        hook_type=HookType.ELICIT,
        func=cli_hook,  # type: ignore[arg-type]
        priority=0,
        enabled=True,
    )
    logger.debug("Registered CLI elicitation hook")
