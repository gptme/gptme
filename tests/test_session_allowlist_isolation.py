"""Regression test: the web-tool host allowlist must not leak across tests.

``set_session_allow_hosts`` writes a module-level ``ContextVar`` that outlives
the test that set it on the same xdist worker. Left unreset, a test that
installs the empty allowlist (``[]`` — "block every host") leaks it into every
later test, so unrelated browser tests fail with ``Host 'example.com' is not in
the session's allowed-hosts list ()`` depending on run order. The autouse
``reset_session_allow_hosts`` fixture in ``conftest.py`` clears it around every
test; these tests assert the isolation holds.

The checks are deliberately self-contained in one process rather than split
across two tests that rely on running on the same xdist worker. CI runs
``pytest -n auto`` with the default ``--dist load`` scheduler, which does *not*
honour ``xdist_group``; two sibling tests can land on different workers, so a
"second test sees ``None``" assertion would pass vacuously (against a fresh
worker default) even if the reset fixture were removed. Driving the fixture's
reset generator directly, and asserting the fixture is autouse, keeps the guard
meaningful regardless of worker distribution.
"""

import contextlib

import pytest
from conftest import _reset_session_allow_hosts

from gptme.tools._url_safety import _get_allow_hosts, set_session_allow_hosts


def test_reset_fixture_is_autouse(request: pytest.FixtureRequest) -> None:
    """Every test must receive the allowlist reset fixture.

    Autouse fixtures are part of the test's fixture closure; if the fixture is
    removed, or its ``autouse=True`` is dropped, this fails.
    """
    assert "reset_session_allow_hosts" in request.fixturenames


def test_reset_fixture_clears_a_leaked_allowlist() -> None:
    """The reset fixture must clear a leaked allowlist before and after a test."""
    reset_gen = _reset_session_allow_hosts()

    # Simulate the leak that motivates the fixture: a prior test installed [].
    set_session_allow_hosts([])
    assert _get_allow_hosts() == []

    # Before-yield reset (runs before each test's body).
    next(reset_gen)
    assert _get_allow_hosts() is None

    # A test body leaks the allowlist again; teardown must clear it.
    set_session_allow_hosts([])
    with contextlib.suppress(StopIteration):
        next(reset_gen)
    assert _get_allow_hosts() is None
