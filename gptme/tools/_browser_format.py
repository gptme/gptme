"""Shared formatting helpers for browser tool backends."""


def format_snapshot(snapshot: str, url: str, title: str) -> str:
    """Prepend page metadata to an ARIA snapshot.

    Adds the current URL (which may differ from the requested URL after
    redirects) and the page title so agents always know where they are.
    """
    header = f"Page: {title}\nURL: {url}\n\n"
    return header + snapshot
