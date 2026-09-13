---
audience: developer
---

# How to Write a Custom Tool Plugin

gptme's built-in tools cover general-purpose tasks, but domain-specific
operations — querying a proprietary API, reading a custom binary format, calling
an internal service — need a custom tool. Without one, you're pasting raw output
into the prompt by hand, which is slow and error-prone.

A plugin is a Python package that exposes a `GptmePlugin` instance via the
`gptme.plugins` entry-point group. Each tool in the plugin is a `ToolSpec` with a
name, a description (shown to the LLM), and a `functions` list of Python
callables. gptme describes those functions in the tool prompt and registers them
in its Python (`ipython`) tool so the agent can call them.

## Write the tool

A plugin that lets gptme query a local SQLite database:

```python
# my_gptme_plugin/__init__.py
import sqlite3
from gptme.tools.base import ToolSpec
from gptme.plugins.plugin import GptmePlugin


def query_db(db_path: str, sql: str) -> str:
    """Execute a read-only SQL query against a local SQLite database.

    Args:
        db_path: Path to the SQLite database file.
        sql: SELECT statement to run.

    Returns:
        Query results as a Markdown table, or "(no results)" when no rows match.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description] if cur.description else []
    if not cols:
        return "(no results)"
    header = " | ".join(cols)
    sep = " | ".join("---" for _ in cols)
    body = "\n".join(" | ".join(str(c) for c in row) for row in rows)
    return f"{header}\n{sep}\n{body}"


plugin = GptmePlugin(
    name="sqlite",
    tools=[
        ToolSpec(
            name="query_db",
            desc="Run a read-only SQL query against a local SQLite database and return results as a Markdown table.",
            functions=[query_db],
        )
    ],
)
```

## Register the entry point

```toml
# pyproject.toml (in the plugin package)
[project]
name = "my-gptme-plugin"
version = "0.1.0"

[project.entry-points."gptme.plugins"]
sqlite = "my_gptme_plugin:plugin"
```

## Install and use it

```bash
pip install -e ./my_gptme_plugin

# Verify it loaded
gptme-util tools list

# Use it in a session
gptme "Query the users table in /var/app/prod.db and find accounts created in the last 7 days"
```

gptme calls `query_db` with the right arguments and shows you the results inline,
without you copy-pasting any SQL output.

## Tips

- **Open databases read-only**: `mode=ro` in the SQLite URI (as shown) prevents
  accidental writes from a buggy SQL statement the LLM generates.
- **Write precise descriptions**: the LLM reads `desc` to decide when and how to
  call the tool — keep it concise but specific.
- **Keep signatures accurate**: gptme derives the parameter schema from type
  annotations and the docstring `Args:` section.
- See {doc}`../plugins` for the full `GptmePlugin` and `ToolSpec` reference, and
  {doc}`../custom_tool` for script-based tools and MCP alternatives.
