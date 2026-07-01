"""Tests for computer-use eval suite helpers."""

from gptme.eval.suites import computer as computer_suite
from gptme.eval.types import ResultContext


def test_check_used_open_page_or_click_element_accepts_click(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["click_element('a[title=\"History of Python\"]')"],
    )

    assert computer_suite.check_used_open_page_or_click_element([])


def test_check_used_open_page_or_click_element_rejects_read_only(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["read_page_text()"],
    )

    assert not computer_suite.check_used_open_page_or_click_element([])


def test_expect_second_page_reached_requires_navigation_file():
    ctx = ResultContext(
        files={},
        stdout="cat: navigation.txt: No such file or directory",
        stderr="",
        exit_code=1,
    )

    assert not computer_suite._expect_second_page_reached(ctx)


def test_expect_second_page_reached_accepts_navigation_file():
    ctx = ResultContext(
        files={"navigation.txt": "History of Python"},
        stdout="History of Python",
        stderr="",
        exit_code=0,
    )

    assert computer_suite._expect_second_page_reached(ctx)
