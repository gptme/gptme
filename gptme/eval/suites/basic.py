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
]
