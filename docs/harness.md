# Harness Updates

`gptme` can now capture assistant-authored harness update requests in session logs.

Phase 1 is audit-only:

- The assistant may emit `HARNESS_UPDATE:` lines in normal output.
- `gptme` validates and records the request in assistant-message metadata.
- Tool availability does not change yet.

## Syntax

```txt
HARNESS_UPDATE: enable_tool web_fetch reason="Need fresh docs" urgency=medium approval=auto
HARNESS_UPDATE: disable_tool shell reason="Task is pure analysis" urgency=low approval=log_only
```

Required fields:

- `change_type`: `enable_tool`, `disable_tool`, or `configure_tool`
- `tool_name`: must match a real available tool
- `reason`
- `urgency`: `low`, `medium`, or `high`
- `approval`: `auto`, `log_only`, or `user_confirm`

Validated requests are stored under assistant-message metadata as `harness_updates`.
Rejected requests are stored as `harness_update_errors`.

## Example Metadata

```txt
metadata = {
  "harness_updates" = [
    {
      "change_type" = "enable_tool",
      "tool_name" = "web_fetch",
      "reason" = "Need fresh docs",
      "urgency" = "medium",
      "approval_mode" = "auto",
      "raw_line" = "HARNESS_UPDATE: enable_tool web_fetch reason=\"Need fresh docs\" urgency=medium approval=auto"
    }
  ]
}
```
