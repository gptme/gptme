"""Regression test: the web-tool host allowlist must not leak across tests.

``set_session_allow_hosts`` writes a module-level ``ContextVar`` that outlives
the test that set it on the same xdist worker. Left unreset, a test that
installs the empty allowlist (``[]`` — "block every host") leaks it into every
later test, so unrelated browser tests fail with ``Host 'example.com' is not in
the session's allowed-hosts list ()`` depending on run order. The autouse
``reset_session_allow_hosts`` fixture in ``conftest.py`` clears it around every
test; this pair asserts the isolation holds.
"""

from gptme.tools._url_safety import _get_allow_hosts, set_session_allow_hosts


def test_installs_empty_allowlist():
    """Install the worst-case allowlist for the immediately following test."""
    set_session_allow_hosts([])
    assert _get_allow_hosts() == []


def test_allowlist_is_reset_between_tests():
    """A prior test's allowlist must not survive into this one."""
    assert _get_allow_hosts() is None
