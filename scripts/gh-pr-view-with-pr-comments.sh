#!/bin/bash

# This script fetches and formats GitHub PR information, including PR description,
# comments, reviews, and review comments, showing them in chronological order.
#
# This script does what I wish `gh pr view --comments` would do.
#
# Features:
# - Shows PR description and metadata
# - Shows all comments in chronological order:
#   - Regular PR comments
#   - Reviews (with special handling of ellipsis-dev[bot] reviews to reduce noise)
#   - Review comments with their code context
# - Handles code suggestions (```suggestion blocks)
# - Shows referenced code with context
# - Skips resolved comments
#
# The output is formatted in sections:
# <pr_info>     - PR description and metadata
# <comments>    - All comments in chronological order
#
# Example usage:
#   ./scripts/gh-pr-view-with-pr-comments.sh https://github.com/owner/repo/pull/123

set -e

# Input can be either:
#   https://github.com/gptme/gptme/pull/466
#   gptme/gptme/pull/466
URL=$1

# Add https://github.com prefix if not present
[[ $URL != https://* ]] && URL="https://github.com/$URL"

# Extract components from URL
OWNER=$(echo $URL | awk -F'/' '{print $(NF-3)}')
REPO=$(echo $URL | awk -F'/' '{print $(NF-2)}')
ISSUE_ID=$(echo $URL | awk -F'/' '{print $NF}')

# Validate URL components
if [[ -z "$OWNER" || -z "$REPO" || -z "$ISSUE_ID" ]]; then
    echo "Error: Invalid URL format. Expected: owner/repo/pull/number"
    exit 1
fi
API_PATH="/repos/$OWNER/$REPO/pulls/$ISSUE_ID/comments"

echo "<pr_info>"
gh pr view $URL | cat
echo "</pr_info>"

echo

# TODO: we may also want to read all the paths and include them in context when used by gptme
#       https://github.com/gptme/gptme/issues/468
# TODO: handle resolved conversations (currently we only get individual comments)
#       https://github.com/gptme/gptme/pull/30 shows an example of resolved conversations
# TODO: use /pulls/reviews endpoint to get full conversation threads with resolution status
#       https://docs.github.com/en/rest/pulls/reviews
# TODO: improve handling of ellipsis-dev[bot] reviews, maybe parse the markdown to extract useful parts
#       https://github.com/gptme/gptme-webui/pull/30#pullrequestreview-2707423802

echo "<comments>"

# Create a temporary file for all comments
TMPFILE=$(mktemp)

# Get PR comments (non-review)
gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "/repos/$OWNER/$REPO/issues/$ISSUE_ID/comments" | jq -r '.[] | {
        type: "pr_comment",
        user: .user.login,
        body: .body,
        created_at: .created_at
    }' >> "$TMPFILE"

# Get reviews
gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "/repos/$OWNER/$REPO/pulls/$ISSUE_ID/reviews" | jq -r '.[] | select(.body != "") | {
        type: "review",
        user: .user.login,
        body: .body,
        created_at: .submitted_at,
        state: .state,
        id: .id
    }' >> "$TMPFILE"

# Get all review comments with thread information
gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$API_PATH" | jq -r '.[] | select(.state != "RESOLVED") | {
        type: "review_comment",
        user: .user.login,
        body: .body,
        created_at: .created_at,
        path: .path,
        line: .line,
        diff_hunk: .diff_hunk,
        in_reply_to_id: .in_reply_to_id,
        id: .id,
        thread_id: (if .in_reply_to_id then .in_reply_to_id else .id end)
    }' >> "$TMPFILE"

# Sort and format all comments
jq -rs '
  # Split and process comments
  {
    # Regular comments and reviews
    other: map(select(.type != "review_comment")) | sort_by(.created_at),
    # Review comments grouped by thread
    review_comments: map(select(.type == "review_comment")) |
      group_by(.thread_id) |
      map(sort_by(.created_at))
  } |
  # Process review comments to add thread markers
  .review_comments |= map(
    # For each thread
    if length > 1 then
      # Mark first comment as thread start and others as replies
      [
        (.[0] + {is_thread_start: true})
      ] +
      (.[1:] | map(. + {is_thread_reply: true}))
    else
      # Single comment threads stay as-is
      .
    end
  ) |
  # Combine everything back together
  (.other + (.review_comments | flatten)) |
  .[] |
    # Helper function for suggestion detection
    def has_suggestion:
        . | contains("```suggestion");

    # Format based on comment type and thread position
    if .type == "pr_comment" then
        "@\(.user):\n\(.body)"
    elif .type == "review" then
        if .user == "ellipsis-dev[bot]" then
            "## Review by @\(.user) (\(.state))\n" + (.body | split("\n")[0])
        else
            "## Review by @\(.user) (\(.state))\n\(.body)"
        end
    else
        # Start thread marker if this is the first comment in a thread
        (if .is_thread_start then "▼ Thread about \(.path):\n" else "" end) +
        "### Comment by @\(.user):\n" +
        .body + "\n" +
        "Referenced code in \(.path):\(.line):" +
        if (.body | has_suggestion) then
            "\nSuggested change:\n" +
            (.body | match("```suggestion\\n([^`]*)```"; "m").captures[0].string)
        else "" end +
        # Only show code context for thread start
        if .is_thread_start then
            "\nContext:\n```\(.path)\n\(.diff_hunk)\n```"
        else "" end
    end + "\n"' "$TMPFILE"

# Cleanup
rm "$TMPFILE"

echo "</comments>"
