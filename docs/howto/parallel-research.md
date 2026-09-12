---
audience: user
---

# How to Run Parallel Research with Subagents

Deep research is slow when done one question at a time. If you need to compare
five frameworks or investigate three bug hypotheses, doing them one by one wastes
wall-clock time and burns context on already-answered sub-questions.

The `subagent` tool lets gptme split the work: each subagent gets a clean context
and runs its task independently, and the coordinator synthesizes the results.
Use this pattern to compare N alternatives, run independent research branches, or
execute the same task against multiple inputs.

The `subagent` tool is disabled by default — enable it with `--tools +subagent`.

## Research alternatives in parallel

Write the coordinator prompt to a file and run it:

```bash
cat > research-prompt.md << 'EOF'
Research the following three Python HTTP client libraries in parallel:
1. httpx
2. aiohttp
3. requests

For each, find: latest version, async support, connection pooling behavior, and
known issues with timeouts. Then write a comparison table to comparison.md and
recommend one for a high-concurrency microservice.
EOF

gptme --tools +subagent "$(cat research-prompt.md)"
```

The coordinator spawns three subagents with separate contexts, each researching
one library. Results are written to shared files; the coordinator reads them and
writes the synthesis.

## Drive it from an interactive session

```bash
gptme --tools +subagent
# > Spawn three subagents: one researches httpx, one aiohttp, one requests.
# > Each should write its findings to a temp file named after the library.
# > When all are done, synthesize the findings into comparison.md.
```

## Tips

- **Use files as the channel**: subagents share your filesystem but not context,
  so have workers write findings to files the coordinator reads.
- **Keep prompts self-contained**: a subagent won't see the coordinator's
  conversation history.
- **Expect max, not sum**: for independent tasks, total wall-clock time is roughly
  `max(subtask times)` rather than `sum(subtask times)`.
- See the {doc}`subagent tool reference </tools/subagent>` for isolation, fan-out,
  structured output, and token budgets.
