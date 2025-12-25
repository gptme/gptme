"""Tests for the keyword detector hook."""

from gptme.hooks.keyword_detector import (
    ActionType,
    KeywordDetector,
    KeywordPattern,
    get_detector,
)
from gptme.message import Message


class TestKeywordPattern:
    """Tests for KeywordPattern class."""

    def test_matches_exact(self):
        """Test exact keyword matching with word boundaries."""
        pattern = KeywordPattern(
            name="test",
            pattern=r"\bCRITICAL\b",
            action=ActionType.LOG_WARNING,
            case_sensitive=True,  # Exact case match
        )
        assert pattern.matches("This is CRITICAL!")
        assert pattern.matches("CRITICAL error occurred")
        assert not pattern.matches("This is critical")  # case-sensitive won't match
        assert not pattern.matches("UNCRITICAL")  # word boundary prevents partial match

    def test_matches_case_insensitive(self):
        """Test case-insensitive matching (default)."""
        pattern = KeywordPattern(
            name="test",
            pattern=r"error",
            action=ActionType.LOG_WARNING,
            case_sensitive=False,
        )
        assert pattern.matches("ERROR")
        assert pattern.matches("Error")
        assert pattern.matches("error")

    def test_matches_case_sensitive(self):
        """Test case-sensitive matching."""
        pattern = KeywordPattern(
            name="test",
            pattern=r"ERROR",
            action=ActionType.LOG_WARNING,
            case_sensitive=True,
        )
        assert pattern.matches("ERROR")
        assert not pattern.matches("error")
        assert not pattern.matches("Error")

    def test_matches_regex(self):
        """Test regex pattern matching."""
        pattern = KeywordPattern(
            name="test",
            pattern=r"failed \d+ times",
            action=ActionType.LOG_WARNING,
        )
        assert pattern.matches("Operation failed 3 times")
        assert pattern.matches("It failed 100 times already")
        assert not pattern.matches("failed times")


class TestKeywordDetector:
    """Tests for KeywordDetector class."""

    def test_check_message_assistant_only(self):
        """Test that only assistant messages are checked."""
        detector = KeywordDetector(
            patterns=[
                KeywordPattern(
                    name="test",
                    pattern=r"CRITICAL",
                    action=ActionType.INJECT_MESSAGE,
                    message="Test warning",
                )
            ]
        )

        # User message should not trigger
        user_msg = Message("user", "This is CRITICAL!")
        results = list(detector.check_message(user_msg))
        assert len(results) == 0

        # Assistant message should trigger
        assistant_msg = Message("assistant", "This is CRITICAL!")
        results = list(detector.check_message(assistant_msg))
        assert len(results) == 1
        assert "Test warning" in results[0].content

    def test_check_message_cooldown(self):
        """Test that cooldown prevents repeated triggers."""
        detector = KeywordDetector(
            patterns=[
                KeywordPattern(
                    name="test",
                    pattern=r"error",
                    action=ActionType.INJECT_MESSAGE,
                    message="Error detected",
                    cooldown_messages=3,
                )
            ]
        )

        # First message triggers
        msg1 = Message("assistant", "An error occurred")
        results1 = list(detector.check_message(msg1))
        assert len(results1) == 1

        # Second message within cooldown doesn't trigger
        msg2 = Message("assistant", "Another error happened")
        results2 = list(detector.check_message(msg2))
        assert len(results2) == 0

        # Third message still in cooldown
        msg3 = Message("assistant", "Yet another error")
        results3 = list(detector.check_message(msg3))
        assert len(results3) == 0

        # Fourth message (after cooldown of 3) triggers
        msg4 = Message("assistant", "Final error")
        results4 = list(detector.check_message(msg4))
        assert len(results4) == 1

    def test_add_remove_pattern(self):
        """Test adding and removing patterns."""
        detector = KeywordDetector(patterns=[])

        pattern = KeywordPattern(
            name="custom",
            pattern=r"test",
            action=ActionType.LOG_WARNING,
        )
        detector.add_pattern(pattern)
        assert len(detector.patterns) == 1

        removed = detector.remove_pattern("custom")
        assert removed is True
        assert len(detector.patterns) == 0

        removed_again = detector.remove_pattern("nonexistent")
        assert removed_again is False

    def test_disabled_pattern(self):
        """Test that disabled patterns don't trigger."""
        detector = KeywordDetector(
            patterns=[
                KeywordPattern(
                    name="test",
                    pattern=r"ALERT",
                    action=ActionType.INJECT_MESSAGE,
                    message="Alert!",
                    enabled=False,
                )
            ]
        )

        msg = Message("assistant", "ALERT: Something happened")
        results = list(detector.check_message(msg))
        assert len(results) == 0

    def test_custom_handler(self):
        """Test custom handler registration."""
        detector = KeywordDetector(
            patterns=[
                KeywordPattern(
                    name="custom_action",
                    pattern=r"special",
                    action=ActionType.INJECT_MESSAGE,
                    message="Default message",
                )
            ]
        )

        # Register custom handler
        def custom_handler(content: str, pattern: KeywordPattern) -> Message | None:
            return Message("system", f"Custom: found '{pattern.name}' in content")

        detector.register_handler("custom_action", custom_handler)

        msg = Message("assistant", "This is special content")
        results = list(detector.check_message(msg))
        assert len(results) == 1
        assert "Custom:" in results[0].content

    def test_log_warning_action(self, caplog):
        """Test LOG_WARNING action type."""
        detector = KeywordDetector(
            patterns=[
                KeywordPattern(
                    name="warn_test",
                    pattern=r"warning condition",
                    action=ActionType.LOG_WARNING,
                    message="Warning triggered",
                )
            ]
        )

        msg = Message("assistant", "There is a warning condition here")
        results = list(detector.check_message(msg))
        # LOG_WARNING doesn't yield messages, just logs
        assert len(results) == 0
        assert "Warning triggered" in caplog.text


class TestDefaultPatterns:
    """Tests for default keyword patterns."""

    def test_critical_pattern(self):
        """Test the critical error pattern."""
        detector = KeywordDetector()  # Uses DEFAULT_PATTERNS

        msg = Message("assistant", "CRITICAL: System failure detected")
        results = list(detector.check_message(msg))
        assert len(results) == 1
        assert "Critical issue" in results[0].content

    def test_stuck_pattern(self):
        """Test the stuck/frustrated pattern."""
        detector = KeywordDetector()

        msg = Message("assistant", "I've tried everything but it's not working")
        results = list(detector.check_message(msg))
        assert len(results) == 1
        assert "stumbling" in results[0].content.lower()

    def test_security_pattern(self, caplog):
        """Test the security mention pattern (logs warning, no message)."""
        detector = KeywordDetector()

        msg = Message("assistant", "The password = 'secret123' should be changed")
        results = list(detector.check_message(msg))
        # Security pattern logs warning but doesn't inject message
        assert len(results) == 0
        assert "secret detected" in caplog.text.lower()


class TestGlobalDetector:
    """Tests for global detector management."""

    def test_get_detector_singleton(self):
        """Test that get_detector returns the same instance."""
        detector1 = get_detector()
        detector2 = get_detector()
        assert detector1 is detector2
