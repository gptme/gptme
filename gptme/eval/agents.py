import logging
from abc import abstractmethod

from gptme import Message
from gptme import chat as gptme_chat
from gptme import get_prompt
from gptme.cli import get_name
from gptme.dirs import get_logs_dir
from gptme.tools import init_tools

from ..tools import ToolFormat
from .filestore import FileStore
from .types import Files

logger = logging.getLogger(__name__)


class Agent:
    model: str
    tool_format: ToolFormat

    def __init__(self, model: str, tool_format: ToolFormat = "markdown"):
        self.model = model
        self.tool_format = tool_format

    @abstractmethod
    def act(self, files: Files | None, prompt: str) -> Files:
        """
        Carries out the prompt and returns artifacts in the form of `Files`.
        """
        raise NotImplementedError


class GPTMe(Agent):
    def act(self, files: Files | None, prompt: str):
        _id = abs(hash(prompt)) % 1000000
        model_fmt = f"{self.model.replace('/', '--')}-{self.tool_format}"
        name = get_name(f"gptme-evals-{model_fmt}-{_id}")
        log_dir = get_logs_dir() / name
        workspace_dir = log_dir / "workspace"
        if workspace_dir.exists():
            raise FileExistsError(
                f"Workspace directory {workspace_dir} already exists."
            )
        workspace_dir.mkdir(parents=True)

        store = FileStore(working_dir=workspace_dir)
        if files:
            store.upload(files)

        # TODO: make eval toolset configurable
        tools = init_tools()

        print("\n--- Start of generation ---")
        logger.debug(f"Working in {store.working_dir}")
        prompt_sys_msgs = get_prompt(tools=tools, workspace=workspace_dir)
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
                logdir=log_dir,
                model=self.model,
                no_confirm=True,
                interactive=False,
                workspace=workspace_dir,
                tool_format=self.tool_format,
            )
        # don't exit on sys.exit()
        except (SystemExit, KeyboardInterrupt):
            pass
        print("--- Finished generation ---\n")

        return store.download()
