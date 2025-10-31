import fcntl
import json
import logging
import os
import shutil
import textwrap
from collections.abc import Generator
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
            gen = islice(gen, limit)  # type: ignore
        return Log(list(gen))

    def write_jsonl(self, path: PathLike) -> None:
        with open(path, "w") as file:
            for msg in self.messages:
                file.write(json.dumps(msg.to_dict()) + "\n")

    def print(self, show_hidden: bool = False):
        print_msg(self.messages, oneline=False, show_hidden=show_hidden)


class LogManager:
    """Manages a conversation log."""

    _lock_fd: TextIO | None = None

    def __init__(
        self,
        log: list[Message] | None = None,
        logdir: PathLike | None = None,
        branch: str | None = None,
        lock: bool = True,
    ):
        self.current_branch = branch or "main"
        if logdir:
            self.logdir = Path(logdir)
        else:
            # generate tmpfile
            fpath = TemporaryDirectory().name
            logger.warning(f"No logfile specified, using tmpfile at {fpath}")
            self.logdir = Path(fpath)
        self.chat_id = self.logdir.name

        # Create and optionally lock the directory
        self.logdir.mkdir(parents=True, exist_ok=True)
        is_pytest = "PYTEST_CURRENT_TEST" in os.environ
        if lock and not is_pytest:
            self._lockfile = self.logdir / ".lock"
            self._lockfile.touch(exist_ok=True)
            self._lock_fd = self._lockfile.open("w")

            # Try to acquire an exclusive lock
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # logger.debug(f"Acquired lock on {self.logdir}")
            except BlockingIOError:
                self._lock_fd.close()
                self._lock_fd = None
                raise RuntimeError(
                    f"Another gptme instance is using {self.logdir}"
                ) from None

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

    def __del__(self):
        """Release the lock and close the file descriptor"""
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
                # logger.debug(f"Released lock on {self.logdir}")
            except Exception as e:
                logger.warning(f"Error releasing lock: {e}")

    @property
    def workspace(self) -> Path:
        """Path to workspace directory (resolves symlink if exists)."""
        return (self.logdir / "workspace").resolve()

    @property
    def log(self) -> Log:
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
        """Appends a message to the log, writes the log, prints the message."""
        self.log = self.log.append(msg)
        self.write()
        if not msg.quiet:
            print_msg(msg, oneline=False)

    def write(self, branches=True) -> None:
        """
        Writes to the conversation log.
        """
        # create directory if it doesn't exist
        Path(self.logfile).parent.mkdir(parents=True, exist_ok=True)

        # write current branch
        self.log.write_jsonl(self.logfile)

        # write other branches
        # FIXME: wont write main branch if on a different branch
        if branches:
            branches_dir = self.logdir / "branches"
            branches_dir.mkdir(parents=True, exist_ok=True)
            for branch, log in self._branches.items():
                if branch == "main":
                    continue
                branch_path = branches_dir / f"{branch}.jsonl"
                log.write_jsonl(branch_path)

    def _save_backup_branch(self, type="edit") -> None:
        """backup the current log to a new branch, usually before editing/undoing"""
        branch_prefix = f"{self.current_branch}-{type}-"
        n = len([b for b in self._branches.keys() if b.startswith(branch_prefix)])
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

        # Doesn't work for multiple undos in a row, but useful in testing
        # assert undid.content == ".undo"  # assert that the last message is an undo
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
        else:
            return None

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
        logger.info(f"Reduced log from {len_from//1} to {len_to//1} tokens")
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
        len_msgs = conv_fn.read_text().count("}\n{")
        assert len(log) <= 1
        modified = conv_fn.stat().st_mtime
        first_timestamp = log[0].timestamp.timestamp() if log else modified
        # Try to get display name from ChatConfig, fallback to folder name
        conv_id = conv_fn.parent.name
        chat_config = ChatConfig.from_logdir(conv_fn.parent)
        display_name = chat_config.name or conv_id

        agent_path = chat_config.agent
        agent_project_config = get_project_config(agent_path) if agent_path else None
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


def check_for_modifications(log: Log) -> bool:
    """Check if there are any file modifications in last 3 assistant messages since last user message."""
    messages_since_user = []
    found_user_message = False

    for m in reversed(log):
        if m.role == "user":
            found_user_message = True
            break
        if m.role == "system":
            continue
        messages_since_user.append(m)

    # If no user message found, skip the check (only system messages so far)
    if not found_user_message:
        return False

    # FIXME: this is hacky and unreliable

    has_modifications = any(
        tu.tool in ["save", "patch", "append", "morph"]
        for m in messages_since_user[:3]
        for tu in ToolUse.iter_from_content(m.content)
    )
    # logger.debug(
    #     f"Found {len(messages_since_user)} messages since user ({found_user_message=}, {has_modifications=})"
    # )
    return has_modifications


def _gen_read_jsonl(path: PathLike) -> Generator[Message, None, None]:
    with open(path) as file:
        for line in file.readlines():
            json_data = json.loads(line)
            files = [Path(f) for f in json_data.pop("files", [])]
            if "timestamp" in json_data:
                json_data["timestamp"] = isoparse(json_data["timestamp"])
            yield Message(**json_data, files=files)
