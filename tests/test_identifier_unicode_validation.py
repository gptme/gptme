"""Reject unencodable identifiers without turning invalid input into a 500."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gptme.util.conversation_ids import conversation_id_error, is_valid_conversation_id

if TYPE_CHECKING:
    from flask.testing import FlaskClient


@pytest.mark.parametrize("name", ["bad\ud800name", "bad\udc80name"])
def test_surrogate_conversation_name(name: str) -> None:
    assert conversation_id_error(name) is not None
    assert not is_valid_conversation_id(name)


@pytest.mark.parametrize("name", ["bad\ud800name", "bad\udc80name"])
def test_surrogate_branch_request(client: FlaskClient, name: str) -> None:
    # JSON permits escaped lone surrogates; validation must handle them before
    # computing the encoded filename length or accessing a conversation.
    response = client.post(
        "/api/v2/conversations/test-conv",
        json={"role": "user", "content": "hello", "branch": name},
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid branch name"}


def test_valid_unicode_identifiers() -> None:
    flask = pytest.importorskip("flask")
    from gptme.server.api_v2_common import _validate_branch

    assert conversation_id_error("对话-😀") is None
    assert conversation_id_error("😀" * 63) is None
    assert conversation_id_error("😀" * 64) == "conversation name too long."
    with flask.Flask(__name__).app_context():
        assert _validate_branch("分支-😀") is None
        assert _validate_branch("😀" * 62) is None
        result = _validate_branch("😀" * 63)
        assert result is not None
        response, status = result
        assert status == 400
        assert response.get_json() == {"error": "branch name too long"}
