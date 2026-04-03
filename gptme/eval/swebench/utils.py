import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# SWE-bench prediction format keys
KEY_INSTANCE_ID = "instance_id"
KEY_MODEL = "model_name_or_path"
KEY_PATCH = "model_patch"


def load_instances(
    dataset_name: str = "princeton-nlp/SWE-bench_Lite",
    split: str = "test",
    force_download: bool = False,
) -> dict[str, dict]:
    from datasets import DownloadMode, load_dataset  # lazy: optional eval extra

    download_mode = (
        DownloadMode.FORCE_REDOWNLOAD
        if force_download
        else DownloadMode.REUSE_DATASET_IF_EXISTS
    )
    data = load_dataset(dataset_name, split=split, download_mode=download_mode)
    return {d["instance_id"]: d for d in data}


def load_instance(
    instance_id: str,
    dataset_name: str = "princeton-nlp/SWE-bench_Lite",
    split: str = "test",
    force_download: bool = False,
) -> dict:
    data = load_instances(dataset_name, split=split, force_download=force_download)
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


def write_predictions_jsonl(
    predictions: list[dict],
    output_path: str | Path,
) -> Path:
    """Write predictions in SWE-bench JSONL format for official harness evaluation.

    Each prediction must have: instance_id, model_name_or_path, model_patch.
    See: https://github.com/princeton-nlp/SWE-bench#-evaluation
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for pred in predictions:
            f.write(json.dumps(pred) + "\n")
    logger.info(f"Wrote {len(predictions)} predictions to {output_path}")
    return output_path


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
                timeout=300,
            )

        logger.info(f"Checking out commit {base_commit} in {repo_dir}")
        subprocess.run(
            ["git", "fetch", "origin"],
            check=True,
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=120,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            check=True,
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=60,
        )

        return repo_dir
    except subprocess.CalledProcessError as e:
        logger.error(f"Error setting up GitHub repo: {e}")
        logger.error(f"Command output: {e.output}")
        raise
    except subprocess.TimeoutExpired as e:
        logger.error(
            f"Timed out setting up GitHub repo (command took >{e.timeout}s): {e.cmd}"
        )
        raise
    except Exception as e:
        logger.error(f"Unexpected error setting up GitHub repo: {e}")
        raise
