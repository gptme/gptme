"""
Multi-stage SWE-bench agent from bjsi's original work (PR #424).

The understand/reproduce/fix sub-agents are not yet implemented — this is the
orchestration skeleton that will drive them once they exist.
"""

import logging
import os
import time
import uuid
from pathlib import Path

from gptme.dirs import get_logs_dir
from gptme.logmanager import LogManager, SWEBenchInfo
from gptme.message import print_msg
from gptme.tools import execute_msg, init_tools
from gptme.util.auto_naming import generate_conversation_id

from ..swe_extra.swe_bench_test_spec import instance_to_trajectory_info, make_test_spec

try:
    from swebench.harness.constants import SWEbenchInstance
except ImportError:
    SWEbenchInstance = dict  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


class SWEBenchAgent:
    """Multi-stage SWE-bench agent that orchestrates understand/reproduce/fix phases."""

    stages = ["understand", "reproduce", "fix"]

    def act(
        self,
        model: str,
        instance: "SWEbenchInstance",
        repo_dir: str,
        log_dir: str,
        resume: bool = False,
        start_stage: str = "understand",
        **kwargs,
    ):
        # Initialize or load trajectory info
        trajectory_info = instance_to_trajectory_info(
            instance,
            model,
            repo_dir=repo_dir,
            log_dir=log_dir if resume else None,
        )

        if not resume:
            trajectory_info.save_to_log_dir(log_dir)

        # NOTE: The understand/reproduce/fix sub-agents require additional
        # implementation. This is the orchestration skeleton from bjsi's
        # original SWE-bench work (PR #424).

        # Understand
        if self.stages.index(start_stage) <= self.stages.index("understand"):
            logger.info("Stage: understand")
            # TODO: Understand().act(model=model, instance=instance, repo_dir=repo_dir,
            #                  log_dir=log_dir, info=trajectory_info,
            #                  **kwargs.get("understand", {}))

        # Reproduce
        if self.stages.index(start_stage) <= self.stages.index("reproduce"):
            logger.info("Stage: reproduce")
            # TODO: Reproduce().act(model=model, instance=instance, repo_dir=repo_dir,
            #                 log_dir=log_dir, info=trajectory_info,
            #                 **kwargs.get("reproduce", {}))

        # Fix
        if self.stages.index(start_stage) <= self.stages.index("fix"):
            logger.info("Stage: fix")
            # TODO: Fix().act(model=model, instance=instance, repo_dir=repo_dir,
            #           log_dir=log_dir, info=trajectory_info,
            #           **kwargs.get("fix", {}))

        return trajectory_info.artifacts

    def get_resume_stage(self, log_dir: str) -> str:
        understand_manager = LogManager.load(
            log_dir, lock=False, create=True, branch="understand"
        )
        reproduce_manager = LogManager.load(
            log_dir, lock=False, create=True, branch="reproduce"
        )
        fix_manager = LogManager.load(log_dir, lock=False, create=True, branch="fix")
        if not understand_manager.log.messages:
            return "understand"
        if not reproduce_manager.log.messages:
            return "reproduce"
        if not fix_manager.log.messages:
            return "fix"
        return "understand"

    def replay(self, log_dir: str):
        logger.info(f"Replaying from log directory: {log_dir}")
        info = SWEBenchInfo.load_from_log_dir(log_dir)
        if not info or not info.repo_dir:
            raise ValueError(f"No valid info found in {log_dir}")
        original_dir = os.getcwd()
        os.chdir(info.repo_dir)
        try:
            init_tools()
            understand_manager = LogManager.load(
                log_dir, lock=False, create=True, branch="understand"
            )
            reproduce_manager = LogManager.load(
                log_dir, lock=False, create=True, branch="reproduce"
            )
            fix_manager = LogManager.load(
                log_dir, lock=False, create=True, branch="fix"
            )
            for msg in understand_manager.log.messages:
                if msg.role == "assistant":
                    for reply_msg in execute_msg(msg):
                        print_msg(reply_msg, oneline=False)
            for msg in reproduce_manager.log.messages:
                if msg.role == "assistant":
                    for reply_msg in execute_msg(msg):
                        print_msg(reply_msg, oneline=False)
            for msg in fix_manager.log.messages:
                if msg.role == "assistant":
                    for reply_msg in execute_msg(msg):
                        print_msg(reply_msg, oneline=False)
        finally:
            os.chdir(original_dir)

    def evaluate_instance(
        self,
        instance: "SWEbenchInstance",
        model: str = "openrouter/qwen/qwen-2.5-coder-32b-instruct",
        resume_dir: Path | None = None,
        **kwargs,
    ):
        instance_id = instance["instance_id"]
        problem_statement = instance["problem_statement"]
        info = SWEBenchInfo.load_from_log_dir(resume_dir) if resume_dir else None
        if resume_dir and not info:
            raise ValueError(f"No info found in {resume_dir}")

        test_spec = make_test_spec(instance, info.repo_dir if info else None)

        logger.info(f"Evaluating instance: {instance_id}")
        logger.debug(f"Problem statement: {problem_statement}")

        if resume_dir and info:
            log_dir = resume_dir
            logger.info(f"Resuming from log directory: {log_dir}")
            test_spec.reset_repo()
            self.replay(str(log_dir))
            repo_dir = info.repo_dir or ""
        else:
            _id = uuid.uuid4().hex[:8]
            model_fmt = f"{model.replace('/', '--')}-{_id}"
            name = generate_conversation_id(f"gptme-evals-{model_fmt}", get_logs_dir())
            log_dir = get_logs_dir() / name
            repo_dir = test_spec.setup_repo()

        start_time = time.time()
        try:
            logger.info(f"Executing agent for instance {instance_id}")
            logger.info(
                f"Finished setting up repo for instance {instance_id} {repo_dir}"
            )

            SWEBenchAgent().act(
                model=model,
                instance=instance,
                repo_dir=repo_dir,
                log_dir=str(log_dir),
                resume=bool(resume_dir),
                start_stage=(
                    self.get_resume_stage(str(log_dir)) if resume_dir else "understand"
                ),
                **kwargs,
            )

            gen_time = time.time() - start_time
            logger.info(
                f"Agent execution completed for instance {instance_id}"
                f" in {gen_time:.2f} seconds"
            )
            passed = test_spec.eval_repo()
            logger.info(
                f"Evaluation completed for instance {instance_id}. Passed: {passed}"
            )
        except Exception as e:
            import traceback

            logger.error(
                f"Error during agent execution for instance {instance_id}: {e}\n"
                f"{''.join(traceback.format_tb(e.__traceback__))}"
            )
