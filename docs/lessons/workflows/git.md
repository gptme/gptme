---
match:
  keywords: [git commit, git push, git branch, git worktree, pull request, gh pr, worktree]
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
# Canonical path inside gptme: native gh tool surface plus conversation comments
gh pr view $PR_URL --comments

# Compatibility wrapper from a checkout: also includes inline review threads
./scripts/gh-pr-view-with-pr-comments.sh $PR_URL
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

### Best Practice: A persistent directory outside the repo

```shell
# Create the worktree outside the repository, in a directory that survives reboots
git worktree add ~/worktrees/gptme-feat-name -b feat/feature-name origin/master

# Work in the worktree
cd ~/worktrees/gptme-feat-name
# ... make changes, commit, push ...

# Clean up after the PR merges
git worktree remove ~/worktrees/gptme-feat-name
```

### Why not /tmp/?

- ❌ **Wiped on reboot**: `/tmp` is cleared at boot (macOS and many Linux setups), and with it
  any uncommitted or unpushed work in the worktree. Commit and push early either way.
- ❌ **Not reliably private**: shared temp directories can be cleaned by other tools.

### Why outside the repo?

- ✅ **No duplicates**: grep/find/ripgrep in the main checkout won't find copies of every file
- ✅ **No accidents**: the worktree directory can't be committed into the repo
- ✅ **Clean project**: the main checkout stays uncluttered

### Avoid These Patterns

- ❌ `/tmp/...` - lost on reboot, together with any unpushed commits
- ❌ `./worktree/` - Causes grep duplicates, often not cleaned up
- ❌ `../repo-worktree-feat` - Messy sibling directories
- ❌ Synced folders (Dropbox, Syncthing) - `.git` files collect sync conflicts
- ❌ Forgetting to remove worktrees after PR merge

### Worktree Commands

```shell
# List all worktrees
git worktree list

# Check if worktree exists before creating
git worktree list | grep feat-name

# Remove a worktree
git worktree remove ~/worktrees/gptme-feat-name

# Prune stale worktree references
git worktree prune
```
