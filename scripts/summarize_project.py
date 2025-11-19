from fnmatch import fnmatch
from pathlib import Path

import git  # type: ignore


def main():
    repo_path = "."  # specify the path to your git repository here
    ignore_globs = ["poetry.lock"]

    repo = git.Repo(repo_path)
    changed_files = [item.a_path for item in repo.index.diff(None)]

    project_name = Path(repo_path).resolve().name

    print(f"# Project summary - {project_name}\n")
    print("Here follows relevant (changed) files in the project\n")

    for file in changed_files:
        # if file is in ignore list, skip it
        if any(fnmatch(file, glob) for glob in ignore_globs):
            continue

        file_path = Path(repo_path) / file
        if file_path.exists():
            print(f"File: `{file}`")
            print(
                "```" + file.split(".")[-1]
            )  # use file extension for markdown code block

            with open(file_path, encoding="utf-8") as f:
                print(f.read())

            print("```")
            print()


if __name__ == "__main__":
    main()
