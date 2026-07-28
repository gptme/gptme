# Choose a workflow for your task

Start with the outcome, not a provider or feature name. Most gptme tasks fit one
of the four rows below. Pick the narrowest execution surface that can finish the
job, then increase model capability or tool access only when the task requires it.

## Decision matrix

| Task | Start here | Model tier | Execution and approval posture |
|---|---|---|---|
| **Answer a low-stakes question** | Run `gptme "…"` with no tools when the answer does not need live or local evidence. | Small and fast. | No external actions; inspect the answer before relying on it. |
| **Research a topic** | Give the agent the browser tool and ask for linked sources plus a short synthesis. | Small for collection; stronger reasoning for conflicting evidence or an important decision. | Keep the work local and require source links. The browser can interact with pages, so keep confirmations on and approve only read operations. |
| **Create an artifact** | Name the output file and acceptance criteria: a report, image, script, or web page. | General-purpose; use a stronger model when structure or judgment matters. | Write only to a dedicated workspace. Review the artifact before sending or publishing it. |
| **Change inspectable code** | Run gptme inside a version-controlled checkout; ask it to inspect, edit, test, and show the diff. | Strong coding/reasoning model. | Keep confirmations on. Review commands and the diff; isolate untrusted repositories. |

Model names change faster than these capabilities. See {doc}`../model-routing`
for current model examples and {ref}`tool-allowlist` for exact tool-selection
syntax.

## Copy-paste starting points

### Low-stakes answer

```bash
gptme --tools "" \
  "Explain the difference between a process and a thread in five bullets."
```

Use this for explanations, brainstorming, and rewriting that need no current
facts or local files. If the answer affects money, safety, legal obligations, or
production behavior, it is no longer low-stakes: ask for evidence and verify it.

### Research

```bash
gptme --tools "browser,rag,chats,read" \
  "Compare three maintained Python task queues. Cite primary sources, state the tradeoffs, and save nothing. Use the browser only to read and search; do not interact with pages."
```

The browser tool also exposes page interactions such as clicks and form entry;
its allowlist entry does not restrict it to reading. Keep interactive
confirmations enabled, reject any interaction request, and use `hint:read-only`
only for tools actually annotated as read-only. For an important choice, split
collection from judgment: gather sources cheaply, then use a stronger model to
challenge the synthesis and identify missing evidence.

### Artifact creation

```bash
mkdir -p output
cd output
gptme \
  "Create report.md comparing the supplied options. Include a decision table and a sources section. Do not publish or send it."
```

An artifact-oriented task has a named deliverable and acceptance criteria. A
file you can inspect, test, or render is easier to verify than a vague request
to "work on" something. Use local execution when the output depends on private
files or local tools. Use a remote or hosted workspace only when its convenience
outweighs the cost of uploading the inputs, and never upload secrets by default.

### Inspectable code

```bash
cd /path/to/version-controlled-project
gptme \
  "Inspect the failing tests, make the smallest fix, rerun the targeted tests, and show me the diff. Do not commit or push."
```

A local checkout gives you the strongest review surface: version control,
tests, and a diff. Review an unfamiliar repository's `gptme.toml` before
starting because project configuration can run hooks and context commands. See
{doc}`../security` for the complete trust model.

## Local or remote?

Choose based on where the required data and verification tools already live:

- **Local execution** is the default for private files, existing repositories,
  device access, and work that must be verified with local tests.
- **Remote or hosted execution** is useful for a clean disposable environment,
  collaboration, or work that must continue after your machine disconnects.
- **Do not copy data merely to fit the surface.** Keep sensitive inputs where
  they already are, or create an isolated environment with only the files the
  task needs.
- **Treat remote execution as a different trust boundary.** Check credential,
  retention, network, and filesystem access before granting tools.

## Approval rule

Keep interactive confirmations on by default. Require an explicit human review
immediately before any action that:

- sends a message or submits a form;
- spends money or creates a paid resource;
- deletes or irreversibly overwrites data; or
- publishes, deploys, merges, or pushes work to other people.

Drafting is not publishing. Preparing a command is not running it. Ask gptme to
stop at the reviewable boundary (`"write the draft but do not send"`, `"show the
diff but do not commit"`). Avoid `--no-confirm` and `--non-interactive` for
these actions; non-interactive mode implies no confirmations.

## Escalate deliberately

Change one dimension at a time when the first attempt is insufficient:

1. Add the missing evidence source or local context.
2. Grant the narrow tool capability needed to inspect or verify it.
3. Move to a stronger model when the remaining problem is judgment or complex
   reasoning, not missing information.
4. Move from local to remote (or the reverse) only when the task's data,
   duration, or verification needs justify the trust-boundary change.

This keeps cheap questions cheap, research bounded to approved read operations,
artifacts inspectable, and consequential actions under human control.
