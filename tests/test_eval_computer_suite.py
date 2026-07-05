"""Tests for computer-use eval suite helpers."""

import logging
from datetime import datetime, timezone

from gptme.eval.suites import computer as computer_suite
from gptme.eval.types import ResultContext
from gptme.message import Message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts():
    return datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _assistant(text: str) -> Message:
    return Message(role="assistant", content=text, timestamp=_ts())


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


def test_expect_form_submitted_requires_echoed_field():
    ctx = ResultContext(
        files={"result.txt": "Error: form unavailable"},
        stdout="Error: form unavailable",
        stderr="",
        exit_code=0,
    )

    assert not computer_suite._expect_form_submitted(ctx)


def test_expect_form_submitted_accepts_echoed_field():
    ctx = ResultContext(
        files={"result.txt": '{"form": {"custname": "TestUser"}}'},
        stdout='{"form": {"custname": "TestUser"}}',
        stderr="",
        exit_code=0,
    )

    assert computer_suite._expect_form_submitted(ctx)


# ---------------------------------------------------------------------------
# _executed_tool_calls — direct calls (lines 47-59)
# ---------------------------------------------------------------------------


def test_executed_tool_calls_empty_messages():
    """Empty message list returns [] without errors (lines 47-53)."""
    assert computer_suite._executed_tool_calls([]) == []


def test_executed_tool_calls_user_message_skipped():
    """Non-assistant messages are ignored (line 50 filter)."""
    msgs = [Message(role="user", content="take a screenshot", timestamp=_ts())]
    assert computer_suite._executed_tool_calls(msgs) == []


def test_executed_tool_calls_no_runnable_tools_emits_debug(caplog):
    """When assistant message exists but no tools are registered, [] is returned and debug is logged (lines 54-58)."""
    msgs = [_assistant("I will take a screenshot now.")]
    with caplog.at_level(logging.DEBUG, logger="gptme.eval.suites.computer"):
        result = computer_suite._executed_tool_calls(msgs)
    assert result == []
    assert "verify init_tools" in caplog.text


# ---------------------------------------------------------------------------
# check_used_snapshot_or_observe_web (line 64)
# ---------------------------------------------------------------------------


def test_check_used_snapshot_or_observe_web_snapshot(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["snapshot_url('https://example.com')"],
    )
    assert computer_suite.check_used_snapshot_or_observe_web([])


def test_check_used_snapshot_or_observe_web_observe_web(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["observe_web('https://example.com')"],
    )
    assert computer_suite.check_used_snapshot_or_observe_web([])


def test_check_used_snapshot_or_observe_web_rejects_screenshot_only(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["computer('screenshot')"],
    )
    assert not computer_suite.check_used_snapshot_or_observe_web([])


# ---------------------------------------------------------------------------
# check_used_open_page (line 72)
# ---------------------------------------------------------------------------


def test_check_used_open_page_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["open_page('https://example.com')"],
    )
    assert computer_suite.check_used_open_page([])


def test_check_used_open_page_rejects_read_url(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["read_page_text()"],
    )
    assert not computer_suite.check_used_open_page([])


# ---------------------------------------------------------------------------
# check_used_fill_element (line 77)
# ---------------------------------------------------------------------------


def test_check_used_fill_element_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ['fill_element(\'[name="custname"]\', "TestUser")'],
    )
    assert computer_suite.check_used_fill_element([])


def test_check_used_fill_element_rejects_type_action(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["computer('type', text='TestUser')"],
    )
    assert not computer_suite.check_used_fill_element([])


# ---------------------------------------------------------------------------
# check_used_click_element (line 82)
# ---------------------------------------------------------------------------


def test_check_used_click_element_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["click_element('[type=\"submit\"]')"],
    )
    assert computer_suite.check_used_click_element([])


def test_check_used_click_element_rejects_coordinate_click(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["computer('left_click', coordinate=(100, 200))"],
    )
    assert not computer_suite.check_used_click_element([])


# ---------------------------------------------------------------------------
# check_did_not_screenshot_for_web (lines 95-127)
# ---------------------------------------------------------------------------


def test_check_did_not_screenshot_no_structured_call_fails(monkeypatch):
    """first_snapshot == -1 → structured approach never used → fail (line 121)."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["computer('screenshot')"],
    )
    assert not computer_suite.check_did_not_screenshot_for_web([])


def test_check_did_not_screenshot_structured_only_passes(monkeypatch):
    """Snapshot used, no screenshot at all → ideal path (line 124)."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["snapshot_url('https://example.com')", "read_page_text()"],
    )
    assert computer_suite.check_did_not_screenshot_for_web([])


def test_check_did_not_screenshot_snapshot_before_screenshot_passes(monkeypatch):
    """Snapshot precedes screenshot → policy respected (line 127 branch)."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: [
            "snapshot_url('https://example.com')",
            "computer('screenshot')",
        ],
    )
    assert computer_suite.check_did_not_screenshot_for_web([])


def test_check_did_not_screenshot_screenshot_before_snapshot_fails(monkeypatch):
    """Screenshot precedes snapshot → policy violated (line 127 branch, False)."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: [
            "computer('screenshot')",
            "snapshot_url('https://example.com')",
        ],
    )
    assert not computer_suite.check_did_not_screenshot_for_web([])


def test_check_did_not_screenshot_double_quote_screenshot_detected(monkeypatch):
    """Double-quote variant computer("screenshot") is also detected (lines 110-116)."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: [
            'computer("screenshot")',
            "snapshot_url('https://example.com')",
        ],
    )
    assert not computer_suite.check_did_not_screenshot_for_web([])


# ---------------------------------------------------------------------------
# _expect_summary_written (line 137)
# ---------------------------------------------------------------------------


def test_expect_summary_written_file_present():
    ctx = ResultContext(
        files={"summary.txt": "TITLE=Hello"}, stdout="", stderr="", exit_code=0
    )
    assert computer_suite._expect_summary_written(ctx)


def test_expect_summary_written_via_stdout():
    ctx = ResultContext(files={}, stdout="TITLE=Hello World", stderr="", exit_code=0)
    assert computer_suite._expect_summary_written(ctx)


def test_expect_summary_written_fails_when_empty():
    ctx = ResultContext(files={}, stdout="", stderr="", exit_code=0)
    assert not computer_suite._expect_summary_written(ctx)


# ---------------------------------------------------------------------------
# _expect_title_extracted (line 141)
# ---------------------------------------------------------------------------


def test_expect_title_extracted_title_prefix():
    ctx = ResultContext(files={}, stdout="TITLE=Example Domain", stderr="", exit_code=0)
    assert computer_suite._expect_title_extracted(ctx)


def test_expect_title_extracted_example_domain():
    ctx = ResultContext(
        files={}, stdout="Example Domain is the page title.", stderr="", exit_code=0
    )
    assert computer_suite._expect_title_extracted(ctx)


def test_expect_title_extracted_fails_generic_output():
    ctx = ResultContext(
        files={}, stdout="no useful content here", stderr="", exit_code=0
    )
    assert not computer_suite._expect_title_extracted(ctx)


# ---------------------------------------------------------------------------
# _expect_clean_exit (line 145)
# ---------------------------------------------------------------------------


def test_expect_clean_exit_zero():
    ctx = ResultContext(files={}, stdout="", stderr="", exit_code=0)
    assert computer_suite._expect_clean_exit(ctx)


def test_expect_clean_exit_nonzero():
    ctx = ResultContext(files={}, stdout="", stderr="error", exit_code=1)
    assert not computer_suite._expect_clean_exit(ctx)


# ---------------------------------------------------------------------------
# _expect_links_written (line 149)
# ---------------------------------------------------------------------------


def test_expect_links_written_file_present():
    ctx = ResultContext(
        files={"links.txt": "Python\nJava\nRust"}, stdout="", stderr="", exit_code=0
    )
    assert computer_suite._expect_links_written(ctx)


def test_expect_links_written_via_stdout():
    ctx = ResultContext(files={}, stdout="Python\nJava\nRust", stderr="", exit_code=0)
    assert computer_suite._expect_links_written(ctx)


def test_expect_links_written_fails_empty():
    ctx = ResultContext(files={}, stdout="", stderr="", exit_code=0)
    assert not computer_suite._expect_links_written(ctx)


# ---------------------------------------------------------------------------
# _expect_at_least_one_title (line 153)
# ---------------------------------------------------------------------------


def test_expect_at_least_one_title_success():
    ctx = ResultContext(files={}, stdout="Example Domain", stderr="", exit_code=0)
    assert computer_suite._expect_at_least_one_title(ctx)


def test_expect_at_least_one_title_fails_empty():
    ctx = ResultContext(files={}, stdout="", stderr="", exit_code=0)
    assert not computer_suite._expect_at_least_one_title(ctx)


# ---------------------------------------------------------------------------
# _expect_result_written (line 157)
# ---------------------------------------------------------------------------


def test_expect_result_written_file_present():
    ctx = ResultContext(
        files={"result.txt": "submitted"}, stdout="", stderr="", exit_code=0
    )
    assert computer_suite._expect_result_written(ctx)


def test_expect_result_written_via_stdout():
    ctx = ResultContext(files={}, stdout="Form submitted!", stderr="", exit_code=0)
    assert computer_suite._expect_result_written(ctx)


def test_expect_result_written_fails_empty():
    ctx = ResultContext(files={}, stdout="", stderr="", exit_code=0)
    assert not computer_suite._expect_result_written(ctx)


# ---------------------------------------------------------------------------
# _expect_page2_content (line 166)
# ---------------------------------------------------------------------------


def test_expect_page2_content_file_present():
    ctx = ResultContext(
        files={"navigation.txt": "History of Python"}, stdout="", stderr="", exit_code=0
    )
    assert computer_suite._expect_page2_content(ctx)


def test_expect_page2_content_via_stdout():
    ctx = ResultContext(
        files={}, stdout="History of Python (redirected)", stderr="", exit_code=0
    )
    assert computer_suite._expect_page2_content(ctx)


def test_expect_page2_content_fails_empty():
    ctx = ResultContext(files={}, stdout="", stderr="", exit_code=0)
    assert not computer_suite._expect_page2_content(ctx)


# ---------------------------------------------------------------------------
# _expect_second_page_reached bytes branch (line 174)
# ---------------------------------------------------------------------------


def test_expect_second_page_reached_decodes_bytes():
    """bytes content is decoded before length check (line 174)."""
    ctx = ResultContext(
        files={"navigation.txt": b"History of Python"},
        stdout="",
        stderr="",
        exit_code=0,
    )
    assert computer_suite._expect_second_page_reached(ctx)


# ---------------------------------------------------------------------------
# check_used_press_key
# ---------------------------------------------------------------------------


def test_check_used_press_key_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["press_key('Return')"],
    )
    assert computer_suite.check_used_press_key([])


def test_check_used_press_key_accepts_tab(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["press_key('Tab')", "press_key('Return')"],
    )
    assert computer_suite.check_used_press_key([])


def test_check_used_press_key_rejects_click_element(monkeypatch):
    """Using click_element for submit should not satisfy press_key check."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["click_element('[type=\"submit\"]')"],
    )
    assert not computer_suite.check_used_press_key([])


def test_check_used_press_key_rejects_type_action(monkeypatch):
    """computer('type', ...) is not press_key."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["computer('type', text='hello')"],
    )
    assert not computer_suite.check_used_press_key([])


# ---------------------------------------------------------------------------
# check_used_select_option
# ---------------------------------------------------------------------------


def test_check_used_select_option_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ['select_option(\'[name="size"]\', "large")'],
    )
    assert computer_suite.check_used_select_option([])


def test_check_used_select_option_rejects_fill_element(monkeypatch):
    """fill_element is not select_option."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ['fill_element(\'[name="size"]\', "large")'],
    )
    assert not computer_suite.check_used_select_option([])


def test_check_used_select_option_rejects_empty(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: [],
    )
    assert not computer_suite.check_used_select_option([])


# ---------------------------------------------------------------------------
# check_used_wait_for_element
# ---------------------------------------------------------------------------


def test_check_used_wait_for_element_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["wait_for_element('[data-testid=\"tweetTextarea_0\"]')"],
    )
    assert computer_suite.check_used_wait_for_element([])


def test_check_used_wait_for_element_with_timeout(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["wait_for_element('#submit-btn', timeout_ms=8000)"],
    )
    assert computer_suite.check_used_wait_for_element([])


def test_check_used_wait_for_element_rejects_snapshot(monkeypatch):
    """snapshot_url is not wait_for_element."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["snapshot_url('https://example.com')"],
    )
    assert not computer_suite.check_used_wait_for_element([])


def test_check_used_wait_for_element_rejects_empty(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: [],
    )
    assert not computer_suite.check_used_wait_for_element([])


# ---------------------------------------------------------------------------
# _expect_keyboard_submit_reflected
# ---------------------------------------------------------------------------


def test_expect_keyboard_submit_reflected_via_custname(monkeypatch):
    ctx = ResultContext(
        files={"result.txt": '{"form": {"custname": "TestUser"}}'},
        stdout='{"form": {"custname": "TestUser"}}',
        stderr="",
        exit_code=0,
    )
    assert computer_suite._expect_keyboard_submit_reflected(ctx)


def test_expect_keyboard_submit_reflected_rejects_narration_without_submit(monkeypatch):
    # "TestUser" appearing in stdout is a false positive: the agent may narrate
    # "I filled TestUser" without the form ever being submitted. Only "custname"
    # (the httpbin JSON field key) confirms a real POST response.
    ctx = ResultContext(
        files={"result.txt": "TestUser submitted"},
        stdout="TestUser submitted",
        stderr="",
        exit_code=0,
    )
    assert not computer_suite._expect_keyboard_submit_reflected(ctx)


def test_expect_keyboard_submit_reflected_fails_on_error(monkeypatch):
    ctx = ResultContext(
        files={"result.txt": "Error: submission failed"},
        stdout="Error: submission failed",
        stderr="",
        exit_code=1,
    )
    assert not computer_suite._expect_keyboard_submit_reflected(ctx)


# ---------------------------------------------------------------------------
# _expect_dropdown_result_written / _expect_dropdown_value_echoed
# ---------------------------------------------------------------------------


def test_expect_dropdown_result_written_file_present():
    ctx = ResultContext(
        files={"dropdown.txt": "size=large"}, stdout="", stderr="", exit_code=0
    )
    assert computer_suite._expect_dropdown_result_written(ctx)


def test_expect_dropdown_result_written_via_stdout():
    ctx = ResultContext(files={}, stdout="Selected size: large", stderr="", exit_code=0)
    assert computer_suite._expect_dropdown_result_written(ctx)


def test_expect_dropdown_result_written_fails_empty():
    ctx = ResultContext(files={}, stdout="", stderr="", exit_code=0)
    assert not computer_suite._expect_dropdown_result_written(ctx)


def test_expect_dropdown_value_echoed_from_file():
    ctx = ResultContext(
        files={"dropdown.txt": "Page now shows: selected:large"},
        stdout="",
        stderr="",
        exit_code=0,
    )
    assert computer_suite._expect_dropdown_value_echoed(ctx)


def test_expect_dropdown_value_echoed_from_stdout_fallback():
    ctx = ResultContext(
        files={},
        stdout="result div now reads selected:large",
        stderr="",
        exit_code=0,
    )
    assert computer_suite._expect_dropdown_value_echoed(ctx)


def test_expect_dropdown_value_echoed_rejects_broad_terms():
    # A bare "large"/"medium"/"small" mention (without the "selected:" marker
    # written by the fixture's change-event listener) must NOT pass — that
    # marker only appears after a genuine select_option() call, not from
    # narration or static page text.
    for term in ("medium pizza selected", "small issue", "large pizza chosen"):
        ctx = ResultContext(files={}, stdout=term, stderr="", exit_code=0)
        assert not computer_suite._expect_dropdown_value_echoed(ctx), (
            f"should reject: {term!r}"
        )


def test_expect_dropdown_value_echoed_fails_unrelated_content():
    ctx = ResultContext(
        files={"dropdown.txt": "Error: form not found"},
        stdout="Error: form not found",
        stderr="",
        exit_code=1,
    )
    assert not computer_suite._expect_dropdown_value_echoed(ctx)


# ---------------------------------------------------------------------------
# check_used_hover_element
# ---------------------------------------------------------------------------


def test_check_used_hover_element_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["hover_element('#menu-trigger')"],
    )
    assert computer_suite.check_used_hover_element([])


def test_check_used_hover_element_rejects_click(monkeypatch):
    """click_element is not hover_element."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["click_element('#menu-trigger')"],
    )
    assert not computer_suite.check_used_hover_element([])


def test_check_used_hover_element_rejects_empty(monkeypatch):
    monkeypatch.setattr(computer_suite, "_executed_tool_calls", lambda messages: [])
    assert not computer_suite.check_used_hover_element([])


# ---------------------------------------------------------------------------
# check_used_snapshot_page
# ---------------------------------------------------------------------------


def test_check_used_snapshot_page_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["snapshot_page()"],
    )
    assert computer_suite.check_used_snapshot_page([])


def test_check_used_snapshot_page_rejects_snapshot_url(monkeypatch):
    """snapshot_url(url) is not snapshot_page()."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["snapshot_url('https://example.com')"],
    )
    assert not computer_suite.check_used_snapshot_page([])


def test_check_used_snapshot_page_rejects_empty(monkeypatch):
    monkeypatch.setattr(computer_suite, "_executed_tool_calls", lambda messages: [])
    assert not computer_suite.check_used_snapshot_page([])


# ---------------------------------------------------------------------------
# check_used_get_current_url
# ---------------------------------------------------------------------------


def test_check_used_get_current_url_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["get_current_url()"],
    )
    assert computer_suite.check_used_get_current_url([])


def test_check_used_get_current_url_rejects_observe_web(monkeypatch):
    """observe_web is not get_current_url."""
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["observe_web('https://example.com')"],
    )
    assert not computer_suite.check_used_get_current_url([])


def test_check_used_get_current_url_rejects_empty(monkeypatch):
    monkeypatch.setattr(computer_suite, "_executed_tool_calls", lambda messages: [])
    assert not computer_suite.check_used_get_current_url([])


# ---------------------------------------------------------------------------
# _expect_hover_menu_found
# ---------------------------------------------------------------------------


def test_expect_hover_menu_found_via_file(monkeypatch):
    ctx = ResultContext(
        files={"hover.txt": "The page now shows hover-revealed menu item"},
        stdout="",
        stderr="",
        exit_code=0,
    )
    assert computer_suite._expect_hover_menu_found(ctx)


def test_expect_hover_menu_found_via_stdout(monkeypatch):
    ctx = ResultContext(
        files={},
        stdout="hover-revealed item appeared",
        stderr="",
        exit_code=0,
    )
    assert computer_suite._expect_hover_menu_found(ctx)


def test_expect_hover_menu_found_rejects_no_marker():
    ctx = ResultContext(
        files={"hover.txt": "Error: element not found"},
        stdout="Error",
        stderr="",
        exit_code=1,
    )
    assert not computer_suite._expect_hover_menu_found(ctx)


# ---------------------------------------------------------------------------
# _expect_current_url_fixture_recorded / _expect_current_url_captured
# ---------------------------------------------------------------------------


def test_expect_current_url_fixture_recorded_accepts_fixture_url():
    ctx = ResultContext(
        files={"url.txt": computer_suite._CURRENT_URL_FIXTURE_URL},
        stdout=computer_suite._CURRENT_URL_FIXTURE_URL,
        stderr="",
        exit_code=0,
    )
    assert computer_suite._expect_current_url_fixture_recorded(ctx)


def test_expect_current_url_fixture_recorded_rejects_unrelated():
    ctx = ResultContext(
        files={"url.txt": "Error"},
        stdout="Error",
        stderr="",
        exit_code=1,
    )
    assert not computer_suite._expect_current_url_fixture_recorded(ctx)


def test_expect_current_url_captured_requires_nonempty():
    ctx = ResultContext(files={}, stdout="", stderr="", exit_code=0)
    assert not computer_suite._expect_current_url_captured(ctx)


def test_expect_current_url_captured_accepts_url():
    ctx = ResultContext(
        files={"url.txt": "https://example.com"},
        stdout="https://example.com",
        stderr="",
        exit_code=0,
    )
    assert computer_suite._expect_current_url_captured(ctx)


def test_check_used_save_browser_state_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["save_browser_state('state.json')"],
    )
    assert computer_suite.check_used_save_browser_state([])


def test_check_used_save_browser_state_rejects_open_page(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["open_page('https://x.com')"],
    )
    assert not computer_suite.check_used_save_browser_state([])


def test_check_used_load_browser_state_accepts(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["load_browser_state('state.json')"],
    )
    assert computer_suite.check_used_load_browser_state([])


def test_check_used_load_browser_state_rejects_save(monkeypatch):
    monkeypatch.setattr(
        computer_suite,
        "_executed_tool_calls",
        lambda messages: ["save_browser_state('state.json')"],
    )
    assert not computer_suite.check_used_load_browser_state([])
