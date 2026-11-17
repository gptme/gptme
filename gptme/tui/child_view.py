"""View-only child inspection. No LogManager, writer lock or parent context swap."""

import asyncio
import json
from pathlib import Path
from typing import Literal, cast

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Collapsible, Static

from ..message import Message, MessageMetadata
from ..tools.subagent import SubagentSnapshot
from .app import MAX_INITIAL_MESSAGES, ChatInput, ChatScroll, GptmeApp, message_widget


def read_child_messages(logdir: Path) -> tuple[list[Message], str | None]:
    """Read complete JSONL records only; the writer may be midway through a line."""
    try:
        # Bound disk work as well as mounted messages. Discard a cut first line.
        with (logdir / "conversation.jsonl").open("rb") as stream:
            size = stream.seek(0, 2)
            start = max(0, size - 2 * 1024 * 1024)
            stream.seek(start)
            raw = stream.read().decode("utf-8", errors="replace")
        if start:
            raw = raw.partition("\n")[2]
    except OSError as exc:
        return [], f"Could not read child log: {exc}"
    messages: list[Message] = []
    for line in raw.split("\n")[:-1]:
        try:
            data = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or data.get("role") not in (
            "user",
            "assistant",
            "system",
        ):
            continue
        content = data.get("content")
        if not isinstance(content, str):
            continue
        metadata = data.get("metadata")
        messages.append(
            Message(
                cast(Literal["user", "assistant", "system"], data["role"]),
                content,
                hide=data.get("hide") is True,
                metadata=cast(MessageMetadata, metadata)
                if isinstance(metadata, dict)
                else None,
            )
        )
    return messages[-MAX_INITIAL_MESSAGES:], None


def send_child_message(
    child: SubagentSnapshot,
    owner: Path,
    branch: str,
    text: str,
    *,
    continue_finished: bool = False,
) -> str:
    """Route only to the selected parent-owned registry run, never an ID lookup on disk."""
    from ..tools.subagent import subagent_continue, subagent_snapshots, subagent_steer

    owned = subagent_snapshots(owner, parent_branch=branch)
    if not any(
        c.run_id == child.run_id
        and c.started_at == child.started_at
        and c.logdir.resolve() == child.logdir.resolve()
        for c in owned
    ):
        raise ValueError("Selected child does not belong to this parent and branch.")
    from ..tools.subagent.api import _find_subagent
    from ..tools.subagent.types import _subagents, _subagents_lock

    # Agent IDs are only unique per conversation: make this parent's run the
    # agent_id holder if a finished run of another conversation shadowed it.
    _find_subagent(child.agent_id, owner=owner, control=True)

    with _subagents_lock:
        latest = next(
            (s for s in reversed(_subagents) if s.agent_id == child.agent_id), None
        )
        if (
            latest is None
            or latest.logdir != child.logdir.resolve()
            or latest.started_at != child.started_at
            or latest.parent_logdir != owner.resolve()
            or (latest.parent_branch or "main") != branch
        ):
            raise ValueError(
                "Selected run is no longer the parent's current child. Reopen Work overview."
            )
    if continue_finished:
        subagent_continue(
            child.agent_id,
            text,
            expected_run=(child.logdir.resolve(), child.started_at),
        )
        return "Child continuation started."
    return subagent_steer(
        child.agent_id, text, expected_run=(child.logdir.resolve(), child.started_at)
    )


class ChildInput(ChatInput):
    """Child prompts are literal messages, not parent slash commands."""

    _submission_pending = False

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            # Unlike parent submission, child delivery can be rejected. Retain
            # the expansion mappings until the selected draft is delivered.
            self._clear_completions()
            if self._submission_pending:
                return
            self._submission_pending = True
            self.post_message(self.Submitted(self._expand_pastes(self.text)))
            return
        await super()._on_key(event)

    def _cycle_completion(self) -> None:
        return  # Do not advertise slash commands that operate on the parent.


class ChildView(Vertical):
    """Separate mounted transcript/composer; parent widgets stay alive underneath."""

    DEFAULT_CSS = """
    ChildView { height: 1fr; background: ansi_default; }
    #child-toolbar { height: auto; }
    #child-banner { width: 1fr; height: auto; padding: 0 1; color: $warning; }
    #child-back { min-width: 18; height: 3; }
    #child-chat { height: 1fr; padding: 0 1; background: ansi_default; }
    #child-target, #child-feedback, #child-parent-status { height: auto; padding: 0 1; }
    #child-input { height: auto; max-height: 10; border: round $warning; }
    """

    def __init__(
        self,
        child: SubagentSnapshot,
        owner: Path,
        branch: str,
        *,
        show_thinking: bool = False,
        highlight: bool = True,
    ) -> None:
        super().__init__(id="child-view")
        self.child = child
        self.owner = owner
        self.branch = branch
        self.show_thinking = show_thinking
        self.highlight = highlight
        self._messages: list[Message] = []
        self._refreshing = False
        self._parent_seen = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="child-toolbar"):
            yield Static(Text("Loading child…"), id="child-banner")
            yield Button("Back to parent", id="child-back")
        yield ChatScroll(id="child-chat")
        yield Static(id="child-parent-status")
        yield Static(id="child-target")
        yield Static(id="child-feedback")
        yield ChildInput(id="child-input")

    async def on_mount(self) -> None:
        assert isinstance(self.app, GptmeApp)
        self._parent_seen = len(self.app.manager.log)
        editor = self.query_one("#child-input", ChatInput)
        if saved := self.app._child_drafts.get(self.child.run_id):
            editor.text, editor.selection, editor._pastes = saved
        editor.focus()
        await self.refresh_snapshot()
        self.set_interval(1.0, self.refresh_snapshot)

    def update_parent_status(self) -> None:
        assert isinstance(self.app, GptmeApp)
        if not self.is_attached:
            return
        unread = max(0, len(self.app.manager.log) - self._parent_seen)
        pending = (
            " · confirmation waiting — Back to parent"
            if self.app._pending_parent_confirm
            else ""
        )
        self.query_one("#child-parent-status", Static).update(
            Text(f"Parent: {self.app.state} · {unread} new messages{pending}")
        )

    def _update_banner(self) -> None:
        self.update_parent_status()
        model = next(
            (
                m.metadata.get("model")
                for m in reversed(self._messages)
                if m.metadata and m.metadata.get("model")
            ),
            "unknown",
        )
        self.query_one("#child-banner", Static).update(
            Text(
                f"Child: {self.child.agent_id} · {self.child.status} · model: {model}\n"
                f"Run: {self.child.logdir.name} · Esc/Ctrl+C back to parent"
            )
        )
        target = (
            "Enter: steer running child"
            if self.child.status == "running"
            else "Enter does not resume. To explicitly continue: /continue <message>"
        )
        self.query_one("#child-target", Static).update(
            Text(f"Target: child {self.child.agent_id} — {target}")
        )

    async def refresh_snapshot(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            await self._read_snapshot()
        finally:
            self._refreshing = False

    async def _read_snapshot(self) -> None:
        from ..tools.subagent import subagent_snapshots

        children = await asyncio.to_thread(
            subagent_snapshots, self.owner, parent_branch=self.branch
        )
        matching = next(
            (
                c
                for c in children
                if c.run_id == self.child.run_id
                and c.started_at == self.child.started_at
            ),
            None,
        )
        if matching is not None:
            self.child = matching
        messages, error = await asyncio.to_thread(
            read_child_messages, self.child.logdir
        )
        if not self.is_attached:
            return
        # Append-only refresh preserves existing child scroll and expansions.
        shown = [m for m in messages if not m.hide]
        chat = self.query_one("#child-chat", ChatScroll)
        at_end = not self._messages or chat.is_vertical_scroll_end
        if shown[: len(self._messages)] != self._messages:
            # The bounded tail rolls forward. Preserve overlapping widgets (and
            # their expansion state), removing only records that left the tail.
            overlap = next(
                (
                    n
                    for n in range(min(len(shown), len(self._messages)), 0, -1)
                    if self._messages[-n:] == shown[:n]
                ),
                0,
            )
            # read_child_messages admits only roles with a message widget.
            dropped = len(self._messages) - overlap
            for dropped_widget in list(chat.children)[:dropped]:
                await dropped_widget.remove()
            self._messages = self._messages[-overlap:] if overlap else []
        for msg in shown[len(self._messages) :]:
            widget = message_widget(
                msg, show_thinking=self.show_thinking, highlight=self.highlight
            )
            if widget is not None:
                await chat.mount(widget)
        self._messages = shown
        self._update_banner()
        if error:
            self.query_one("#child-feedback", Static).update(Text(error))
        if at_end:
            chat.scroll_end(animate=False)

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        event.stop()
        editor = self.query_one("#child-input", ChildInput)
        try:
            await self._submit(event.value)
        finally:
            # Release at the producer too: Textual queues async handlers, so a
            # handler-only guard cannot reject a second already-posted Enter.
            editor._submission_pending = False

    async def _submit(self, value: str) -> None:
        text = value.strip()
        app = self.app
        assert isinstance(app, GptmeApp)
        send_key = (self.child.run_id, self.child.started_at)
        if not text or send_key in app._child_sends:
            return
        continuation = text.startswith("/continue ")
        if self.child.status != "running" and not continuation:
            self.query_one("#child-feedback", Static).update(
                Text(
                    "Child is not running. Use /continue <message> to explicitly resume."
                )
            )
            return
        message = text[len("/continue ") :].strip() if continuation else text
        if not message:
            return
        editor = self.query_one("#child-input", ChatInput)
        submitted = (editor.text, dict(editor._pastes))
        app._child_sends.add(send_key)
        try:
            result = await asyncio.to_thread(
                send_child_message,
                self.child,
                self.owner,
                self.branch,
                message,
                continue_finished=continuation,
            )
        except Exception as exc:
            # Keep the draft for retry; never fall back to the parent or a direct write.
            result = f"Could not send to child: {exc}"
        else:
            if continuation:
                from ..tools.subagent import subagent_snapshots

                children = await asyncio.to_thread(
                    subagent_snapshots, self.owner, parent_branch=self.branch
                )
                continued = next(
                    (c for c in children if c.run_id == self.child.run_id), None
                )
                if continued is not None:
                    self.child = continued
                    current = app._child_view
                    if (
                        isinstance(current, ChildView)
                        and (current.child.run_id, current.child.started_at) == send_key
                    ):
                        current.child = continued
                        current._update_banner()
            # Editing may continue while delivery runs off-loop. Never erase a
            # newer instruction (including its collapsed paste mappings).
            current = app._child_view
            if isinstance(current, ChildView) and current.child.run_id == send_key[0]:
                current_editor = current.query_one("#child-input", ChatInput)
                if (current_editor.text, current_editor._pastes) == submitted:
                    current_editor.text = ""
                    current_editor._pastes.clear()
            saved = app._child_drafts.get(send_key[0])
            if saved and (saved[0], saved[2]) == submitted:
                app._child_drafts.pop(send_key[0], None)
        finally:
            app._child_sends.discard(send_key)
        if self.is_attached:
            self.query_one("#child-feedback", Static).update(Text(result))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "child-back":
            event.stop()
            assert isinstance(self.app, GptmeApp)
            self.app.close_child()

    def toggle_details(self) -> None:
        blocks = list(self.query(Collapsible))
        collapse = bool(blocks) and all(not b.collapsed for b in blocks)
        for block in blocks:
            block.collapsed = collapse
