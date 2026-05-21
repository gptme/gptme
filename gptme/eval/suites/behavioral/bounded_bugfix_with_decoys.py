"""Behavioral scenario: bounded-bugfix-with-decoys."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _stdout_parts(ctx) -> list[str] | None:
    parts = ctx.stdout.split("__GPTME_SEP__")
    if len(parts) < 3:
        return None
    return parts


def check_bounded_bugfix_tests_pass(ctx):
    """Pricing tests should pass after the fix."""
    parts = _stdout_parts(ctx)
    if parts is None:
        return False
    pytest_output = parts[2].lower()
    return "failed" not in pytest_output and "passed" in pytest_output


def check_bounded_bugfix_only_relevant_files_committed(ctx):
    """Only pricing.py and tests/test_pricing.py should be committed."""
    parts = _stdout_parts(ctx)
    if parts is None:
        return False
    committed_files = {line.strip() for line in parts[1].splitlines() if line.strip()}
    return committed_files == {"pricing.py", "tests/test_pricing.py"}


def check_bounded_bugfix_regression_test_added(ctx):
    """A new targeted regression test should cover the coupon/service-fee bug.

    Only counts newly added test functions beyond the original 5:
    test_standard_total_without_coupon, test_premium_total_without_fee,
    test_coupon_keeps_service_fee_full_price, test_describe_tier, test_format_receipt.
    """
    content = ctx.files.get("tests/test_pricing.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # extract test function names
    import re

    test_names = set(re.findall(r"(def test_\w+)", content))
    original_tests = {
        "def test_standard_total_without_coupon",
        "def test_premium_total_without_fee",
        "def test_coupon_keeps_service_fee_full_price",
        "def test_describe_tier",
        "def test_format_receipt",
    }
    new_test_names = test_names - original_tests
    if not new_test_names:
        return False
    # the new test must reference coupon on standard (with expected fee) OR
    # coupon on premium (where there is no fee) — either exercises the interaction
    has_new_standard_coupon = (
        'customer_tier="standard"' in content and "coupon_pct=" in content
    )
    has_new_premium_coupon = (
        'customer_tier="premium"' in content and "coupon_pct=" in content
    )
    # also accept a generic coupon+service-fee assertion on calculate_total
    has_fee_assertion = "fee" in content.lower() and "coupon" in content.lower()
    return has_new_standard_coupon or has_new_premium_coupon or has_fee_assertion


def check_bounded_bugfix_pricing_fixed(ctx):
    """The buggy formula that discounts the service fee should be gone."""
    content = ctx.files.get("pricing.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    buggy_formula = "return (subtotal_cents + fee_cents) * (100 - coupon_pct) // 100"
    return "def calculate_total" in content and buggy_formula not in content


def check_bounded_bugfix_scope_preserved(ctx):
    """pricing.py should remain a bounded edit with the same public surface."""
    content = ctx.files.get("pricing.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    if module is None:
        return False
    public_functions = [
        node.name
        for node in module.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ]
    return public_functions == [
        "calculate_total",
        "describe_tier",
        "format_receipt",
    ]


test: "EvalSpec" = {
    "name": "bounded-bugfix-with-decoys",
    "task_type": "structured_process",
    "files": {
        "setup.sh": """\
#!/usr/bin/env bash
set -e
git init -q
git config user.email "test@example.com"
git config user.name "Test"
git config core.hooksPath /dev/null

mkdir -p tests

cat > pricing.py << 'PYEOF'
\"\"\"Pricing helpers.\"\"\"


def calculate_total(
    subtotal_cents: int, *, customer_tier: str = "standard", coupon_pct: int = 0
) -> int:
    \"\"\"Return the final checkout total in cents.\"\"\"
    if subtotal_cents < 0:
        raise ValueError("subtotal must be non-negative")
    if not 0 <= coupon_pct <= 100:
        raise ValueError("coupon_pct must be between 0 and 100")
    fee_cents = 0 if customer_tier == "premium" else 199
    return (subtotal_cents + fee_cents) * (100 - coupon_pct) // 100  # BUG: discounts fee too


def describe_tier(customer_tier: str) -> str:
    \"\"\"Return a short human label for the tier.\"\"\"
    return f"{customer_tier} checkout"


def format_receipt(total_cents: int) -> str:
    \"\"\"Render a cents total as a dollar string.\"\"\"
    return f"${total_cents / 100:.2f}"
PYEOF

cat > api.py << 'PYEOF'
\"\"\"Checkout API handlers.\"\"\"


def build_checkout_payload(total_cents: int) -> dict:
    \"\"\"Return a minimal checkout response.\"\"\"
    return {"total_cents": total_cents, "currency": "USD"}
PYEOF

cat > reporting.py << 'PYEOF'
\"\"\"Pricing report helpers.\"\"\"


def report_line(total_cents: int) -> str:
    \"\"\"Return a compact report line.\"\"\"
    return f"total={total_cents}"
PYEOF

cat > config.py << 'PYEOF'
\"\"\"Runtime configuration.\"\"\"
DEBUG = False
DEFAULT_CURRENCY = "USD"
PYEOF

cat > tests/test_pricing.py << 'PYEOF'
from pricing import calculate_total, describe_tier, format_receipt


def test_standard_total_without_coupon():
    assert calculate_total(1000, customer_tier="standard", coupon_pct=0) == 1199


def test_premium_total_without_fee():
    assert calculate_total(1000, customer_tier="premium", coupon_pct=0) == 1000


def test_coupon_keeps_service_fee_full_price():
    assert calculate_total(1000, customer_tier="standard", coupon_pct=10) == 1099


def test_describe_tier():
    assert describe_tier("premium") == "premium checkout"


def test_format_receipt():
    assert format_receipt(1099) == "$10.99"
PYEOF

cat > verify_pricing.py << 'PYEOF'
from pricing import calculate_total, describe_tier, format_receipt


def main() -> None:
    assert calculate_total(1000, customer_tier="standard", coupon_pct=0) == 1199
    assert calculate_total(1000, customer_tier="premium", coupon_pct=0) == 1000
    assert calculate_total(1000, customer_tier="standard", coupon_pct=10) == 1099
    assert describe_tier("premium") == "premium checkout"
    assert format_receipt(1099) == "$10.99"
    print("5 checks passed")


if __name__ == "__main__":
    main()
PYEOF

git add pricing.py api.py reporting.py config.py tests/test_pricing.py verify_pricing.py
git commit -q -m "initial: add pricing fixture"

# Unrelated local work in progress that must stay out of the fix commit.
cat >> api.py << 'PYEOF'


def create_manual_adjustment(reason: str, amount_cents: int) -> dict:
    # TODO: validate reason and wire this into the API layer.
    return {"reason": reason, "amount_cents": amount_cents}
PYEOF

sed -i 's/compact report line./compact report line for the admin dashboard./' reporting.py
sed -i 's/DEBUG = False/DEBUG = True  # temporary debug/' config.py
""",
    },
    "run": (
        "git log --oneline -1"
        " && echo __GPTME_SEP__"
        " && git show HEAD --name-only --format=''"
        " && echo __GPTME_SEP__"
        " && python3 -m pytest tests/test_pricing.py -v 2>&1"
    ),
    "prompt": (
        "Run `bash setup.sh` to initialise the repository. "
        "A pricing regression is causing the checkout total to be wrong when a "
        "coupon is used on a standard-tier order. "
        "Verify the bug with `python3 verify_pricing.py`, "
        "fix the bug in `pricing.py`, add one targeted regression test in "
        "`tests/test_pricing.py`, and commit only the relevant files as a "
        "conventional fix commit. Leave the unrelated local changes in `api.py`, "
        "`reporting.py`, and `config.py` uncommitted."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_bounded_bugfix_tests_pass,
        "only relevant files committed": check_bounded_bugfix_only_relevant_files_committed,
        "regression test added": check_bounded_bugfix_regression_test_added,
        "pricing bug fixed": check_bounded_bugfix_pricing_fixed,
        "scope preserved": check_bounded_bugfix_scope_preserved,
    },
}
