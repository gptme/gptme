"""
Browser tool by calling lynx --dump
"""

import os
import subprocess
from urllib.parse import urlparse


def _validate_url_scheme(url: str) -> None:
    """Validate that URL uses a safe scheme (http/https only).

    Security: Prevents file:// protocol from reading local files.
    See: https://github.com/gptme/gptme/issues/1021
    """
    parsed = urlparse(url)
    allowed_schemes = {"http", "https"}
    if parsed.scheme.lower() not in allowed_schemes:
        raise ValueError(
            f"URL scheme '{parsed.scheme}' not allowed. "
            f"Only {allowed_schemes} are permitted for security reasons."
        )


def read_url(url: str, cookies: dict | None = None) -> str:
    # Security: validate URL scheme before passing to lynx
    _validate_url_scheme(url)

    env = os.environ.copy()
    # TODO: create and set LYNX_CFG to use custom lynx config file (needed to save cookies, which I need to debug how cookies should be read)
    # env["LYNX_CFG"] = str(Path("~/.config/lynx/lynx.cfg").expanduser())
    if cookies:
        # save them to file to be read by lynx
        pass
    #     with open(Path("~/.lynx_cookies").expanduser(), "w") as f:
    #         for k, v in cookies.items():
    #             f.write(f"{k}\t{v}\n")
    p = subprocess.run(
        ["lynx", "--dump", url, "--display_charset=utf-8"],
        env=env,
        check=True,
        capture_output=True,
    )
    # should be utf-8, but we can't be sure
    return p.stdout.decode("utf-8", errors="replace")


def search(query: str, engine: str = "duckduckgo") -> str:
    if engine == "google":
        # TODO: we need to figure out a way to remove the consent banner to access google search results
        #       otherwise google is not usable
        return read_url(
            f"https://www.google.com/search?q={query}&hl=en",
            cookies={"CONSENT+": "YES+42"},
        )
    elif engine == "duckduckgo":
        return read_url(f"https://lite.duckduckgo.com/lite/?q={query}")
    raise ValueError(f"Unknown search engine: {engine}")


def test_read_url():
    content = read_url("https://gptme.org/")
    assert "Getting Started" in content
    content = read_url("https://github.com/gptme/gptme/issues/205")
    assert "lynx-backed browser tool" in content


def test_search():
    # result = search("Python", "google")
    result = search("Erik Bjäreholt", "duckduckgo")
    assert "erik.bjareholt.com" in result
