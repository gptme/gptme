---
match:
  keywords: [git, commit, push, branch, pr, pull request, worktree]
  tools: [shell, gh]
---

# Git Workflow Best Practices

Guidelines for effective git usage in gptme.

## Commit Best Practices

1. **Use Conventional Commits format**:
   - `feat:` - New features (not just docs)
   - `fix:` - Bug fixes
   - `docs:` - Documentation only changes
   - `refactor:` - Code restructuring
   - `test:` - Test additions/changes
   - `chore:` - Maintenance tasks

2. **Stage files selectively**:
```shell
# Check what changed
git status

# Stage specific files
git add path/to/file.py

# ❌ Never use git add . or git add -A
```

3. **Write clear commit messages**:
```shell
# Use HEREDOC for complex messages
git commit -m "$(cat <<'EOF'
feat: add new feature

Detailed explanation of the feature
and its benefits.
EOF
)"
```

## Branch Workflow

1. **Create feature branches for non-trivial changes**:
```shell
git checkout -b feat/new-feature
```

2. **Keep branches focused**: One logical change per branch

3. **Create PRs for review**:
```shell
gh pr create --title 'Title' --body 'Description'
```

## Working with PRs

### Reading PRs with full context
```shell
# Use the enhanced PR viewer
~/Programming/gptme/scripts/gh-pr-view-with-pr-comments.sh $PR_URL
```

### Checking PR status
```shell
# View PR details
gh pr view $PR_URL

# Check CI status
gh pr checks $PR_URL
```

## Common Mistakes to Avoid

- ❌ Don't use `git reset --hard` (destructive)
- ❌ Don't use `git commit -a` (stages all files)
- ❌ Don't use `git add -A` or `git add .` (adds unintended files)
- ✅ Use `git status` before commits
- ✅ Stage files explicitly with `git add`
- ✅ Use `git stash` to save uncommitted changes

## Using Git Worktrees

Worktrees allow parallel work on multiple branches without switching.

### Best Practice: Use /tmp/ for Worktrees

```shell
# Create worktree in /tmp/ (recommended)
git worktree add /tmp/gptme-feat-name feat/feature-name

# Work in the worktree
cd /tmp/gptme-feat-name
# ... make changes, commit, push ...

# Clean up when done
git worktree remove /tmp/gptme-feat-name
```

### Why /tmp/?

- ✅ **Auto-cleaned**: Removed on reboot (no orphaned worktrees)
- ✅ **No duplicates**: grep/find/ripgrep won't find duplicate files
- ✅ **No accidents**: Can't accidentally commit worktree directory
- ✅ **Clean project**: Main repo stays uncluttered

### Avoid These Patterns

- ❌ `./worktree/` - Causes grep duplicates, often not cleaned up
- ❌ `../repo-worktree-feat` - Messy sibling directories
- ❌ Forgetting to remove worktrees after PR merge

### Worktree Commands

```shell
# List all worktrees
git worktree list

# Check if worktree exists before creating
git worktree list | grep feat-name

# Remove a worktree
git worktree remove /tmp/gptme-feat-name

# Prune stale worktree references
git worktree prune
```
