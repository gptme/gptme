from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


def correct_output_hello_world(ctx):
    return ctx.stdout == "Hello, world!\n"


def correct_output_hello_human(ctx):
    return ctx.stdout == "Hello, human!\n"


def check_exists_hello(ctx):
    return "hello.py" in ctx.files


def check_exists_main(ctx):
    return "main.py" in ctx.files


def check_prime_exists(ctx):
    return "prime.py" in ctx.files


def check_prime_output(ctx):
    return "541" in ctx.stdout.split()


def check_output_hello_ask(ctx):
    return "Hello, Erik!" in ctx.stdout


def check_fix_bug_output(ctx):
    """The fixed fibonacci should output the correct 10th fibonacci number (55)."""
    return "55" in ctx.stdout.split()


def check_fix_bug_file(ctx):
    return "fib.py" in ctx.files


def check_fix_bug_no_recursion_error(ctx):
    """Ensure no RecursionError or similar crash — program should exit cleanly."""
    return ctx.exit_code == 0


def check_read_modify_output(ctx):
    """After modification, stats.py should output correct stats for the data."""
    output = ctx.stdout.lower()
    words = output.split()
    # data.csv has 5 rows with values 10,20,30,40,50
    # count=5, mean=30, max=50
    has_count = "5" in words  # use split to avoid "5" matching inside "50"
    has_mean = "30" in output  # substring to handle both "30" and "30.0" float output
    has_max = "50" in words
    return has_count and has_mean and has_max


def check_read_modify_file(ctx):
    return "stats.py" in ctx.files


# --- json-transform checks ---


def check_json_transform_output(ctx):
    """Output should contain the transformed JSON data with total revenue and products list."""
    import json

    try:
        data = json.loads(ctx.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    # products: A(10*100=1000), B(20*50=1000), C(5*200=1000) => total 3000
    if data.get("total_revenue") != 3000:
        return False
    # Prompt requires a 'products' list with per-product revenue entries
    products = data.get("products")
    if not isinstance(products, list) or len(products) != 3:
        return False
    revenues = {p.get("name"): p.get("revenue") for p in products}
    return revenues == {"A": 1000, "B": 1000, "C": 1000}


def check_json_transform_file(ctx):
    return "transform.py" in ctx.files


def check_json_transform_exit(ctx):
    return ctx.exit_code == 0


# --- multi-file-refactor checks ---


def check_refactor_main(ctx):
    """main.py should call calculate_total instead of calcTotal."""
    content = ctx.files.get("main.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "calculate_total" in content and "calcTotal(" not in content


def check_refactor_utils(ctx):
    """utils.py should define calculate_total instead of calcTotal."""
    content = ctx.files.get("utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def calculate_total" in content and "def calcTotal" not in content


def check_refactor_output(ctx):
    """Program should still produce correct output after refactoring."""
    return "150" in ctx.stdout


def check_refactor_exit(ctx):
    return ctx.exit_code == 0


# --- write-tests checks ---


def check_tests_file(ctx):
    return "test_mathlib.py" in ctx.files


def check_tests_pass(ctx):
    return ctx.exit_code == 0


def check_tests_output(ctx):
    """All three library functions should have tests (visible in verbose pytest -v output)."""
    return all(fn in ctx.stdout for fn in ("factorial", "is_palindrome", "clamp"))


# --- fix-import-error checks ---


def check_fix_import_output(ctx):
    """multiply(6, 7) should output 42; use substring match to handle 42.0 float output."""
    return "42" in ctx.stdout


def check_fix_import_exit(ctx):
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "hello",
        "files": {},
        "run": "python hello.py",
        "prompt": 'write a script hello.py which prints "Hello, world!"',
        "tools": ["save"],  # Only needs file creation
        "expect": {
            "correct output": correct_output_hello_world,
            "correct file": check_exists_hello,
        },
    },
    {
        "name": "hello-patch",
        "files": {"hello.py": 'print("Hello, world!")'},
        "run": "python hello.py",
        "prompt": 'Patch the code in hello.py to print "Hello, human!"',
        "tools": ["patch"],  # Only needs patching
        "expect": {
            "correct output": correct_output_hello_human,
            "correct file": check_exists_hello,
        },
    },
    {
        "name": "hello-ask",
        "files": {"hello.py": 'print("Hello, world!")'},
        "run": "echo 'Erik' | python hello.py",
        "prompt": "modify hello.py to ask the user for their name and print 'Hello, <name>!'",
        "tools": [
            "save",
            "patch",
            "shell",
        ],  # Can use both save and patch
        "expect": {
            "correct output": check_output_hello_ask,
        },
    },
    {
        "name": "prime100",
        "files": {},
        "run": "python prime.py",
        "prompt": "write a script prime.py that computes and prints the 100th prime number when called, then call it",
        "tools": [
            "save",
            "shell",
        ],
        "expect": {
            "correct file": check_prime_exists,
            "correct output": check_prime_output,
        },
    },
    {
        "name": "fix-bug",
        "files": {
            "fib.py": (
                "def fibonacci(n):\n"
                "    if n <= 0:\n"
                "        return 0\n"
                "    elif n == 1:\n"
                "        return 1\n"
                "    else:\n"
                "        return fibonacci(n) + fibonacci(n - 1)  # bug: should be n-1 and n-2\n"
                "\n"
                "print(fibonacci(10))\n"
            ),
        },
        "run": "python fib.py",
        "prompt": "There is a bug in fib.py that causes infinite recursion. Read the file, find the bug, and fix it.",
        "tools": ["read", "patch", "save"],
        "expect": {
            "correct output": check_fix_bug_output,
            "correct file": check_fix_bug_file,
            "no crash": check_fix_bug_no_recursion_error,
        },
    },
    {
        "name": "read-modify",
        "files": {
            "data.csv": "name,value\nalpha,10\nbeta,20\ngamma,30\ndelta,40\nepsilon,50\n",
            "stats.py": (
                "import csv\n"
                "\n"
                "# TODO: read data.csv and print basic statistics\n"
                "# Should print: count, mean, and max value\n"
                "print('Not implemented yet')\n"
            ),
        },
        "run": "python stats.py",
        "prompt": (
            "Read data.csv and stats.py. Modify stats.py to read the CSV file "
            "and print statistics: the count of rows, the mean of the 'value' column, "
            "and the max value. Format each on its own line like 'count: N', 'mean: X', 'max: Y'."
        ),
        "tools": ["read", "patch", "save"],
        "expect": {
            "correct output": check_read_modify_output,
            "correct file": check_read_modify_file,
        },
    },
    {
        "name": "json-transform",
        "files": {
            "products.json": (
                "[\n"
                '  {"name": "A", "price": 10, "quantity": 100},\n'
                '  {"name": "B", "price": 20, "quantity": 50},\n'
                '  {"name": "C", "price": 5, "quantity": 200}\n'
                "]\n"
            ),
        },
        "run": "python transform.py",
        "prompt": (
            "Read products.json and write transform.py that loads the JSON file, "
            "computes the revenue (price * quantity) for each product, "
            "and prints a JSON object to stdout with keys: "
            "'products' (list of {name, revenue}) and 'total_revenue' (sum of all revenues)."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "correct output": check_json_transform_output,
            "correct file": check_json_transform_file,
            "clean exit": check_json_transform_exit,
        },
    },
    {
        "name": "multi-file-refactor",
        "files": {
            "utils.py": (
                "def calcTotal(prices, tax_rate):\n"
                '    """Calculate total price with tax."""\n'
                "    subtotal = sum(prices)\n"
                "    return round(subtotal * (1 + tax_rate), 2)\n"
            ),
            "main.py": (
                "from utils import calcTotal\n"
                "\n"
                "prices = [50, 30, 20]\n"
                "total = calcTotal(prices, 0.5)\n"
                "print(total)\n"
            ),
        },
        "run": "python main.py",
        "prompt": (
            "Rename the function 'calcTotal' to 'calculate_total' in both utils.py and main.py. "
            "Make sure the program still works correctly after the rename."
        ),
        "tools": ["read", "patch", "save"],
        "expect": {
            "main.py updated": check_refactor_main,
            "utils.py updated": check_refactor_utils,
            "correct output": check_refactor_output,
            "clean exit": check_refactor_exit,
        },
    },
    {
        "name": "write-tests",
        "files": {
            "mathlib.py": (
                "def factorial(n):\n"
                "    if n < 0:\n"
                "        raise ValueError('n must be non-negative')\n"
                "    if n <= 1:\n"
                "        return 1\n"
                "    return n * factorial(n - 1)\n"
                "\n"
                "\n"
                "def is_palindrome(s):\n"
                "    s = s.lower().replace(' ', '')\n"
                "    return s == s[::-1]\n"
                "\n"
                "\n"
                "def clamp(value, low, high):\n"
                "    return max(low, min(high, value))\n"
            ),
        },
        "run": "python -m pytest test_mathlib.py -v",
        "prompt": (
            "Read mathlib.py and write comprehensive tests in test_mathlib.py using pytest. "
            "Test all three functions (factorial, is_palindrome, clamp) including edge cases "
            "and error handling. Make sure all tests pass."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "test file exists": check_tests_file,
            "tests pass": check_tests_pass,
            "test output": check_tests_output,
        },
    },
    {
        "name": "fix-import-error",
        "files": {
            "app.py": (
                "from helpers import compute_answer\n"
                "\n"
                "result = compute_answer(6, 7)\n"
                "print(result)\n"
            ),
            "helpers.py": (
                "from math_ops import multiply\n"
                "\n"
                "\n"
                "def compute_answer(a, b):\n"
                "    return multiply(a, b)\n"
            ),
        },
        "run": "python app.py",
        "prompt": (
            "Running 'python app.py' fails with an import error because math_ops.py doesn't exist. "
            "Read the existing files to understand what's needed, "
            "then create the missing math_ops.py module with the required function "
            "so that app.py runs successfully and prints the correct result."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "correct output": check_fix_import_output,
            "clean exit": check_fix_import_exit,
        },
    },
]
