import json
import logging
import os
import shutil
import textwrap
from collections.abc import Generator
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import islice, zip_longest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import (
    Any,
    Literal,
    TextIO,
    TypeAlias,
)

from dateutil.parser import isoparse
from rich import print

from .config import ChatConfig, get_project_config
from .dirs import get_logs_dir
from .message import Message, len_tokens, print_msg
from .tools import ToolUse
from .util.context import enrich_messages_with_context
from .util.reduce import limit_log, reduce_log
from .util.uri import URI

PathLike: TypeAlias = str | Path

logger = logging.getLogger(__name__)

RoleLiteral = Literal["user", "assistant", "system"]


@dataclass(frozen=True, repr=False)
class Log:
    messages: list[Message] = field(default_factory=list)

    def __getitem__(self, key):
        return self.messages[key]

    def __len__(self) -> int:
        return len(self.messages)

    def len_tokens(self, model: str) -> int:
        return len_tokens(self.messages, model)

    def __iter__(self) -> Generator[Message, None, None]:
        yield from self.messages

    def __repr__(self) -> str:
        return f"Log(messages=<{len(self.messages)} msgs>])"

    def replace(self, **kwargs) -> "Log":
        return replace(self, **kwargs)

    def append(self, msg: Message) -> "Log":
        return self.replace(messages=self.messages + [msg])

    def pop(self) -> "Log":
        return self.replace(messages=self.messages[:-1])

    @classmethod
    def read_jsonl(cls, path: PathLike, limit=None) -> "Log":
        gen = _gen_read_jsonl(path)
        if limit:
            gen = islice(gen, limit)  # type: ignore[assignment]
        return Log(list(gen))

    def write_jsonl(self, path: PathLike) -> None:
        with open(path, "w") as file:
            for msg in self.messages:
                file.write(json.dumps(msg.to_dict()) + "\n")

    def print(self, show_hidden: bool = False):
        print_msg(self.messages, oneline=False, show_hidden=show_hidden)


# Context-local storage for current LogManager instance
# Each context (thread/async task) gets its own independent reference
_current_log_var: ContextVar["LogManager | None"] = ContextVar(
    "current_log", default=None
)


class LogManager:
    """Manages a conversation log.

    Can be used as a context manager to ensure locks are properly released:
        with LogManager.load(logdir) as manager:
            # use manager
            pass
    """

    _lock_fd: TextIO | None = None
    _tmpdir: TemporaryDirectory | None = None  # Store to prevent premature GC

    @classmethod
    def get_current_log(cls) -> "LogManager | None":
        """Get the current LogManager instance for this context."""
        return _current_log_var.get()

    def __enter__(self) -> "LogManager":
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager, ensuring lock is released."""
        self._release_lock()
        return

    def __init__(
        self,
        log: list[Message] | None = None,
        logdir: PathLike | None = None,
        branch: str | None = None,
        lock: bool = True,
        view: str | None = None,
    ):
        self.current_branch = branch or "main"
        # View branch support: compacted views stored separately from user branches
        # When current_view is set, new messages go to BOTH master AND the view
        self.current_view: str | None = view
        if logdir:
            self.logdir = Path(logdir)
        else:
            # generate tmpfile - store TemporaryDirectory instance to prevent
            # premature garbage collection and ensure proper cleanup
            self._tmpdir = TemporaryDirectory()
            logger.warning(
                f"No logfile specified, using tmpfile at {self._tmpdir.name}"
            )
            self.logdir = Path(self._tmpdir.name)
        self.chat_id = self.logdir.name

        # Set as current log for tools to access (context-local)
        _current_log_var.set(self)

        # Create and optionally lock the directory
        self.logdir.mkdir(parents=True, exist_ok=True)
        is_pytest = "PYTEST_CURRENT_TEST" in os.environ
        if lock and not is_pytest and os.name != "nt":
            self._lockfile = self.logdir / ".lock"
            self._lockfile.touch(exist_ok=True)
            self._lock_fd = self._lockfile.open("r+")

            self._acquire_lock()

        # load branches from adjacent files
        self._branches = {self.current_branch: Log(log or [])}
        if self.logdir / "conversation.jsonl":
            _branch = "main"
            if _branch not in self._branches:
                self._branches[_branch] = Log.read_jsonl(
                    self.logdir / "conversation.jsonl"
                )
        for file in self.logdir.glob("branches/*.jsonl"):
            if file.name == self.logdir.name:
                continue
            _branch = file.stem
            if _branch not in self._branches:
                self._branches[_branch] = Log.read_jsonl(file)

        # Load view branches (compacted views stored in views/ directory)
        self._views: dict[str, Log] = {}
        views_dir = self.logdir / "views"
        if views_dir.exists():
            for file in views_dir.glob("*.jsonl"):
                view_name = file.stem
                self._views[view_name] = Log.read_jsonl(file)
                logger.debug(f"Loaded view branch: {view_name}")

        # If a view was requested, load it as the active log
        if self.current_view and self.current_view in self._views:
            # When on a view, the "current" log is the view
            # but we track master separately for dual-write
            pass  # View is already loaded in _views

    def _acquire_lock(self):
        """Acquire an exclusive lock on the conversation directory.

        Handles:
        - Same-process detection (for server mode)
        - Stale lock recovery (dead process left lock)
        - PID tracking for debugging
        """
        import atexit
        import fcntl

        assert self._lock_fd is not None, "_acquire_lock called without open fd"

        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock acquired - write our PID (seek/truncate first to clear old content)
            self._lock_fd.seek(0)
            self._lock_fd.truncate()
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
            atexit.register(self._release_lock)
        except BlockingIOError:
            # Lock is held - check if it's us or another process
            lock_pid = self._read_lock_pid()

            if lock_pid == os.getpid():
                # Same process - we already hold this lock (server mode)
                # Close the duplicate fd, we don't need it
                self._lock_fd.close()
                self._lock_fd = None
                return

            if lock_pid is not None:
                # Check if that process is still running
                try:
                    os.kill(lock_pid, 0)  # Signal 0 just checks if process exists
                    # Process is alive - can't acquire lock
                    self._lock_fd.close()
                    self._lock_fd = None
                    raise RuntimeError(
                        f"Another gptme instance (PID {lock_pid}) is using {self.logdir}"
                    )
                except ProcessLookupError:
                    # Process is dead - stale lock, recover it
                    logger.warning(f"Removing stale lock from dead process {lock_pid}")
                    assert self._lock_fd is not None
                    self._lock_fd.close()
                    self._lockfile.unlink()
                    self._lockfile.touch(exist_ok=True)
                    self._lock_fd = self._lockfile.open("r+")
                    # Retry lock acquisition
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._lock_fd.seek(0)
                    self._lock_fd.truncate()
                    self._lock_fd.write(str(os.getpid()))
                    self._lock_fd.flush()
                    atexit.register(self._release_lock)
                    return

            # Could not read PID - fail with generic message
            self._lock_fd.close()
            self._lock_fd = None
            raise RuntimeError(
                f"Another gptme instance is using {self.logdir}"
            ) from None

    def _read_lock_pid(self) -> int | None:
        """Read PID from lock file, or None if unreadable."""
        try:
            with open(self._lockfile) as f:
                lock_pid_str = f.read().strip()
                if lock_pid_str.isdigit():
                    return int(lock_pid_str)
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read lock holder: {e}")
        return None

    def _release_lock(self):
        """Release the lock and close the file descriptor"""
        if self._lock_fd:
            import fcntl

            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
                self._lock_fd = None
                # logger.debug(f"Released lock on {self.logdir}")
            except (OSError, ValueError) as e:
                logger.warning(f"Error releasing lock: {e}")

    def __del__(self):
        """Release the lock on garbage collection"""
        self._release_lock()

    @property
    def workspace(self) -> Path:
        """Path to workspace directory (resolves symlink if exists)."""
        return (self.logdir / "workspace").resolve()

    @property
    def log(self) -> Log:
        # If viewing a compacted view, return that; otherwise return branch log
        if self.current_view is not None:
            return self._views[self.current_view]
        return self._branches[self.current_branch]

    @log.setter
    def log(self, value: Log | list[Message]) -> None:
        if isinstance(value, list):
            value = Log(value)
        self._branches[self.current_branch] = value

    @property
    def logfile(self) -> Path:
        if self.current_branch == "main":
            return get_logs_dir() / self.chat_id / "conversation.jsonl"
        return self.logdir / "branches" / f"{self.current_branch}.jsonl"

    @property
    def name(self) -> str:
        """Get the user-friendly display name from ChatConfig, fallback to chat_id."""
        chat_config = ChatConfig.from_logdir(self.logdir)
        return chat_config.name or self.chat_id

    def append(self, msg: Message) -> None:
        """Appends a message to the log, writes the log, prints the message.

        When on a view branch, implements dual-write:
        - Appends to master branch (preserves full history)
        - Appends to current view (maintains compacted context)
        """
        # Store files by content hash and update message with hashes
        msg = self._store_message_files(msg)

        # If on a view branch, dual-write to both master AND view
        if self.current_view and self.current_view in self._views:
            # Append to master (main branch) for full history preservation
            if "main" in self._branches:
                self._branches["main"] = self._branches["main"].append(msg)
            # Also append to the current view
            # (log getter returns view when current_view is set, no setter needed)
            self._views[self.current_view] = self._views[self.current_view].append(msg)
        else:
            # Not on a view, append to current branch normally (no dual-write)
            self.log = self.log.append(msg)

        self.write()
        if not msg.quiet:
            print_msg(msg, oneline=False)

    def _store_message_files(self, msg: Message) -> Message:
        """Store attached files by content hash and return updated message."""
        if not msg.files:
            return msg

        from .util.file_storage import store_file

        file_hashes = dict(msg.file_hashes)  # Start with existing hashes
        for filepath in msg.files:
            # Skip URIs - they can't be stored locally
            if isinstance(filepath, URI):
                continue
            if not filepath.exists():
                continue
            # Store by hash and record the mapping
            file_hash, stored_name = store_file(self.logdir, filepath)
            # Use full path as key to avoid collisions with same-named files
            file_hashes[str(filepath)] = file_hash

        # Return message with updated hashes (Message is frozen, so replace)
        return replace(msg, file_hashes=file_hashes)

    def write(self, branches=True, sync=False) -> None:
        """
        Writes to the conversation log.

        Args:
            branches: Whether to write other branches
            sync: If True, force fsync to ensure data is on disk
        """
        # create directory if it doesn't exist
        Path(self.logfile).parent.mkdir(parents=True, exist_ok=True)

        # write current branch
        self.log.write_jsonl(self.logfile)

        # write other branches
        if branches:
            branches_dir = self.logdir / "branches"
            branches_dir.mkdir(parents=True, exist_ok=True)
            for branch, log in self._branches.items():
                if branch == "main":
                    # when on a non-main branch, also persist main to conversation.jsonl
                    if self.current_branch != "main":
                        main_path = get_logs_dir() / self.chat_id / "conversation.jsonl"
                        main_path.parent.mkdir(parents=True, exist_ok=True)
                        log.write_jsonl(main_path)
                    continue
                branch_path = branches_dir / f"{branch}.jsonl"
                log.write_jsonl(branch_path)

            # Write view branches
            if self._views:
                views_dir = self.logdir / "views"
                views_dir.mkdir(parents=True, exist_ok=True)
                for view_name, log in self._views.items():
                    view_path = views_dir / f"{view_name}.jsonl"
                    log.write_jsonl(view_path)

        # Force sync to disk if requested
        if sync:
            with open(self.logfile, "rb") as f:
                os.fsync(f.fileno())

    def _save_backup_branch(self, type="edit") -> None:
        """backup the current log to a new branch, usually before editing/undoing"""
        branch_prefix = f"{self.current_branch}-{type}-"
        n = len([b for b in self._branches if b.startswith(branch_prefix)])
        self._branches[f"{branch_prefix}{n}"] = self.log
        self.write()

    def edit(self, new_log: Log | list[Message]) -> None:
        """Edits the log."""
        if isinstance(new_log, list):
            new_log = Log(new_log)
        self._save_backup_branch(type="edit")
        self.log = new_log
        self.write()

    def undo(self, n: int = 1, quiet=False) -> None:
        """Removes the last message from the log."""
        undid = self.log[-1] if self.log else None
        if undid and undid.content.startswith("/undo"):
            self.log = self.log.pop()

        # don't save backup branch if undoing a command
        if self.log and not self.log[-1].content.startswith("/"):
            self._save_backup_branch(type="undo")

        peek = self.log[-1] if self.log else None
        if not peek:
            print("[yellow]Nothing to undo.[/]")
            return

        if not quiet:
            print("[yellow]Undoing messages:[/yellow]")
        for _ in range(n):
            undid = self.log[-1]
            self.log = self.log.pop()
            if not quiet:
                print(
                    f"[red]  {undid.role}: {textwrap.shorten(undid.content.strip(), width=50, placeholder='...')}[/]",
                )
            peek = self.log[-1] if self.log else None

    @classmethod
    def load(
        cls,
        logdir: PathLike,
        initial_msgs: list[Message] | None = None,
        branch: str = "main",
        create: bool = False,
        lock: bool = True,
        **kwargs,
    ) -> "LogManager":
        """Loads a conversation log."""
        if str(logdir).endswith(".jsonl"):
            logdir = Path(logdir).parent

        logsdir = get_logs_dir()
        if str(logsdir) not in str(logdir):
            # if the path was not fully specified, assume its a dir in logsdir
            logdir = logsdir / logdir
        else:
            logdir = Path(logdir)

        if branch == "main":
            logfile = logdir / "conversation.jsonl"
        else:
            logfile = logdir / f"branches/{branch}.jsonl"

        if not Path(logfile).exists():
            if create:
                # logger.debug(f"Creating new logfile {logfile}")
                Path(logfile).parent.mkdir(parents=True, exist_ok=True)
                Log([]).write_jsonl(logfile)
            else:
                raise FileNotFoundError(f"Could not find logfile {logfile}")

        log = Log.read_jsonl(logfile)
        msgs = log.messages or initial_msgs or []
        return cls(msgs, logdir=logdir, branch=branch, lock=lock, **kwargs)

    def branch(self, name: str) -> None:
        """Switches to a branch."""
        self.write()
        if name not in self._branches:
            logger.info(f"Creating a new branch '{name}'")
            self._branches[name] = self.log
        self.current_branch = name

    def diff(self, branch: str) -> str | None:
        """Prints the diff between the current branch and another branch."""
        if branch not in self._branches:
            logger.warning(f"Branch '{branch}' does not exist.")
            return None

        # walk the log forwards until we find a message that is different
        diff_i: int | None = None
        for i, (msg1, msg2) in enumerate(zip_longest(self.log, self._branches[branch])):
            diff_i = i
            if msg1 != msg2:
                break
        else:
            # no difference
            return None

        # output the continuing messages on the current branch as +
        # and the continuing messages on the other branch as -
        diff = []
        for msg in self.log[diff_i:]:
            diff.append(f"+ {msg.format()}")
        for msg in self._branches[branch][diff_i:]:
            diff.append(f"- {msg.format()}")

        if diff:
            return "\n".join(diff)
        return None

    # ==================== View Branch Methods ====================
    # Views are compacted versions of the conversation stored separately.
    # When on a view, new messages go to BOTH master AND the view (dual-write).

    def create_view(self, name: str, log: Log | list[Message]) -> None:
        """Create a new view branch with compacted content.

        Args:
            name: View name (e.g., 'compacted-001')
            log: The compacted log to store
        """
        if isinstance(log, list):
            log = Log(log)
        self._views[name] = log

        # Write to views directory
        views_dir = self.logdir / "views"
        views_dir.mkdir(parents=True, exist_ok=True)
        view_path = views_dir / f"{name}.jsonl"
        log.write_jsonl(view_path)
        logger.info(f"Created view branch: {name} ({len(log)} messages)")

    def switch_view(self, name: str) -> None:
        """Switch to a view branch.

        Args:
            name: View name to switch to
        """
        if name not in self._views:
            raise ValueError(f"View '{name}' does not exist")
        self.write()  # Save current state first
        self.current_view = name
        # log getter now returns view when current_view is set
        logger.info(f"Switched to view: {name}")

    def switch_to_master(self) -> None:
        """Switch back to master (full uncompacted history)."""
        self.write()  # Save current state first
        self.current_view = None
        # log getter now returns branch when current_view is None
        logger.info("Switched to master branch")

    def get_next_view_name(self) -> str:
        """Generate the next sequential view name."""
        existing = [
            int(v.split("-")[1])
            for v in self._views
            if v.startswith("compacted-") and v.split("-")[1].isdigit()
        ]
        next_num = max(existing, default=0) + 1
        return f"compacted-{next_num:03d}"

    @property
    def master_log(self) -> Log:
        """Get the master log (always the main branch, never compacted)."""
        return self._branches.get("main", self._branches[self.current_branch])

    def fork(self, name: str) -> None:
        """
        Copy the conversation folder to a new name.
        """
        self.write()
        logsdir = get_logs_dir()
        shutil.copytree(self.logfile.parent, logsdir / name, symlinks=True)
        self.logdir = logsdir / name
        self.chat_id = name
        self.write()

    def to_dict(self, branches=False) -> dict:
        """Returns a dict representation of the log."""
        d: dict[str, Any] = {
            "id": self.chat_id,
            "name": self.name,
            "log": [msg.to_dict() for msg in self.log],
            "logfile": str(self.logfile),
        }
        if branches:
            d["branches"] = {
                branch: [msg.to_dict() for msg in msgs]
                for branch, msgs in self._branches.items()
            }
        return d


def prepare_messages(
    msgs: list[Message], workspace: Path | None = None
) -> list[Message]:
    """
    Prepares the messages before sending to the LLM.
    - Takes the stored gptme conversation log
    - Enhances it with context such as file contents
    - Transforms it to the format expected by LLM providers
    """

    from gptme.llm.models import get_default_model  # fmt: skip

    # Enrich with enabled context enhancements (RAG, fresh context)
    msgs = enrich_messages_with_context(msgs, workspace)

    # Use regular reduction
    msgs_reduced = list(reduce_log(msgs))

    model = get_default_model()
    assert model is not None, "No model loaded"
    if (len_from := len_tokens(msgs, model.model)) != (
        len_to := len_tokens(msgs_reduced, model.model)
    ):
        logger.info(f"Reduced log from {len_from // 1} to {len_to // 1} tokens")
    msgs_limited = limit_log(msgs_reduced)
    if len(msgs_reduced) != len(msgs_limited):
        logger.info(
            f"Limited log from {len(msgs_reduced)} to {len(msgs_limited)} messages"
        )

    return msgs_limited


def _conversation_files() -> list[Path]:
    # NOTE: only returns the main conversation, not branches (to avoid duplicates)
    # returns the conversation files sorted by modified time (newest first)
    logsdir = get_logs_dir()
    return list(
        sorted(logsdir.glob("*/conversation.jsonl"), key=lambda f: -f.stat().st_mtime)
    )


@dataclass(frozen=True)
class ConversationMeta:
    """Metadata about a conversation."""

    id: str
    name: str
    path: str
    created: float
    modified: float
    messages: int
    branches: int
    workspace: str
    agent_name: str | None = None
    agent_path: str | None = None

    def format(self, metadata=False) -> str:
        """Format conversation metadata for display."""
        output = f"{self.name} (id: {self.id})"
        if metadata:
            output += f"\nMessages: {self.messages}"
            output += f"\nCreated:  {datetime.fromtimestamp(self.created)}"
            output += f"\nModified: {datetime.fromtimestamp(self.modified)}"
            if self.branches > 1:
                output += f"\n({self.branches} branches)"
        return output


def get_conversations() -> Generator[ConversationMeta, None, None]:
    """Returns all conversations, excluding ones used for testing, evals, etc."""
    for conv_fn in _conversation_files():
        log = Log.read_jsonl(conv_fn, limit=1)
        # TODO: can we avoid reading the entire file? maybe wont even be used, due to user convo filtering
        len_msgs = 1 + conv_fn.read_text().count("}\n{")
        assert len(log) <= 1
        modified = conv_fn.stat().st_mtime
        first_timestamp = log[0].timestamp.timestamp() if log else modified
        # Try to get display name from ChatConfig, fallback to folder name
        conv_id = conv_fn.parent.name
        chat_config = ChatConfig.from_logdir(conv_fn.parent)
        display_name = chat_config.name or conv_id

        agent_path = chat_config.agent
        agent_project_config = (
            get_project_config(agent_path, quiet=True) if agent_path else None
        )
        agent_name = (
            agent_project_config.agent.name
            if agent_project_config and agent_project_config.agent
            else None
        )

        yield ConversationMeta(
            id=conv_id,
            name=display_name,
            path=str(conv_fn),
            created=first_timestamp,
            modified=modified,
            messages=len_msgs,
            branches=1 + len(list(conv_fn.parent.glob("branches/*.jsonl"))),
            workspace=str(chat_config.workspace),
            agent_name=agent_name,
            agent_path=str(agent_path) if agent_path else None,
        )


def get_user_conversations() -> Generator[ConversationMeta, None, None]:
    """Returns all user conversations, excluding ones used for testing, evals, etc."""
    for conv in get_conversations():
        if any(conv.id.startswith(prefix) for prefix in ["tmp", "test-"]) or any(
            substr in conv.id for substr in ["gptme-evals-"]
        ):
            continue
        yield conv


def list_conversations(
    limit: int = 20,
    include_test: bool = False,
) -> list[ConversationMeta]:
    """
    List conversations with a limit.

    Args:
        limit: Maximum number of conversations to return
        include_test: Whether to include test conversations
    """
    conversation_iter = (
        get_conversations() if include_test else get_user_conversations()
    )
    return list(islice(conversation_iter, limit))


def get_conversation_by_id(conv_id: str) -> ConversationMeta | None:
    """
    Get a conversation by its ID.

    Args:
        conv_id: The conversation ID to find

    Returns:
        ConversationMeta if found, None otherwise
    """
    for conv in get_conversations():
        if conv.id == conv_id:
            return conv
    return None


def delete_conversation(conv_id: str) -> bool:
    """
    Delete a conversation by its ID.

    Args:
        conv_id: The conversation ID to delete

    Returns:
        True if deleted successfully, False if not found

    Raises:
        PermissionError: If the conversation directory cannot be deleted
    """
    conv = get_conversation_by_id(conv_id)
    if conv is None:
        return False

    # Get the conversation directory (parent of conversation.jsonl)
    conv_path = Path(conv.path)
    conv_dir = conv_path.parent

    # Delete the entire conversation directory
    shutil.rmtree(conv_dir)
    return True


def check_for_modifications(log: Log) -> bool:
    """Check if the most recent assistant message (since last user) has file modifications.

    Only checks the last assistant message to prevent re-triggering pre-commit/autocommit
    when the agent responds to hook output (e.g. pre-commit failure) without making new
    file changes. Checking all assistant messages since the last user would cause infinite
    loops: the original save is still visible after the agent writes a text response to
    the failure.
    """
    last_assistant: Message | None = None
    found_user = False

    for m in reversed(log):
        if m.role == "user":
            found_user = True
            break
        if m.role == "system":
            continue  # Skip system messages (tool results, hook outputs)
        if last_assistant is None:
            last_assistant = m  # Record only the most recent assistant message

    if not found_user or last_assistant is None:
        return False

    return any(
        tu.tool in ["save", "patch", "append", "morph"]
        for tu in ToolUse.iter_from_content(last_assistant.content)
    )


def _gen_read_jsonl(path: PathLike) -> Generator[Message, None, None]:
    from .util.uri import parse_file_reference

    with open(path) as file:
        for line in file.readlines():
            line = line.strip()
            if not line:
                continue
            try:
                json_data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"Skipping malformed JSON line in {path}")
                continue
            files = [parse_file_reference(f) for f in json_data.pop("files", [])]
            file_hashes = json_data.pop("file_hashes", {})
            if "timestamp" in json_data:
                json_data["timestamp"] = isoparse(json_data["timestamp"])
            yield Message(**json_data, files=files, file_hashes=file_hashes)
