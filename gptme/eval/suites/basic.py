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
    # data.csv has 5 rows with values 10,20,30,40,50
    # count=5, mean=30, max=50
    has_count = "5" in output
    has_mean = "30" in output
    has_max = "50" in output
    return has_count and has_mean and has_max


def check_read_modify_file(ctx):
    return "stats.py" in ctx.files


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
]
