"""Tests for the elicitation hook system."""

from unittest.mock import patch

import pytest

from gptme.hooks import HookType, get_hooks, register_hook, unregister_hook
from gptme.hooks.elicitation import (
    ElicitationRequest,
    ElicitationResponse,
    FormField,
    cli_elicit,
    elicit,
)


@pytest.fixture(autouse=True)
def cleanup_hooks():
    """Clean up elicitation hooks after each test."""
    yield
    for hook in get_hooks(HookType.ELICIT):
        unregister_hook(hook.name, HookType.ELICIT)


class TestElicitationRequest:
    """Tests for ElicitationRequest dataclass."""

    def test_text_request(self):
        req = ElicitationRequest(type="text", prompt="Enter text:")
        assert req.type == "text"
        assert req.prompt == "Enter text:"
        assert not req.sensitive

    def test_secret_request_is_sensitive(self):
        """Secret requests are always marked sensitive."""
        req = ElicitationRequest(type="secret", prompt="Enter API key:")
        assert req.sensitive is True

    def test_choice_request_with_options(self):
        req = ElicitationRequest(
            type="choice",
            prompt="Choose one:",
            options=["A", "B", "C"],
        )
        assert req.options == ["A", "B", "C"]

    def test_form_request_with_fields(self):
        fields = [
            FormField(name="name", prompt="Your name?"),
            FormField(name="age", prompt="Your age?", type="number"),
        ]
        req = ElicitationRequest(
            type="form", prompt="Fill out the form:", fields=fields
        )
        assert req.fields is not None
        assert len(req.fields) == 2
        assert req.fields[0].name == "name"
        assert req.fields[1].type == "number"


class TestElicitationResponse:
    """Tests for ElicitationResponse dataclass."""

    def test_cancel_factory(self):
        resp = ElicitationResponse.cancel()
        assert resp.cancelled is True
        assert resp.value is None

    def test_text_factory(self):
        resp = ElicitationResponse.text("hello")
        assert resp.value == "hello"
        assert not resp.sensitive
        assert not resp.cancelled

    def test_text_sensitive_factory(self):
        resp = ElicitationResponse.text("s3cr3t", sensitive=True)
        assert resp.value == "s3cr3t"
        assert resp.sensitive is True

    def test_multi_factory(self):
        resp = ElicitationResponse.multi(["A", "C"])
        assert resp.values == ["A", "C"]
        assert resp.value is None


class TestElicitFunction:
    """Tests for the main elicit() function."""

    def test_registered_hook_is_used(self):
        """A registered elicitation hook should be called."""

        def my_hook(request: ElicitationRequest) -> ElicitationResponse | None:
            return ElicitationResponse.text(f"handled: {request.prompt}")

        register_hook(
            name="test_elicit",
            hook_type=HookType.ELICIT,
            func=my_hook,  # type: ignore[arg-type]
        )

        req = ElicitationRequest(type="text", prompt="What is your name?")
        resp = elicit(req)
        assert resp.value == "handled: What is your name?"
        assert not resp.cancelled

    def test_hook_fallthrough_to_next(self):
        """Hook returning None falls through to the next hook."""
        called = []

        def first_hook(request: ElicitationRequest) -> ElicitationResponse | None:
            called.append("first")
            return None  # Fall through

        def second_hook(request: ElicitationRequest) -> ElicitationResponse | None:
            called.append("second")
            return ElicitationResponse.text("from second")

        register_hook(
            name="first",
            hook_type=HookType.ELICIT,
            func=first_hook,  # type: ignore[arg-type]
            priority=10,
        )
        register_hook(
            name="second",
            hook_type=HookType.ELICIT,
            func=second_hook,  # type: ignore[arg-type]
            priority=0,
        )

        req = ElicitationRequest(type="text", prompt="Test?")
        resp = elicit(req)
        assert called == ["first", "second"]
        assert resp.value == "from second"

    def test_no_hook_non_interactive_cancels(self):
        """Without hooks and in non-interactive mode, elicit returns cancelled."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            req = ElicitationRequest(type="text", prompt="Test?")
            resp = elicit(req)
            assert resp.cancelled

    def test_no_hook_interactive_uses_cli(self):
        """Without hooks in interactive mode, falls back to CLI."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch(
                "gptme.hooks.elicitation.cli_elicit",
                return_value=ElicitationResponse.text("cli_value"),
            ) as mock_cli:
                req = ElicitationRequest(type="text", prompt="Test?")
                resp = elicit(req)
                mock_cli.assert_called_once_with(req)
                assert resp.value == "cli_value"

    def test_cancelled_hook_propagates(self):
        """A hook returning cancel is propagated."""

        def cancelling_hook(request: ElicitationRequest) -> ElicitationResponse | None:
            return ElicitationResponse.cancel()

        register_hook(
            name="cancel_hook",
            hook_type=HookType.ELICIT,
            func=cancelling_hook,  # type: ignore[arg-type]
        )

        req = ElicitationRequest(type="text", prompt="Gonna cancel?")
        resp = elicit(req)
        assert resp.cancelled


class TestCliElicit:
    """Tests for the CLI elicitation handler."""

    def test_text_input(self):
        """CLI text elicitation reads from input()."""
        with patch("builtins.input", return_value="hello world"):
            req = ElicitationRequest(type="text", prompt="Say something:")
            resp = cli_elicit(req)
            assert resp.value == "hello world"
            assert not resp.cancelled
            assert not resp.sensitive

    def test_text_input_with_default(self):
        """Empty input uses the default value."""
        with patch("builtins.input", return_value=""):
            req = ElicitationRequest(type="text", prompt="Name:", default="Alice")
            resp = cli_elicit(req)
            assert resp.value == "Alice"

    def test_secret_uses_getpass(self):
        """Secret elicitation uses getpass for hidden input."""
        with patch("getpass.getpass", return_value="supersecret") as mock_gp:
            req = ElicitationRequest(type="secret", prompt="Enter password:")
            resp = cli_elicit(req)
            mock_gp.assert_called_once()
            assert resp.value == "supersecret"
            assert resp.sensitive

    def test_keyboard_interrupt_returns_cancel(self):
        """KeyboardInterrupt during elicitation returns cancelled response."""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            req = ElicitationRequest(type="text", prompt="Will be interrupted:")
            resp = cli_elicit(req)
            assert resp.cancelled

    def test_eof_returns_cancel(self):
        """EOFError during elicitation returns cancelled response."""
        with patch("builtins.input", side_effect=EOFError):
            req = ElicitationRequest(type="text", prompt="EOF test:")
            resp = cli_elicit(req)
            assert resp.cancelled

    def test_confirmation_yes(self):
        """Confirmation with 'yes' answer works correctly."""
        try:
            import questionary

            # questionary is available - mock its confirm
            with patch.object(questionary, "confirm") as mock_confirm:
                mock_confirm.return_value.ask.return_value = True
                req = ElicitationRequest(type="confirmation", prompt="Are you sure?")
                resp = cli_elicit(req)
                assert resp.value == "yes"
        except ImportError:
            # questionary not available - falls back to input()
            with patch("builtins.input", return_value=""):
                req = ElicitationRequest(type="confirmation", prompt="Are you sure?")
                resp = cli_elicit(req)
                assert resp.value == "yes"

    def test_choice_fallback_without_questionary(self):
        """Choice elicitation works without questionary via numbered list fallback."""
        with patch.dict("sys.modules", {"questionary": None}):
            with patch("builtins.input", return_value="2"):
                req = ElicitationRequest(
                    type="choice",
                    prompt="Pick one:",
                    options=["Apple", "Banana", "Cherry"],
                )
                resp = cli_elicit(req)
                assert resp.value == "Banana"

    def test_multi_choice_fallback_without_questionary(self):
        """Multi-choice works without questionary via comma-separated fallback."""
        with patch.dict("sys.modules", {"questionary": None}):
            with patch("builtins.input", return_value="1, 3"):
                req = ElicitationRequest(
                    type="multi_choice",
                    prompt="Pick multiple:",
                    options=["Apple", "Banana", "Cherry"],
                )
                resp = cli_elicit(req)
                assert resp.values == ["Apple", "Cherry"]

    def test_form_collects_all_fields(self):
        """Form elicitation collects all fields."""
        import json

        fields = [
            FormField(name="name", prompt="Your name?", type="text"),
            FormField(name="confirm", prompt="Confirmed?", type="boolean"),
        ]

        # Mock the cli_elicit function to return predictable responses per call
        call_count = {"n": 0}
        responses = [
            ElicitationResponse.text("Alice"),  # name field
            ElicitationResponse.text("yes"),  # confirm field (boolean)
        ]

        def mock_sub_elicit(request: ElicitationRequest) -> ElicitationResponse:
            idx = call_count["n"]
            call_count["n"] += 1
            return (
                responses[idx] if idx < len(responses) else ElicitationResponse.cancel()
            )

        # We need to patch the recursive cli_elicit calls for form sub-fields
        with patch("gptme.hooks.elicitation.cli_elicit", side_effect=mock_sub_elicit):
            # But we need the form logic itself to run, so call _cli_form directly
            from gptme.hooks.elicitation import _cli_form

            req = ElicitationRequest(
                type="form",
                prompt="Setup form:",
                fields=fields,
            )
            resp = _cli_form(req)
            assert not resp.cancelled
            assert resp.value is not None
            result = json.loads(resp.value)
            assert result["name"] == "Alice"
            assert result["confirm"] is True


class TestElicitTool:
    """Tests for the elicit tool integration."""

    def test_tool_is_discovered(self):
        """The elicit tool should be discoverable."""
        from gptme.tools.elicit import tool_elicit

        assert tool_elicit.name == "elicit"
        assert tool_elicit.disabled_by_default

    def test_valid_text_spec(self):
        """Tool executes with valid text spec."""
        import json

        from gptme.tools.elicit import execute_elicit

        spec = json.dumps({"type": "text", "prompt": "Say something:"})

        with patch(
            "gptme.tools.elicit.elicit",
            return_value=ElicitationResponse.text("hello"),
        ):
            messages = list(execute_elicit(spec, [], {}))

        assert len(messages) == 1
        assert "hello" in messages[0].content

    def test_cancelled_spec(self):
        """Tool handles cancellation."""
        import json

        from gptme.tools.elicit import execute_elicit

        spec = json.dumps({"type": "text", "prompt": "Test:"})

        with patch(
            "gptme.tools.elicit.elicit",
            return_value=ElicitationResponse.cancel(),
        ):
            messages = list(execute_elicit(spec, [], {}))

        assert len(messages) == 1
        assert "cancel" in messages[0].content.lower()

    def test_secret_not_in_message(self):
        """Secret values are not included in the system message."""
        import json

        from gptme.tools.elicit import execute_elicit

        spec = json.dumps({"type": "secret", "prompt": "API key:"})

        with patch(
            "gptme.tools.elicit.elicit",
            return_value=ElicitationResponse.text("supersecret", sensitive=True),
        ):
            messages = list(execute_elicit(spec, [], {}))

        assert len(messages) == 1
        # The secret value should NOT appear in the message
        assert "supersecret" not in messages[0].content
        # But the user should be informed
        assert (
            "secret" in messages[0].content.lower()
            or "provided" in messages[0].content.lower()
        )

    def test_form_json_output(self):
        """Form responses are formatted as JSON."""
        import json

        from gptme.tools.elicit import execute_elicit

        spec = json.dumps(
            {
                "type": "form",
                "prompt": "Setup:",
                "fields": [{"name": "name", "prompt": "Name?", "type": "text"}],
            }
        )
        form_value = json.dumps({"name": "Alice"})

        with patch(
            "gptme.tools.elicit.elicit",
            return_value=ElicitationResponse.text(form_value),
        ):
            messages = list(execute_elicit(spec, [], {}))

        assert len(messages) == 1
        assert "Alice" in messages[0].content
        assert "json" in messages[0].content.lower()

    def test_invalid_spec_returns_error(self):
        """Invalid spec produces error message."""
        from gptme.tools.elicit import execute_elicit

        messages = list(execute_elicit("not valid json", [], {}))
        assert len(messages) == 1
        assert (
            "invalid" in messages[0].content.lower()
            or "failed" in messages[0].content.lower()
            or "spec" in messages[0].content.lower()
        )

    def test_missing_prompt_returns_error(self):
        """Spec missing 'prompt' produces error message."""
        import json

        from gptme.tools.elicit import execute_elicit

        spec = json.dumps({"type": "text"})  # Missing prompt
        messages = list(execute_elicit(spec, [], {}))
        assert len(messages) == 1
        assert (
            "invalid" in messages[0].content.lower()
            or "missing" in messages[0].content.lower()
        )
