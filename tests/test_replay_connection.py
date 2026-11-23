"""Evidence replay must release its temporary SQLite database after scoring."""

import sqlite3

import pytest

from gptme.util import replay


@pytest.mark.parametrize("deny_tables", [False, True])
def test_bm25_closes_database(
    deny_tables: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = sqlite3.connect(":memory:")
    if deny_tables:
        connection.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
    monkeypatch.setattr(replay.sqlite3, "connect", lambda _: connection)
    try:
        messages = [{"role": "user", "content": "Python programming"}]
        if deny_tables:
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                replay.score_messages_bm25(messages, "Python")
        else:
            assert replay.score_messages_bm25(messages, "Python")
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")
    finally:
        connection.close()
