import logging
import os
import subprocess

from datasets import load_dataset

logger = logging.getLogger(__name__)


from datasets import DownloadMode


def load_instances(
    dataset_name: str = "princeton-nlp/SWE-bench_Lite", split: str = "test"
) -> dict[str, dict]:
    data = load_dataset(
        dataset_name, split=split, download_mode=DownloadMode.FORCE_REDOWNLOAD
    )
    return {d["instance_id"]: d for d in data}


def load_instance(
    instance_id: str,
    dataset_name: str = "princeton-nlp/SWE-bench_Lite",
    split: str = "test",
) -> dict:
    data = load_instances(dataset_name, split=split)
    return data[instance_id]


def setup_swebench_repo(instance_data: dict, repo_base_dir: str | None = None) -> str:
    if not repo_base_dir:
        repo_base_dir = os.getenv("REPO_DIR", "/tmp/repos")

    repo_dir_name = instance_data["repo"].replace("/", "__")
    github_repo_path = f"swe-bench/{repo_dir_name}"
    return setup_github_repo(
        repo=github_repo_path,
        base_commit=instance_data["base_commit"],
        base_dir=repo_base_dir,
    )


def get_file_spans_from_patch(patch: str) -> dict[str, list[str]]:
    file_spans: dict[str, list[str]] = {}
    current_file: str | None = None

    for line in patch.split("\n"):
        if line.startswith("diff --git"):
            current_file = line.split()[-1][2:]  # Extract the file path
            file_spans[current_file] = []

    return file_spans


def setup_github_repo(repo: str, base_commit: str, base_dir: str | None = None) -> str:
    if base_dir is None:
        base_dir = os.getenv("REPO_DIR", "/tmp/repos")

    repo_dir = os.path.join(base_dir, repo.replace("/", "_"))

    try:
        if not os.path.exists(repo_dir):
            logger.info(f"Cloning repository {repo} to {repo_dir}")
            os.makedirs(repo_dir, exist_ok=True)
            subprocess.run(
                ["git", "clone", f"https://github.com/{repo}.git", repo_dir],
                check=True,
                capture_output=True,
                text=True,
            )

        logger.info(f"Checking out commit {base_commit} in {repo_dir}")
        os.chdir(repo_dir)
        subprocess.run(
            ["git", "fetch", "origin"], check=True, capture_output=True, text=True
        )
        subprocess.run(
            ["git", "checkout", base_commit], check=True, capture_output=True, text=True
        )

        return repo_dir
    except subprocess.CalledProcessError as e:
        logger.error(f"Error setting up GitHub repo: {e}")
        logger.error(f"Command output: {e.output}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error setting up GitHub repo: {e}")
        raise
