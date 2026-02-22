"""
An old script used to manage TODOs with LLMs, intended to be rewritten into a tool for gptme.
"""

import os
import subprocess
from pathlib import Path

from langchain import SerpAPIWrapper
from langchain.agents import Tool, initialize_agent, load_tools
from langchain.chains.llm import LLMChain
from langchain.chat_models import ChatOpenAI
from langchain.prompts import PromptTemplate

_todos: list[str] = []


pre_prompt = """
You are an AI Assistant designed to help people get things done. In particular, helping programmers.

You can list, add, remove, and prioritise TODOs.

You have:
 - A Personal Knowledge Base containing TODOs (the SearchTODO tool).
 - A file TODO.md where you can store output.
 - A terminal with a bash shell on local machine. (don't run nano, vim, or anything that will block the terminal)

You will follow the ReAct framework:

> Observation -> Action & Action Input -> Result -> Observation

Example:

Lets find some TODOs using the SearchTODO tool. You can pass it a search term to filter the results, but for now we'll check all.
Action: SearchTODO
Action Input: ""
Result: - [ ]
Observation: I need to categorize

""".strip()

path_todos = Path("~/Programming/roam-backup/markdown").expanduser()


def _load_todos():
    global _todos
    if len(_todos) == 0:
        if os.path.exists("TODO"):
            with open("TODO") as f:
                _todos = [line.strip() for line in f]
        else:
            return ["[ ] Find TODOs, prioritise them, and add the top ones"]
    return _todos


def _format_todos():
    return "\n".join([f"- {todo}" for i, todo in enumerate(_load_todos())])


def _prompt() -> str:
    return f"{pre_prompt}\n\nYour TODOs are:\n{_format_todos()}"


def _load_roam_todos(filter: str = "") -> str:
    """Use rg to find TODOs in the roam-backup"""
    todos = []
    rg = subprocess.run(
        [
            "rg",
            "--no-filename",
            "--no-line-number",
            r"\{\{\[\[TODO\]\]\}\}",
            path_todos,
        ],
        capture_output=True,
        check=False,
    )
    if rg.returncode != 0:
        stderr_stripped = "\n".join(rg.stderr.decode("utf-8").split("\n")[:5])
        raise Exception(f"Error running rg: {stderr_stripped}...")
    for line in rg.stdout.decode("utf-8").split("\n"):
        if line.strip():
            todos.append(line.strip())
    if filter and filter not in ["TODO", "None", "N/A"]:
        todos = [todo for todo in todos if filter.lower() in todo.lower()]
    if len(todos) > 20:
        todos = todos[:20]
    return "\n".join([todo.replace(r"{{[[TODO]]}}", "[ ]") for todo in todos])


def test_load_roam_todos():
    assert _load_roam_todos("")


llm = ChatOpenAI(temperature=0, model="gpt-3.5-turbo")

# tools
tool_terminal = load_tools(["terminal"], llm=llm)[0]

# terminal but strips the backticks
tool_terminal2 = Tool(
    name="Terminal",
    func=lambda input: tool_terminal.run(tool_input=input.strip("`")),
    description="Run a command in the terminal (bash).",
)

search = SerpAPIWrapper()
tool_search = Tool(
    name="Search",
    func=search.run,
    description="useful for when you need to answer questions about current events. You should ask targeted questions",
)


def tool_prioritize(tasks: str):
    template = PromptTemplate(
        template="""Rank these TODOs by their priority.
Annotate with tags like: #value-high, #size-small, etc.

Tasks:
{tasks}

Top 5 tasks:""",
        input_variables=["tasks"],
    )
    chain = LLMChain(llm=llm, prompt=template)
    return chain.run(tasks=tasks, verbose=True)


tool_prioritize = Tool(
    name="Prioritize", func=tool_prioritize, description="Prioritize a list of tasks"
)


def do_todo(prompt, *args, **kwargs):
    agent = initialize_agent(
        [tool_terminal2], llm, agent="zero-shot-react-description", verbose=True
    )
    return agent.run(input=prompt, verbose=True)


tool_dotodo = Tool(
    name="DoTODO",
    func=do_todo,
    description="Execute a TODO. Pass it a single TODO as input.",
)

tool_addtodo = Tool(
    name="AddTODO", func=do_todo, description="Add a TODO to in-progress TODOs"
)

tool_searchtodo = Tool(
    name="SearchTODO",
    func=lambda params: _load_roam_todos(),
    description="Retrieve a list of TODOs from a PKM system. Pass it a search term to filter the results, or the empty string.",
)


def answer(prompt: str = ""):
    prompt = _prompt() + (("\n\n" + prompt) if prompt else "")
    print("Running chain with prompt: ", prompt)
    tools = [tool_search, tool_searchtodo, tool_terminal2, tool_prioritize]

    agent = initialize_agent(
        tools,
        llm,
        agent="zero-shot-react-description",
        verbose=True,
        return_intermediate_steps=True,
    )
    resp = agent(prompt)
    return resp


def main():
    answer()


if __name__ == "__main__":
    main()
