#!/bin/bash
# Unified version bump script — shared between `make release` and scheduled-release CI.
#
# Usage:
#   ./scripts/bump_version.sh                       # Interactive mode (prompts for version)
#   ./scripts/bump_version.sh --type dev             # Automated: dev pre-release (.devYYYYMMDD)
#   ./scripts/bump_version.sh --type patch           # Automated: stable patch (x.y.Z+1)
#   ./scripts/bump_version.sh --type minor           # Automated: stable minor (x.Y+1.0)
#   ./scripts/bump_version.sh --type dev --date 20260301  # Override date for dev release
#
# Modes:
#   Interactive (no --type): Validates working tree, prompts for version, creates signed tag.
#                            Used by `make release` for human-driven releases.
#   Automated (--type):      Computes version from last stable tag, creates unsigned tag.
#                            Used by scheduled-release CI and `make release-dev`.
#
# In both modes:
#   1. Finds the last stable tag (vX.Y.Z) and last tag of any kind
#   2. Updates pyproject.toml via `poetry version`
#   3. Commits and tags the version bump (does NOT push — caller decides)
#
# Outputs version metadata to stdout and $GITHUB_OUTPUT (if set):
#   version=X.Y.Z[.devDATE]
#   tag=vX.Y.Z[.devDATE]
#   prerelease=true|false
#   title=v... (human label for GitHub release)
#   last_stable=vX.Y.Z
#   last_tag=vX.Y.Z[.devDATE]

set -euo pipefail

TYPE=""
DATE=$(date -u +%Y%m%d)

while [[ $# -gt 0 ]]; do
    case $1 in
        --type) TYPE="$2"; shift 2 ;;
        --date) DATE="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# Resolve from current working directory (works in worktrees)
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# --- Shared: find last stable and last tag ---

LAST_STABLE=$(git tag --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true)
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")

if [ -z "$LAST_STABLE" ]; then
    echo "Error: No stable release tag found (vX.Y.Z). Create an initial release manually first." >&2
    exit 1
fi

STABLE_VERSION="${LAST_STABLE#v}"
IFS='.' read -r MAJOR MINOR PATCH <<< "$STABLE_VERSION"

# --- Output helper ---

_output() {
    echo "$1=$2"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        echo "$1=$2" >> "$GITHUB_OUTPUT"
    fi
}

# --- Interactive mode (no --type) ---

if [ -z "$TYPE" ]; then
    # Validate working tree (only in interactive mode)
    git diff --cached --exit-code || { echo "There are staged files, please commit or unstage them"; exit 1; }
    git diff --exit-code pyproject.toml || { echo "pyproject.toml is dirty, please commit or stash changes"; exit 1; }

    git pull

    VERSION_TAG=$(git describe --tags --abbrev=0 | cut -b 2-)
    VERSION_PYPROJECT=$(poetry version --short)
    IS_COMMIT_TAGGED=$(git tag --points-at HEAD | grep -q "^v[0-9]\+\.[0-9]\+\.[0-9]\+$" && echo "true" || echo "false")

    if [ "${VERSION_TAG}" == "${VERSION_PYPROJECT}" ] && [ "${IS_COMMIT_TAGGED}" == "true" ]; then
        echo "Version ${VERSION_TAG} is already tagged, assuming up-to-date"
        exit 0
    elif [ "${VERSION_TAG}" != "${VERSION_PYPROJECT}" ]; then
        echo "The latest tag is ${VERSION_TAG} but the version in pyproject.toml is ${VERSION_PYPROJECT}"
        echo "Updating the version in pyproject.toml to match the latest tag"
        poetry version "${VERSION_TAG}"
        git add pyproject.toml
        git commit -m "chore: bump version to ${VERSION_TAG}" || echo "No version bump needed"
    else
        read -rp "Enter new version number: " VERSION_NEW
        VERSION_NEW="${VERSION_NEW#v}"
        if [ "${VERSION_NEW}" == "${VERSION_TAG}" ]; then
            echo "Version ${VERSION_NEW} already exists, assuming up-to-date"
            exit 0
        fi
        echo "Bumping version to ${VERSION_NEW}"
        poetry version "${VERSION_NEW}"
        git add pyproject.toml
        git commit -m "chore: bump version to ${VERSION_NEW}"
        git tag -s "v${VERSION_NEW}" -m "v${VERSION_NEW}"

        # Determine if this is a pre-release
        PRERELEASE=false
        if echo "${VERSION_NEW}" | grep -qE '\.(dev|a|b|rc)'; then
            PRERELEASE=true
        fi

        _output "version"     "$VERSION_NEW"
        _output "tag"         "v${VERSION_NEW}"
        _output "prerelease"  "$PRERELEASE"
        _output "title"       "v${VERSION_NEW}"
        _output "last_stable" "$LAST_STABLE"
        _output "last_tag"    "$LAST_TAG"
    fi
    exit 0
fi

# --- Automated mode (--type dev|patch|minor) ---

case "$TYPE" in
    dev)
        NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1)).dev${DATE}"
        PRERELEASE=true
        TITLE="v${NEW_VERSION} (dev)"

        # Handle same-day collision: if tag already exists, append incrementing suffix
        TAG_CANDIDATE="v${NEW_VERSION}"
        SUFFIX=2
        while git tag -l "$TAG_CANDIDATE" | grep -q .; do
            NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1)).dev${DATE}${SUFFIX}"
            TAG_CANDIDATE="v${NEW_VERSION}"
            TITLE="v${NEW_VERSION} (dev)"
            SUFFIX=$((SUFFIX + 1))
        done
        ;;
    patch)
        NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))"
        PRERELEASE=false
        TITLE="v${NEW_VERSION}"
        ;;
    minor)
        NEW_VERSION="${MAJOR}.$((MINOR + 1)).0"
        PRERELEASE=false
        TITLE="v${NEW_VERSION}"
        ;;
    *)
        echo "Unknown release type: $TYPE (expected: dev, patch, minor)" >&2
        exit 1
        ;;
esac

TAG="v${NEW_VERSION}"
echo "Bumping version: $LAST_STABLE → $TAG (type=$TYPE, pre-release=$PRERELEASE)"

# Update pyproject.toml
poetry version "$NEW_VERSION"
echo "Updated pyproject.toml: $(grep '^version' pyproject.toml)"

# Commit and tag
git add pyproject.toml
git commit -m "chore: bump version to ${NEW_VERSION}"
git tag "$TAG"
echo "Created commit and tag: $TAG"

_output "version"     "$NEW_VERSION"
_output "tag"         "$TAG"
_output "prerelease"  "$PRERELEASE"
_output "title"       "$TITLE"
_output "last_stable" "$LAST_STABLE"
_output "last_tag"    "$LAST_TAG"
