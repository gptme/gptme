# edited from the original swebench/harness/test_spec.py
from functools import cache
import os
import re

from dataclasses import dataclass
import subprocess
from typing import Union
import uuid

import requests

from gptme.logmanager import SWEBenchInfo

from .swe_bench_constants import (
    MAP_VER_TO_INSTALL,
    MAP_REPO_TO_TEST_FRAMEWORK,
    MAP_REPO_TO_REQS_PATHS,
)

from swebench.harness.constants import (
    MAP_REPO_TO_INSTALL,
    SWEbenchInstance,
    SWE_BENCH_URL_RAW,
)

from swebench.harness.utils import (
    get_test_directives,
)

DIFF_MODIFIED_FILE_REGEX = r"--- a/(.*)"


@cache
def get_requirements_by_commit(repo: str, commit: str) -> str:
    for req_path in MAP_REPO_TO_REQS_PATHS[repo]:
        reqs_url = os.path.join(SWE_BENCH_URL_RAW, repo, commit, req_path)
        reqs = requests.get(reqs_url)
        if reqs.status_code == 200:
            break
    else:
        raise ValueError(
            f"Could not find requirements file at paths {MAP_REPO_TO_REQS_PATHS[repo]} for repo {repo} at commit {commit}"
        )

    lines = reqs.text
    original_req = []
    additional_reqs = []
    req_dir = "/".join(req_path.split("/")[:-1])
    exclude_line = lambda line: any(
        [line.strip().startswith(x) for x in ["-e .", "#", ".[test"]]
    )

    for line in lines.split("\n"):
        if line.strip().startswith("-r"):
            # Handle recursive requirements
            file_name = line[len("-r") :].strip()
            reqs_url = os.path.join(
                SWE_BENCH_URL_RAW,
                repo,
                commit,
                req_dir,
                file_name,
            )
            reqs = requests.get(reqs_url)
            if reqs.status_code == 200:
                for line_extra in reqs.text.split("\n"):
                    if not exclude_line(line_extra):
                        additional_reqs.append(line_extra)
        else:
            if not exclude_line(line):
                original_req.append(line)

    # Combine all requirements into single text body
    additional_reqs.append("\n".join(original_req))
    all_reqs = "\n".join(additional_reqs)

    return all_reqs


def get_requirements(instance: SWEbenchInstance) -> str:
    """
    Get requirements.txt for given task instance

    Args:
        instance (dict): task instance
    Returns:
        requirements.txt (str): Returns requirements.txt as string
    """
    # Attempt to find requirements.txt at each path based on task instance's repo
    commit = (
        instance["environment_setup_commit"]
        if "environment_setup_commit" in instance
        and instance["environment_setup_commit"] != "2401580b6f41fe72f1360493ee46e8a842bd04ba"
        else instance["base_commit"]
    )

    return get_requirements_by_commit(instance["repo"], commit)



@dataclass
class TestSpec:
    """
    A dataclass that represents a test specification for a single instance of SWE-bench.
    """
    instance_id: str
    repo: str
    version: str
    repo_script_list: str
    reset_repo_script_list: str
    eval_script_list: str
    FAIL_TO_PASS: list[str]
    PASS_TO_PASS: list[str]
    repo_dir: str

    @property
    def eval_script(self):
        return "\n".join(["#!/bin/bash", "set -uxo pipefail"] + self.eval_script_list) + "\n"

    @property
    def install_repo_script(self):
        return "\n".join(["#!/bin/bash", "set -euxo pipefail"] + self.repo_script_list) + "\n"
    
    def reset_repo(self):
        script = "\n".join(self.reset_repo_script_list)
        print(f"Running script: {script}")
        res = subprocess.run(script, shell=True)
        return res.returncode == 0

    def eval_repo(self):
        script = "\n".join(self.eval_script_list)
        print(f"Running script: {script}")
        res = subprocess.run(script, shell=True)
        return res.returncode == 0
    
    def setup_repo(self):
        script = "\n".join(self.repo_script_list)
        print(f"Running script: {script}")
        subprocess.run(script, shell=True)
        return self.repo_dir


def get_test_specs_from_dataset(dataset: Union[list[SWEbenchInstance], list[TestSpec]]) -> list[TestSpec]:
    """
    Idempotent function that converts a list of SWEbenchInstance objects to a list of TestSpec objects.
    """
    if isinstance(dataset[0], TestSpec):
        return dataset
    return list(map(make_test_spec, dataset))


def make_repo_script_list(instance, install, repo, repo_directory, base_commit, venv_name):
    """
    Create a list of bash commands to set up the repository for testing.
    """
    setup_commands = [
        f"git clone -o origin https://github.com/{repo} {repo_directory}",
        f"chmod -R 777 {repo_directory}",
        f"cd {repo_directory}",
        f"git reset --hard {base_commit}",
        f"git remote remove origin",
    ] + make_env_script_list(instance, install, venv_name)
    
    if repo in MAP_REPO_TO_INSTALL:
        setup_commands.append(MAP_REPO_TO_INSTALL[repo])

    if "pre_install" in install:
        for pre_install in install["pre_install"]:
            setup_commands.append(pre_install)

    if "install" in install:
        setup_commands.append(install["install"])
    return setup_commands


def make_env_script_list(instance, install, venv_name):
    """
    Creates the list of commands to set up the Python virtual environment for testing.
    """
    HEREDOC_DELIMITER = "EOF_59812759871"
    python_version = install['python']
    
    setup_commands = [
        f"pyenv install -s {python_version}",
        f"pyenv local {python_version}",
        f"python -m venv {venv_name}",
        f"source {venv_name}/bin/activate",
        "python -m pip install --upgrade pip",
    ]

    # Handle dependencies based on package specification
    if install.get("packages") == "requirements.txt":
        reqs = get_requirements(instance)
        path_to_reqs = "requirements.txt"
        setup_commands.append(
            f"cat <<'{HEREDOC_DELIMITER}' > {path_to_reqs}\n{reqs}\n{HEREDOC_DELIMITER}"
        )
        setup_commands.append(f"pip install -r {path_to_reqs}")
        setup_commands.append(f"rm {path_to_reqs}")
    
    elif install.get("packages") == "environment.yml":
        # Convert environment.yml dependencies to requirements.txt format
        # This is a simplified approach - you might need to handle this differently
        reqs = get_requirements(instance)  # You'll need to modify this to handle yml conversion
        path_to_reqs = "requirements.txt"
        setup_commands.append(
            f"cat <<'{HEREDOC_DELIMITER}' > {path_to_reqs}\n{reqs}\n{HEREDOC_DELIMITER}"
        )
        setup_commands.append(f"pip install -r {path_to_reqs}")
        setup_commands.append(f"rm {path_to_reqs}")
    
    elif install.get("pip_packages"):
        pip_packages = " ".join(install["pip_packages"])
        setup_commands.append(f"pip install {pip_packages}")

    return setup_commands


def make_eval_script_list(instance, install, venv_name, repo_directory, base_commit, test_patch):
    """
    Applies the test patch and runs the tests.
    """
    HEREDOC_DELIMITER = "EOF_114329324912"
    test_files = re.findall(DIFF_MODIFIED_FILE_REGEX, test_patch)
    reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
    apply_test_patch_command = (
        f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{test_patch}\n{HEREDOC_DELIMITER}"
    )
    test_framework = MAP_REPO_TO_TEST_FRAMEWORK[instance["repo"]]
    if isinstance(test_framework, dict):
        test_framework = test_framework[instance["version"]]
    test_command = " ".join(
        [
            test_framework,
            *get_test_directives(instance),
        ]
    )
    
    python_version = install['python']
    eval_commands = []
    
    if "eval_commands" in install:
        eval_commands += install["eval_commands"]
        
    eval_commands += [
        f"git config --global --add safe.directory {repo_directory}",
        f"cd {repo_directory}",
        f"git status",
        f"git show",
        f"git diff {base_commit}",
        f"pyenv install -s {python_version}",
        f"pyenv local {python_version}",
        f"source {venv_name}/bin/activate",
    ]
    
    if "install" in install:
        eval_commands.append(install["install"])

    set_env_commands = [
        "export PAGER=cat",
        "export GH_PAGER=cat",
        "export GIT_PAGER=cat",
    ]
    
    eval_commands += [
        *set_env_commands,
        reset_tests_command,
        apply_test_patch_command,
        test_command,
        reset_tests_command,
    ]
    return eval_commands


def make_test_spec(instance: SWEbenchInstance, repo_directory: str | None = None) -> TestSpec:
    if isinstance(instance, TestSpec):
        return instance
        
    instance_id = instance["instance_id"]
    repo = instance["repo"]
    version = str(float(instance["version"]))
    base_commit = instance["base_commit"]
    test_patch = instance["test_patch"]
    pass_to_pass = instance["PASS_TO_PASS"]
    fail_to_pass = instance["FAIL_TO_PASS"]

    venv_name = "env"
    if not repo_directory:
        run_id = uuid.uuid4().hex[:8]
        repo_directory = f"/tmp/repos/{instance_id}-{run_id}"
    install = MAP_VER_TO_INSTALL[repo][version]

    repo_script_list = make_repo_script_list(instance, install, repo, repo_directory, base_commit, venv_name)
    eval_script_list = make_eval_script_list(
        instance, install, venv_name, repo_directory, base_commit, test_patch
    )
    reset_repo_script_list = [f"cd {repo_directory}", f"git reset --hard {base_commit}"]
    return TestSpec(
        instance_id=instance_id,
        repo=repo,
        repo_dir=repo_directory,
        repo_script_list=repo_script_list,
        eval_script_list=eval_script_list,
        reset_repo_script_list=reset_repo_script_list,
        version=version,
        FAIL_TO_PASS=fail_to_pass,
        PASS_TO_PASS=pass_to_pass,
    )

def instance_to_trajectory_info(instance: SWEbenchInstance, model_name: str, target = False, repo_dir: str | None = None, log_dir: str | None = None) -> SWEBenchInfo:
    if log_dir:
        info = SWEBenchInfo.load_from_log_dir(log_dir)
        if info is not None:
            return info
    return SWEBenchInfo(
        instance_id=instance["instance_id"],
        model_name=model_name,
        target=target,
        exit_status=None,
        generated_patch=None,
        eval_logs=None,
        repo_dir=repo_dir,
    )