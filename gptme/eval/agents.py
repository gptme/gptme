import logging
import uuid
from abc import abstractmethod
from pathlib import Path

from gptme import Message, get_prompt
from gptme import chat as gptme_chat
from gptme.dirs import get_logs_dir
from gptme.executor import prepare_execution_environment
from gptme.util.auto_naming import generate_conversation_id

from ..tools import ToolFormat
from .execenv import DockerGPTMeEnv
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
    use_docker: bool = False

    def __init__(
        self,
        model: str,
        tool_format: ToolFormat = "markdown",
        tools: list[str] | None = None,
        system_prompt: str | None = None,
        use_docker: bool = False,
    ):
        self.model = model
        self.tool_format = tool_format
        self.tools = tools
        self.system_prompt = system_prompt
        self.use_docker = use_docker

        _id = uuid.uuid4().hex[:8]
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

        if self.use_docker:
            return self._act_docker(store, prompt)
        else:
            return self._act_local(store, prompt)

    def _act_docker(self, store: FileStore, prompt: str) -> Files:
        """Execute gptme inside a Docker container for isolation."""
        print("\n--- Start of generation (Docker-isolated) ---")
        logger.debug(f"Working in {store.working_dir} (Docker mode)")

        # Create Docker environment with workspace and logs mounted
        docker_env = DockerGPTMeEnv(
            host_dir=self.workspace_dir,
            log_dir=self.log_dir,
        )

        try:
            # Run gptme inside Docker
            stdout, stderr, exit_code = docker_env.run_gptme(
                prompt=prompt,
                model=self.model,
                tool_format=self.tool_format,
                tools=self.tools,
                system_prompt=self.system_prompt,
            )

            if exit_code != 0:
                logger.warning(f"Docker gptme exited with code {exit_code}")
                if stderr:
                    logger.warning(f"stderr: {stderr[:500]}")
        except Exception as e:
            logger.error(f"Docker execution failed: {e}")
            raise
        finally:
            # Clean up container
            docker_env.cleanup()

        print("--- Finished generation (Docker-isolated) ---\n")
        return store.download()

    def _act_local(self, store: FileStore, prompt: str) -> Files:
        """Execute gptme directly in the current process."""
        # Prepare execution environment and get initialized tools
        _, tools = prepare_execution_environment(
            workspace=self.workspace_dir,
            tools=self.tools,
        )

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
