"""StatusProvider protocol and entry-point registry for ``gptme-util status``.

External packages register implementations under the ``gptme.status_providers``
entry-point group.  Only *installed* packages can register entry points — the
current working directory is never scanned, which prevents auto-loading code
from arbitrary repositories.

Security model
--------------
``importlib.metadata.entry_points`` discovers providers from *installed* Python
packages only.  No filesystem scanning, no ``importlib.import_module`` from the
current directory, no ``exec()`` of repo-local files.  A malicious repository
cannot inject a provider simply by being ``cd``'d into.

Example ``pyproject.toml`` snippet (provider package side):

.. code-block:: toml

    [project.entry-points."gptme.status_providers"]
    my-provider = "my_package.status:make_provider"

The value must be a zero-argument callable that returns a
:class:`StatusProvider` instance.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class StatusProvider(Protocol):
    """Protocol for external status data providers.

    Implementations contribute extra fields to ``gptme-util status`` output
    without requiring any changes to gptme core.

    Register via Python entry points (group ``gptme.status_providers``).  The
    registered value must be a zero-argument factory that returns a
    :class:`StatusProvider` instance.

    Security
    --------
    Only *installed* packages can register entry points.  gptme core never
    loads Python from the current working directory or any repository checkout
    automatically — no ``importlib.import_module(path)`` from cwd, no
    ``exec()`` of repo-local files.
    """

    @property
    def name(self) -> str:
        """Short identifier used as a label in output (e.g. ``"bob"``)."""
        ...

    def collect(self) -> dict[str, object]:
        """Collect and return status data as a flat mapping.

        Keys are merged into the top-level ``--json`` output.  Use a provider-
        specific prefix on keys to avoid collisions (e.g. ``"bob_tasks"``
        rather than plain ``"tasks"``).
        """
        ...

    def narrative_sections(self) -> list[str]:
        """Return zero or more Markdown section strings for narrative output.

        Each returned string is appended to the document built by
        :func:`gptme.cli.cmd_status.build_document` after the core sections.
        Return an empty list when no extra narrative is needed.
        """
        ...


def load_providers() -> list[StatusProvider]:
    """Return all installed :class:`StatusProvider` implementations.

    Scans the ``gptme.status_providers`` entry-point group in installed
    packages only.  Any provider that fails to import, instantiate, or
    satisfy the protocol is silently skipped and logged at ``DEBUG`` level so
    that a broken provider does not break the status command for unrelated
    providers.

    Returns
    -------
    list[StatusProvider]
        Instantiated providers in entry-point registration order.
    """
    from importlib.metadata import entry_points  # lazy — keeps startup fast

    providers: list[StatusProvider] = []
    try:
        eps = entry_points(group="gptme.status_providers")
        for ep in eps:
            try:
                factory = ep.load()
                provider = factory()
                if not isinstance(provider, StatusProvider):
                    logger.debug(
                        "Status provider factory %r returned %r which does not"
                        " satisfy the StatusProvider protocol — skipping",
                        ep.name,
                        type(provider).__name__,
                    )
                    continue
                # Probe that the name property is safely readable.  The
                # runtime_checkable isinstance() check only verifies that the
                # attribute *exists* on the type — it does not call the
                # property getter.  A provider whose name property raises would
                # pass the isinstance check above but crash the error handlers
                # in cmd_status.py which dereference provider.name inside
                # except blocks.
                try:
                    _ = provider.name
                except Exception as name_exc:
                    logger.debug(
                        "Status provider loaded from entry point %r has a"
                        " name property that raises (%s) — skipping to prevent"
                        " error-handler crashes",
                        ep.name,
                        name_exc,
                    )
                    continue
                providers.append(provider)
            except Exception as exc:
                logger.debug("Failed to load status provider %r: %s", ep.name, exc)
    except Exception as exc:
        logger.debug("Failed to enumerate status providers: %s", exc)
    return providers
