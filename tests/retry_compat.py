"""Compatibility shims for pytest-retry interacting with pytest internals.

Loaded as a plugin by ``tests/conftest.py`` (and directly, via ``-p
retry_compat``, by the regression tests that guard it).
"""

import pytest
from _pytest.tmpdir import tmppath_result_key


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    """Re-seed pytest's ``tmp_path`` bookkeeping so retried tests can tear down.

    pytest's ``tmp_path`` finalizer reads ``node.stash[tmppath_result_key]`` and
    then ``del``etes it (``_pytest/tmpdir.py``). That key is only (re)populated
    by tmpdir's ``pytest_runtest_makereport`` hook.

    pytest-retry runs a preliminary teardown before each retry -- consuming and
    deleting the key -- then re-runs setup/call by invoking the hooks directly
    and building the report with ``TestReport.from_item_and_call``, which
    bypasses ``pytest_runtest_makereport`` entirely. The key is therefore never
    restored, so the *final* teardown of any retried test that used ``tmp_path``
    raises ``KeyError: <_pytest.stash.StashKey ...>``.

    That turns a test which *passed on retry* into a job-failing ERROR, which
    defeats the point of ``--retries``. Seeding an empty dict restores the
    pre-retry behaviour. The value is only consulted under
    ``tmp_path_retention_policy = "failed"``; we use the default ("all"), where
    the dict is fetched but never read.

    Observed on pytest 9.1 x pytest-retry 1.7.0. Remove once pytest-retry
    restores the stash or routes retry attempts through the makereport hook.
    """
    item.stash.setdefault(tmppath_result_key, {})
    yield
