#!/usr/bin/env python3
"""Automated demo capture pipeline for gptme.

Captures demos in three modes:
1. Terminal recordings via asciinema (for CLI demos)
2. WebUI screenshots via Playwright (for docs/README)
3. WebUI screen recordings via Playwright (for video demos)

Usage:
    # Run all demos
    python scripts/demo_capture.py --all

    # Run specific mode
    python scripts/demo_capture.py --terminal
    python scripts/demo_capture.py --screenshots
    python scripts/demo_capture.py --recording

    # Custom output directory
    python scripts/demo_capture.py --all --output-dir /tmp/demos

Requirements:
    - asciinema (pip install asciinema)
    - playwright (pip install playwright && playwright install chromium)
    - ffmpeg (for video conversion)
    - gptme (pip install gptme)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Default output directory
DEFAULT_OUTPUT_DIR = Path("demo_output")

# Demo scenarios for terminal recordings
TERMINAL_DEMOS = [
    {
        "name": "hello-world",
        "description": "Simple hello world example",
        "prompt": "Write a Python script that prints 'Hello, World!' and save it to hello.py, then run it.",
    },
    {
        "name": "fibonacci",
        "description": "Create a fibonacci function",
        "prompt": "Write a function that computes the nth fibonacci number to fib.py, then test it with n=10.",
    },
    {
        "name": "file-editing",
        "description": "File creation and editing",
        "prompt": "Create a simple calculator module in calc.py with add, subtract, multiply, divide functions. Then write tests in test_calc.py and run them.",
    },
]

# Pages to screenshot in the WebUI
WEBUI_PAGES = [
    {
        "name": "home",
        "description": "Home page with conversation list",
        "path": "/",
        "wait_for": "text=Introduction to gptme",
        "viewport": {"width": 1280, "height": 800},
    },
    {
        "name": "home-mobile",
        "description": "Home page on mobile viewport",
        "path": "/",
        "wait_for": "text=Introduction to gptme",
        "viewport": {"width": 375, "height": 812},
    },
    {
        "name": "demo-conversation",
        "description": "Demo conversation view",
        "path": "/",
        "click": "text=Introduction to gptme",
        "wait_for": "text=Hello! I'm gptme",
        "viewport": {"width": 1280, "height": 800},
    },
]


def check_tool(name: str) -> bool:
    """Check if a tool is available."""
    return shutil.which(name) is not None


def check_prerequisites(modes: list[str]) -> list[str]:
    """Check required tools and return list of missing ones."""
    missing = []

    if "terminal" in modes:
        if not check_tool("asciinema"):
            missing.append("asciinema (pip install asciinema)")
        if not check_tool("gptme"):
            missing.append("gptme (pip install gptme)")

    if "screenshots" in modes or "recording" in modes:
        try:
            # Check if playwright is importable
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from playwright.sync_api import sync_playwright",
                ],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            missing.append(
                "playwright (pip install playwright && playwright install chromium)"
            )

        if not check_tool("gptme-server"):
            missing.append("gptme-server (pip install gptme)")

    if "recording" in modes and not check_tool("ffmpeg"):
        missing.append("ffmpeg")

    return missing


def capture_terminal_demo(demo: dict, output_dir: Path) -> Path | None:
    """Record a terminal demo using asciinema + gptme.

    Returns the path to the .cast file, or None on failure.
    """
    name = demo["name"]
    prompt = demo["prompt"]
    cast_file = output_dir / f"{name}.cast"

    print(f"  Recording terminal demo: {name}")
    print(f"  Prompt: {prompt[:80]}...")

    # Create a temporary workspace for the demo
    with tempfile.TemporaryDirectory(prefix=f"gptme-demo-{name}-") as tmpdir:
        # Record with asciinema, running gptme non-interactively
        cmd = [
            "asciinema",
            "rec",
            str(cast_file),
            "--cols",
            "120",
            "--rows",
            "35",
            "--overwrite",
            "--command",
            f"cd {tmpdir} && gptme --non-interactive --no-confirm '{prompt}' 2>&1 || true",
        ]

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        # Disable gptme features that add noise to demos
        env["GPTME_TOOL_SOUNDS"] = "false"

        try:
            result = subprocess.run(
                cmd,
                env=env,
                timeout=120,  # 2 minute timeout per demo
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                print(f"  WARNING: asciinema exited with code {result.returncode}")
                if result.stderr:
                    print(f"  stderr: {result.stderr[:200]}")

            if cast_file.exists():
                size = cast_file.stat().st_size
                print(f"  Saved: {cast_file} ({size} bytes)")
                return cast_file
            print("  ERROR: Cast file not created")
            return None

        except subprocess.TimeoutExpired:
            print("  ERROR: Demo timed out after 120s")
            return None
        except Exception as e:
            print(f"  ERROR: {e}")
            return None


def capture_webui_screenshots(
    output_dir: Path, server_url: str = "http://localhost:5700"
) -> list[Path]:
    """Capture screenshots of the WebUI using Playwright.

    Returns list of screenshot paths.
    """
    # Import playwright inline (may not be installed)
    from playwright.sync_api import sync_playwright

    screenshots: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for page_config in WEBUI_PAGES:
            name = str(page_config["name"])
            viewport = page_config["viewport"]
            screenshot_path = output_dir / f"webui-{name}.png"

            print(f"  Capturing screenshot: {name}")

            context = browser.new_context(
                viewport=viewport,  # type: ignore[arg-type]
                device_scale_factor=2,  # Retina quality
            )
            page = context.new_page()

            try:
                # Navigate to the page
                url = server_url.rstrip("/") + str(page_config["path"])
                page.goto(url, wait_until="networkidle", timeout=15000)

                # Wait for specific content
                if "wait_for" in page_config:
                    page.wait_for_selector(page_config["wait_for"], timeout=10000)

                # Click if needed (e.g., to open a conversation)
                if "click" in page_config:
                    page.click(page_config["click"])
                    # Wait for navigation
                    if "wait_for" in page_config:
                        page.wait_for_selector(page_config["wait_for"], timeout=10000)

                # Small delay for animations to settle
                page.wait_for_timeout(500)

                # Take screenshot
                page.screenshot(path=str(screenshot_path), full_page=False)
                size = screenshot_path.stat().st_size
                print(f"  Saved: {screenshot_path} ({size // 1024}KB)")
                screenshots.append(screenshot_path)

            except Exception as e:
                print(f"  ERROR capturing {name}: {e}")

            finally:
                context.close()

        browser.close()

    return screenshots


def capture_webui_recording(
    output_dir: Path, server_url: str = "http://localhost:5700"
) -> Path | None:
    """Record a video of WebUI interaction using Playwright.

    Returns path to the video file, or None on failure.
    """
    from playwright.sync_api import sync_playwright

    video_path = output_dir / "webui-demo.webm"
    result_path: Path | None = video_path

    print("  Recording WebUI interaction...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(output_dir / "_video_tmp"),
            record_video_size={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            # Navigate to home
            page.goto(server_url, wait_until="networkidle", timeout=15000)
            page.wait_for_selector("text=Introduction to gptme", timeout=10000)
            page.wait_for_timeout(1000)

            # Click on demo conversation
            page.click("text=Introduction to gptme")
            page.wait_for_selector("text=Hello! I'm gptme", timeout=10000)
            page.wait_for_timeout(2000)

            # Scroll through the conversation
            for _ in range(3):
                page.mouse.wheel(0, 300)
                page.wait_for_timeout(500)

            # Scroll back up
            page.keyboard.press("Home")
            page.wait_for_timeout(1000)

            # Navigate back to home
            page.goto(server_url, wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(1000)

        except Exception as e:
            print(f"  ERROR during recording: {e}")

        finally:
            # Close context to finalize video
            page_video = page.video
            context.close()

            # Move the recorded video to the final location
            if page_video:
                try:
                    recorded = page_video.path()
                    if recorded and Path(recorded).exists():
                        shutil.move(str(recorded), str(video_path))
                        size = video_path.stat().st_size
                        print(f"  Saved: {video_path} ({size // 1024}KB)")
                    else:
                        print("  ERROR: Video file not found after recording")
                        result_path = None
                except Exception as e:
                    print(f"  ERROR saving video: {e}")
                    result_path = None
            else:
                result_path = None

            # Cleanup temp dir
            tmp_dir = output_dir / "_video_tmp"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

        browser.close()

    return result_path


def start_gptme_server(port: int = 5700) -> subprocess.Popen | None:
    """Start gptme-server in the background.

    Returns the process, or None if server is already running.
    """
    import urllib.request

    # Check if server is already running
    try:
        urllib.request.urlopen(f"http://localhost:{port}/api", timeout=2)
        print(f"  gptme-server already running on port {port}")
        return None
    except Exception:
        pass

    print(f"  Starting gptme-server on port {port}...")
    proc = subprocess.Popen(
        [
            "gptme-server",
            "serve",
            "--port",
            str(port),
            "--cors-origin",
            "http://localhost:5701",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api", timeout=2)
            print(f"  Server ready on port {port}")
            return proc
        except Exception:
            time.sleep(1)

    print("  ERROR: Server failed to start within 30s")
    proc.terminate()
    return None


def start_webui_server(port: int = 5701) -> subprocess.Popen | None:
    """Start the webui dev server.

    Returns the process, or None if already running.
    """
    import urllib.request

    # Check if already running
    try:
        urllib.request.urlopen(f"http://localhost:{port}", timeout=2)
        print(f"  WebUI already running on port {port}")
        return None
    except Exception:
        pass

    webui_dir = Path(__file__).parent.parent / "webui"
    if not webui_dir.exists():
        print(f"  ERROR: WebUI directory not found at {webui_dir}")
        return None

    print(f"  Starting WebUI on port {port}...")
    env = os.environ.copy()
    env["PORT"] = str(port)
    proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=str(webui_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    # Wait for server to be ready
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://localhost:{port}", timeout=2)
            print(f"  WebUI ready on port {port}")
            return proc
        except Exception:
            time.sleep(1)

    print("  ERROR: WebUI failed to start within 30s")
    proc.terminate()
    return None


def generate_summary(output_dir: Path, results: dict[str, list[Path | None]]) -> Path:
    """Generate a summary JSON of captured assets."""
    assets: dict[str, list[dict[str, str | int]]] = {}

    for mode, files in results.items():
        assets[mode] = []
        for f in files:
            if f and f.exists():
                assets[mode].append(
                    {
                        "name": f.name,
                        "path": str(f),
                        "size_bytes": f.stat().st_size,
                    }
                )

    summary: dict[str, object] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "assets": assets,
    }

    summary_path = output_dir / "summary.json"
    with open(summary_path, "w") as fh:
        json.dump(summary, fh, indent=2)

    print(f"\nSummary written to: {summary_path}")
    return summary_path


def main():
    parser = argparse.ArgumentParser(
        description="Capture gptme demos: terminal recordings, WebUI screenshots, and screen recordings."
    )
    parser.add_argument("--all", action="store_true", help="Run all capture modes")
    parser.add_argument(
        "--terminal", action="store_true", help="Record terminal demos with asciinema"
    )
    parser.add_argument(
        "--screenshots", action="store_true", help="Capture WebUI screenshots"
    )
    parser.add_argument(
        "--recording", action="store_true", help="Record WebUI interaction video"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--server-url", default="http://localhost:5701", help="WebUI server URL"
    )
    parser.add_argument(
        "--list-demos", action="store_true", help="List available terminal demos"
    )

    args = parser.parse_args()

    if args.list_demos:
        print("Available terminal demos:")
        for demo in TERMINAL_DEMOS:
            print(f"  {demo['name']}: {demo['description']}")
        return

    # Determine which modes to run
    modes = []
    if args.all:
        modes = ["terminal", "screenshots", "recording"]
    else:
        if args.terminal:
            modes.append("terminal")
        if args.screenshots:
            modes.append("screenshots")
        if args.recording:
            modes.append("recording")

    if not modes:
        parser.print_help()
        print(
            "\nError: specify at least one mode (--terminal, --screenshots, --recording, or --all)"
        )
        sys.exit(1)

    # Check prerequisites
    missing = check_prerequisites(modes)
    if missing:
        print("Missing prerequisites:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    # Create output directory
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}")

    results: dict[str, list[Path | None]] = {}
    server_proc = None
    webui_proc = None

    try:
        # Start servers if needed for WebUI modes
        if "screenshots" in modes or "recording" in modes:
            server_proc = start_gptme_server()
            webui_proc = start_webui_server()

        # Terminal demos
        if "terminal" in modes:
            print("\n=== Terminal Recordings (asciinema) ===")
            terminal_dir = output_dir / "terminal"
            terminal_dir.mkdir(exist_ok=True)
            results["terminal"] = []
            for demo in TERMINAL_DEMOS:
                cast_file = capture_terminal_demo(demo, terminal_dir)
                results["terminal"].append(cast_file)

        # WebUI screenshots
        if "screenshots" in modes:
            print("\n=== WebUI Screenshots (Playwright) ===")
            screenshots_dir = output_dir / "screenshots"
            screenshots_dir.mkdir(exist_ok=True)
            results["screenshots"] = list(
                capture_webui_screenshots(screenshots_dir, server_url=args.server_url)
            )

        # WebUI recording
        if "recording" in modes:
            print("\n=== WebUI Screen Recording (Playwright) ===")
            recording_dir = output_dir / "recordings"
            recording_dir.mkdir(exist_ok=True)
            video = capture_webui_recording(recording_dir, server_url=args.server_url)
            results["recording"] = [video] if video else []

    finally:
        # Clean up servers we started
        if webui_proc:
            webui_proc.terminate()
            webui_proc.wait(timeout=5)
        if server_proc:
            server_proc.terminate()
            server_proc.wait(timeout=5)

    # Generate summary
    generate_summary(output_dir, results)

    # Print results
    print("\n=== Results ===")
    total = 0
    for mode, files in results.items():
        valid = [f for f in files if f and f.exists()]
        total += len(valid)
        print(f"  {mode}: {len(valid)} files")
        for f in valid:
            print(f"    - {f.name} ({f.stat().st_size // 1024}KB)")

    print(f"\nTotal: {total} demo assets captured")
    if total == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
