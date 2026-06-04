#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def load_spa_redirect_sources(redirects_path: Path) -> list[str]:
    sources: list[str] = []
    for raw_line in redirects_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        source, destination, status = line.split()
        if destination == "/" and status == "200":
            sources.append(source)
    if not sources:
        raise ValueError(f"no SPA redirect rules found in {redirects_path}")
    return sources


def probe_path_for_redirect_source(source: str) -> str:
    if source.endswith("/*"):
        return f"{source[:-1]}deep-link-smoke"
    if "*" in source:
        return source.replace("*", "deep-link-smoke")
    return source


def verify_deep_links(base_url: str, redirects_path: Path, timeout: float) -> list[str]:
    failures: list[str] = []
    sources = load_spa_redirect_sources(redirects_path)
    probe_paths = sorted({probe_path_for_redirect_source(source) for source in sources})
    normalized_base = base_url.rstrip("/")

    for probe_path in probe_paths:
        url = f"{normalized_base}{probe_path}"
        request = Request(
            url,
            headers={"User-Agent": "gptme-webui-deep-link-smoke/1.0"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                status = response.getcode()
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as exc:
            failures.append(f"{probe_path}: HTTP {exc.code}")
            continue
        except URLError as exc:
            failures.append(f"{probe_path}: {exc.reason}")
            continue

        if status != 200 or "text/html" not in content_type.lower():
            failures.append(
                f"{probe_path}: expected 200 text/html, got {status} {content_type!r}"
            )

    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that deployed Cloudflare Pages SPA deep links work."
    )
    parser.add_argument(
        "--redirects-path",
        type=Path,
        required=True,
        help="Path to the deployed _redirects file to derive probe routes from.",
    )
    parser.add_argument(
        "--base-url",
        default="https://chat.gptme.org",
        help="Base URL to probe. Defaults to the production hosted webui.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    failures = verify_deep_links(args.base_url, args.redirects_path, args.timeout)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    sources = load_spa_redirect_sources(args.redirects_path)
    probe_paths = sorted({probe_path_for_redirect_source(source) for source in sources})
    print(f"Verified {len(probe_paths)} deep links against {args.base_url}")
    for probe_path in probe_paths:
        print(f"OK   {probe_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
