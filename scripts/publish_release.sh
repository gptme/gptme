#!/bin/bash
# Shared release publishing — push tag and create GitHub release.
# Used by both `make release` and scheduled-release CI workflow.
#
# Package building, PyPI publishing, and artifact builds are handled
# by the release.yml workflow (triggered by the release:created event
# or explicitly via workflow_dispatch).
#
# Prerequisites: bump_version.sh has already been run (version committed + tagged).
#
# Usage:
#   ./scripts/publish_release.sh                      # Push + GH release (auto-generated notes)
#   ./scripts/publish_release.sh --notes-file FILE    # Use structured changelog file
#   ./scripts/publish_release.sh --dry-run            # Print what would happen
#
# The script reads version metadata from the current git state (tag on HEAD, pyproject.toml).

set -euo pipefail

NOTES_FILE=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --notes-file)    NOTES_FILE="$2"; shift 2 ;;
        --dry-run)       DRY_RUN=true; shift ;;
        *)               echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# --- Read version metadata from git/pyproject ---

VERSION=$(poetry version --short)
TAG="v${VERSION}"

# Verify the tag exists on HEAD
if ! git tag --points-at HEAD | grep -q "^${TAG}$"; then
    echo "Error: tag ${TAG} not found on HEAD. Run bump_version.sh first." >&2
    exit 1
fi

# Find previous tag of any kind (excluding the one we just created).
# Use creatordate sort instead of version:refname because collision-suffixed
# dev tags (e.g. v0.31.1.dev202603103) sort higher than later dates.
PREV_TAG=$(git tag --sort=-creatordate | grep -v "^${TAG}$" | head -1 || true)

# Find previous stable tag (excluding the one we just created)
PREV_STABLE=$(git tag --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | grep -v "^${TAG}$" | head -1 || true)

# Determine if pre-release
PRERELEASE=false
if echo "$VERSION" | grep -qE '\.(dev|a|b|rc)'; then
    PRERELEASE=true
fi

# For dev: changelog since previous tag; for stable: since previous stable
if [ "$PRERELEASE" = "true" ]; then
    NOTES_START="$PREV_TAG"
else
    NOTES_START="$PREV_STABLE"
fi

PRERELEASE_FLAG=""
if [ "$PRERELEASE" = "true" ]; then
    PRERELEASE_FLAG="--prerelease"
fi

echo "Publishing release: ${TAG} (pre-release=${PRERELEASE})"

# --- Step 1: Push ---

echo "Pushing ${TAG} to origin..."
if [ "$DRY_RUN" = "false" ]; then
    if [ "$PRERELEASE" = "false" ]; then
        # Stable releases: push version bump commit to master.
        # Requires the user/bot to have branch protection bypass rights.
        git push origin HEAD:master
    else
        # Pre-releases: skip the branch push entirely.
        # The tag push (below) will carry the version-bump commit to GitHub
        # as an unreachable-from-branch object — that's fine for dev builds.
        # This avoids hitting branch protection rules in CI.
        echo "  Skipping branch push for pre-release (branch protection safe)."
    fi
    git push origin "${TAG}"
else
    if [ "$PRERELEASE" = "false" ]; then
        echo "  [dry-run] git push origin HEAD:master"
    else
        echo "  [dry-run] Skipping branch push for pre-release"
    fi
    echo "  [dry-run] git push origin ${TAG}"
fi

# --- Step 2: Create GitHub release (idempotent) ---

echo "Creating GitHub release: ${TAG}..."
if [ "$DRY_RUN" = "false" ]; then
    # Idempotency guard: skip creation if the release already exists (safe for re-runs)
    if gh release view "$TAG" &>/dev/null; then
        echo "  Release ${TAG} already exists — skipping creation."
    elif [ -n "$NOTES_FILE" ] && [ -f "$NOTES_FILE" ]; then
        # Truncate changelog if it exceeds GitHub API limit (125000 chars)
        GH_BODY_LIMIT=120000
        NOTES_SIZE=$(wc -c < "$NOTES_FILE")
        if [ "$NOTES_SIZE" -gt "$GH_BODY_LIMIT" ]; then
            echo "Warning: changelog is ${NOTES_SIZE} chars (limit ${GH_BODY_LIMIT}), truncating..."
            TRUNCATED_FILE=$(mktemp)
            # shellcheck disable=SC2064
            trap "rm -f '$TRUNCATED_FILE'" EXIT
            head -c "$GH_BODY_LIMIT" "$NOTES_FILE" > "$TRUNCATED_FILE"
            printf '\n\n*(Changelog truncated — see full git log for details)*\n' >> "$TRUNCATED_FILE"
            NOTES_FILE="$TRUNCATED_FILE"
        fi

        # Use structured changelog file (from `make release`)
        # shellcheck disable=SC2086
        gh release create "$TAG" \
            --title "${TAG}" \
            -F "$NOTES_FILE" \
            $PRERELEASE_FLAG
    elif [ -n "$NOTES_START" ]; then
        # Auto-generate notes from commits (from CI)
        # shellcheck disable=SC2086
        gh release create "$TAG" \
            --title "${TAG}" \
            --generate-notes \
            --notes-start-tag "$NOTES_START" \
            $PRERELEASE_FLAG
    else
        # shellcheck disable=SC2086
        gh release create "$TAG" \
            --title "${TAG}" \
            --generate-notes \
            $PRERELEASE_FLAG
    fi
else
    echo "  [dry-run] gh release create ${TAG} ..."
fi

echo "✓ Release published: ${TAG}"
echo "  https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/${TAG}"
