import logging
import random
from abc import abstractmethod
from pathlib import Path

from gptme import Message, get_prompt
from gptme import chat as gptme_chat
from gptme.dirs import get_logs_dir
from gptme.tools import init_tools
from gptme.util.auto_naming import generate_conversation_id

from ..tools import ToolFormat
from .filestore import FileStore
from .types import Files

logger = logging.getLogger(__name__)


class Agent:
    model: str
    tool_format: ToolFormat
    tools: list[str] | None = None
    system_prompt: str | None = None
    log_dir: Path
    workspace_dir: Path

    def __init__(
        self,
        model: str,
        tool_format: ToolFormat = "markdown",
        tools: list[str] | None = None,
        system_prompt: str | None = None,
    ):
        self.model = model
        self.tool_format = tool_format
        self.tools = tools
        self.system_prompt = system_prompt

        _id = random.randint(10000, 99999)
        model_fmt = f"{self.model.replace('/', '--')}-{self.tool_format}"
        name = generate_conversation_id(
            f"gptme-evals-{model_fmt}-{_id}", get_logs_dir()
        )
        log_dir = get_logs_dir() / name
        workspace_dir = log_dir / "workspace"
        if workspace_dir.exists():
            raise FileExistsError(
                f"Workspace directory {workspace_dir} already exists."
            )
        workspace_dir.mkdir(parents=True)

        self.log_dir = log_dir
        self.workspace_dir = workspace_dir

    @abstractmethod
    def act(self, files: Files | None, prompt: str) -> Files:
        """
        Carries out the prompt and returns artifacts in the form of `Files`.
        """
        raise NotImplementedError


class GPTMe(Agent):
    def act(self, files: Files | None, prompt: str):
        store = FileStore(working_dir=self.workspace_dir)
        if files:
            store.upload(files)

        # Use configured tools or default to all tools
        tools = init_tools(allowlist=self.tools)

        print("\n--- Start of generation ---")
        logger.debug(f"Working in {store.working_dir}")

        prompt_sys_msgs = get_prompt(
            tool_format=self.tool_format,
            tools=tools,
            workspace=self.workspace_dir,
            prompt=self.system_prompt or "full",  # this only replaces the base prompt
        )

        # Modify the first (core) system prompt to add eval-specific instruction
        if prompt_sys_msgs:
            prompt_sys_msgs[0] = prompt_sys_msgs[0].replace(
                content=prompt_sys_msgs[0].content
                + "\n\nIf you have trouble and dont seem to make progress, stop trying."
            )
        try:
            gptme_chat(
                [Message("user", prompt)],
                prompt_sys_msgs,
                logdir=self.log_dir,
                model=self.model,
                no_confirm=True,
                interactive=False,
                workspace=self.workspace_dir,
                tool_format=self.tool_format,
                tool_allowlist=self.tools,
            )
        # don't exit on sys.exit()
        except (SystemExit, KeyboardInterrupt):
            pass
        print("--- Finished generation ---\n")

        return store.download()
