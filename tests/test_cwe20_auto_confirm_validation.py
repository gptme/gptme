"""Test for CWE-20: Improper input validation on auto_confirm parameter.

The PUT /api/v2/conversations/<id> endpoint uses truthy coercion on the
``auto_confirm`` JSON field: any truthy value (including the string "false")
sets ``auto_confirm_count = 999``, enabling unlimited automatic tool execution
without human review.

The step endpoint (POST /api/v2/conversations/<id>/step) already has strict
type validation (``type(auto_confirm) in (bool, int)``), but the PUT endpoint
and the tool-confirm "auto" action lack equivalent checks.

This test verifies that:
1. String values like "false" are rejected (not coerced to truthy).
2. Only bool and int types are accepted for auto_confirm.
"""

import random

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

pytestmark = [pytest.mark.timeout(10)]


def _create_conversation(client: FlaskClient, auto_confirm=None):
    """Create a V2 conversation, optionally passing auto_confirm."""
    convname = f"test-cwe20-{random.randint(0, 10_000_000)}"
    payload: dict = {"prompt": "You are an AI assistant for testing."}
    if auto_confirm is not None:
        payload["auto_confirm"] = auto_confirm
    response = client.put(f"/api/v2/conversations/{convname}", json=payload)
    return response, convname


class TestAutoConfirmPutValidation:
    """PUT /api/v2/conversations/<id> auto_confirm type validation."""

    def test_string_false_rejected(self, client: FlaskClient):
        """The string 'false' must NOT enable auto-confirm (truthy coercion bug)."""
        resp, _ = _create_conversation(client, auto_confirm="false")
        assert resp.status_code == 400, (
            f"Expected 400 for auto_confirm='false', got {resp.status_code}. "
            f"Truthy coercion would incorrectly set auto_confirm_count=999."
        )

    def test_string_true_rejected(self, client: FlaskClient):
        """Strings should not be accepted for auto_confirm."""
        resp, _ = _create_conversation(client, auto_confirm="true")
        assert resp.status_code == 400

    def test_dict_rejected(self, client: FlaskClient):
        """Non-primitive types must be rejected."""
        resp, _ = _create_conversation(client, auto_confirm={"nested": True})
        assert resp.status_code == 400

    def test_list_rejected(self, client: FlaskClient):
        """List values must be rejected."""
        resp, _ = _create_conversation(client, auto_confirm=[1, 2])
        assert resp.status_code == 400

    def test_float_rejected(self, client: FlaskClient):
        """Floats must be rejected (only bool/int allowed)."""
        resp, _ = _create_conversation(client, auto_confirm=1.5)
        assert resp.status_code == 400

    def test_bool_true_accepted(self, client: FlaskClient):
        """Boolean True should be accepted."""
        resp, _ = _create_conversation(client, auto_confirm=True)
        assert resp.status_code == 200

    def test_bool_false_accepted(self, client: FlaskClient):
        """Boolean False should be accepted (no auto-confirm)."""
        resp, _ = _create_conversation(client, auto_confirm=False)
        assert resp.status_code == 200

    def test_int_accepted(self, client: FlaskClient):
        """Integer values should be accepted as auto_confirm_count."""
        resp, _ = _create_conversation(client, auto_confirm=5)
        assert resp.status_code == 200

    def test_none_accepted(self, client: FlaskClient):
        """Omitting auto_confirm should work (default: no auto-confirm)."""
        resp, _ = _create_conversation(client)
        assert resp.status_code == 200
