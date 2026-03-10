"""Practical eval tests for real-world programming tasks.

Tests capabilities beyond basic file I/O and debugging:
- Building HTTP APIs
- Parsing structured data
- Adding defensive error handling
"""

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- build-api checks ---


def check_api_server_file(ctx):
    """server.py should exist."""
    return "server.py" in ctx.files


def check_api_get_items(ctx):
    """GET /items should return the two seeded items as JSON."""
    try:
        data = json.loads(ctx.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    results = data.get("results", {})
    get_items = results.get("get_items")
    if not isinstance(get_items, list) or len(get_items) != 2:
        return False
    names = {item.get("name") for item in get_items}
    return names == {"apple", "banana"}


def check_api_post_item(ctx):
    """POST /items should add a new item and return it."""
    try:
        data = json.loads(ctx.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    results = data.get("results", {})
    post_item = results.get("post_item")
    if not isinstance(post_item, dict):
        return False
    return post_item.get("name") == "cherry" and post_item.get("price") == 3.0


def check_api_get_after_post(ctx):
    """After POST, GET /items should return 3 items."""
    try:
        data = json.loads(ctx.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    results = data.get("results", {})
    get_after = results.get("get_after_post")
    if not isinstance(get_after, list):
        return False
    return len(get_after) == 3


def check_api_exit(ctx):
    return ctx.exit_code == 0


# --- parse-log checks ---


def check_parse_log_file(ctx):
    return "analyze.py" in ctx.files


def check_parse_log_output(ctx):
    """Output should contain correct statistics extracted from the log."""
    output = ctx.stdout.lower()
    words = output.split()
    # Log has: 3 ERROR, 4 WARNING, 5 INFO = 12 total lines
    # Error count should be 3
    has_error_count = "3" in words
    # Most common endpoint: /api/users appears 4 times
    has_users_endpoint = "/api/users" in output
    # Total requests (lines with endpoints): 12
    has_total = "12" in words
    return has_error_count and has_users_endpoint and has_total


def check_parse_log_exit(ctx):
    return ctx.exit_code == 0


# --- add-error-handling checks ---


def check_error_handling_file(ctx):
    return "processor.py" in ctx.files


def check_error_handling_normal(ctx):
    """Normal case should still work: processed results appear in output."""
    output = ctx.stdout
    # Test script runs process_records with mixed good/bad data
    return "Alice" in output and "30" in output


def check_error_handling_no_crash(ctx):
    """Program should not crash on bad input — exit code 0."""
    return ctx.exit_code == 0


def check_error_handling_bad_data(ctx):
    """Bad records should be reported, not crash the program."""
    output = ctx.stdout.lower()
    # Should mention errors/skipped/invalid for the bad records
    return "error" in output or "skip" in output or "invalid" in output


def check_error_handling_has_try(ctx):
    """processor.py should contain try/except or similar error handling."""
    content = ctx.files.get("processor.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "try" in content and "except" in content


tests: list["EvalSpec"] = [
    {
        "name": "build-api",
        "files": {
            "test_server.py": (
                "import json\n"
                "import subprocess\n"
                "import sys\n"
                "import time\n"
                "import urllib.request\n"
                "import urllib.error\n"
                "\n"
                "\n"
                "def main():\n"
                "    # Start server in background\n"
                "    proc = subprocess.Popen(\n"
                "        [sys.executable, 'server.py'],\n"
                "        stdout=subprocess.PIPE,\n"
                "        stderr=subprocess.PIPE,\n"
                "    )\n"
                "    results = {}\n"
                "    try:\n"
                "        # Wait for server to start\n"
                "        for _ in range(20):\n"
                "            try:\n"
                "                urllib.request.urlopen('http://localhost:8642/items')\n"
                "                break\n"
                "            except (urllib.error.URLError, ConnectionRefusedError):\n"
                "                time.sleep(0.25)\n"
                "        else:\n"
                "            print(json.dumps({'error': 'server did not start'}))\n"
                "            return\n"
                "\n"
                "        # Test GET /items (should return seeded items)\n"
                "        resp = urllib.request.urlopen('http://localhost:8642/items')\n"
                "        results['get_items'] = json.loads(resp.read())\n"
                "\n"
                "        # Test POST /items\n"
                "        data = json.dumps({'name': 'cherry', 'price': 3.0}).encode()\n"
                "        req = urllib.request.Request(\n"
                "            'http://localhost:8642/items',\n"
                "            data=data,\n"
                "            headers={'Content-Type': 'application/json'},\n"
                "            method='POST',\n"
                "        )\n"
                "        resp = urllib.request.urlopen(req)\n"
                "        results['post_item'] = json.loads(resp.read())\n"
                "\n"
                "        # Test GET /items again (should include new item)\n"
                "        resp = urllib.request.urlopen('http://localhost:8642/items')\n"
                "        results['get_after_post'] = json.loads(resp.read())\n"
                "\n"
                "        print(json.dumps({'results': results}))\n"
                "    finally:\n"
                "        proc.terminate()\n"
                "        proc.wait(timeout=5)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
        },
        "run": "python test_server.py",
        "prompt": (
            "Build a simple REST API server in server.py using only the Python standard "
            "library (http.server). The server should:\n"
            "1. Listen on port 8642\n"
            "2. Support GET /items — returns a JSON array of items\n"
            "3. Support POST /items — accepts JSON {name, price}, adds to the list, "
            "returns the new item as JSON\n"
            "4. Seed the initial items list with: "
            '[{"name": "apple", "price": 1.5}, {"name": "banana", "price": 0.75}]\n'
            "A test_server.py file already exists to validate the endpoints. "
            "Do not modify test_server.py."
        ),
        "tools": ["read", "save", "patch", "shell"],
        "expect": {
            "server.py exists": check_api_server_file,
            "GET /items works": check_api_get_items,
            "POST /items works": check_api_post_item,
            "POST persists": check_api_get_after_post,
            "clean exit": check_api_exit,
        },
    },
    {
        "name": "parse-log",
        "files": {
            "access.log": (
                "2024-01-15 08:23:01 INFO GET /api/users 200 45ms\n"
                "2024-01-15 08:23:15 WARNING GET /api/users 200 1205ms\n"
                "2024-01-15 08:24:02 INFO POST /api/orders 201 89ms\n"
                "2024-01-15 08:24:30 ERROR GET /api/products 500 12ms\n"
                "2024-01-15 08:25:01 INFO GET /api/users 200 38ms\n"
                "2024-01-15 08:25:45 WARNING POST /api/orders 201 980ms\n"
                "2024-01-15 08:26:10 ERROR POST /api/orders 500 5ms\n"
                "2024-01-15 08:26:30 INFO GET /api/products 200 67ms\n"
                "2024-01-15 08:27:00 WARNING GET /api/products 200 1100ms\n"
                "2024-01-15 08:27:15 INFO GET /api/users 200 42ms\n"
                "2024-01-15 08:27:45 ERROR GET /api/users 503 3ms\n"
                "2024-01-15 08:28:00 WARNING GET /api/orders 200 950ms\n"
            ),
            "README.md": (
                "# Log Format\n"
                "Each line: `YYYY-MM-DD HH:MM:SS LEVEL METHOD ENDPOINT STATUS DURATION`\n"
                "\n"
                "## Expected Output\n"
                "```\n"
                "Total requests: <count>\n"
                "Errors: <count>\n"
                "Warnings: <count>\n"
                "Most common endpoint: <endpoint> (<count> requests)\n"
                "Average response time: <time>ms\n"
                "```\n"
            ),
        },
        "run": "python analyze.py access.log",
        "prompt": (
            "Write analyze.py that parses the access.log file and prints statistics. "
            "The log format is documented in README.md. The script should take the "
            "log file path as a command-line argument and print:\n"
            "- Total requests (number of log lines)\n"
            "- Error count (lines with ERROR level)\n"
            "- Warning count (lines with WARNING level)\n"
            "- Most common endpoint (the path that appears most often)\n"
            "- Average response time in milliseconds\n"
            "Use the exact output format shown in README.md."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "analyze.py exists": check_parse_log_file,
            "correct statistics": check_parse_log_output,
            "clean exit": check_parse_log_exit,
        },
    },
    {
        "name": "add-error-handling",
        "files": {
            "processor.py": (
                "import json\n"
                "\n"
                "\n"
                "def process_record(record):\n"
                '    """Process a single record and return formatted result."""\n'
                "    name = record['name']\n"
                "    age = int(record['age'])\n"
                "    score = float(record['score'])\n"
                "    grade = 'A' if score >= 90 else 'B' if score >= 80 else 'C' if score >= 70 else 'F'\n"
                "    return {'name': name, 'age': age, 'grade': grade}\n"
                "\n"
                "\n"
                "def process_records(records):\n"
                '    """Process a list of records and return results."""\n'
                "    results = []\n"
                "    for record in records:\n"
                "        result = process_record(record)\n"
                "        results.append(result)\n"
                "    return results\n"
            ),
            "main.py": (
                "from processor import process_records\n"
                "\n"
                "# Mix of good and bad data\n"
                "records = [\n"
                "    {'name': 'Alice', 'age': '30', 'score': '95'},\n"
                "    {'name': 'Bob', 'age': 'not_a_number', 'score': '85'},\n"
                "    {'name': 'Charlie', 'age': '25', 'score': '78'},\n"
                "    {'name': None, 'age': '20', 'score': '60'},\n"
                "    {'age': '22', 'score': '88'},\n"  # missing 'name'
                "    {'name': 'Diana', 'age': '28', 'score': '92'},\n"
                "]\n"
                "\n"
                "results = process_records(records)\n"
                "for r in results:\n"
                "    if 'error' in r:\n"
                "        print(f\"Error: {r['error']}\")\n"
                "    else:\n"
                "        print(f\"{r['name']}: age={r['age']}, grade={r['grade']}\")\n"
            ),
        },
        "run": "python main.py",
        "prompt": (
            "Running 'python main.py' crashes because processor.py has no error handling "
            "for bad input data. The records list in main.py contains intentionally bad data "
            "(non-numeric age, None name, missing keys). Fix processor.py so that:\n"
            "1. process_record() catches errors and returns {'error': '<description>'} "
            "for bad records instead of crashing\n"
            "2. process_records() continues processing remaining records when one fails\n"
            "3. Good records (Alice, Charlie, Diana) still produce correct output\n"
            "Do not modify main.py."
        ),
        "tools": ["read", "save", "patch"],
        "expect": {
            "processor.py exists": check_error_handling_file,
            "normal data works": check_error_handling_normal,
            "no crash": check_error_handling_no_crash,
            "bad data reported": check_error_handling_bad_data,
            "has error handling": check_error_handling_has_try,
        },
    },
]
