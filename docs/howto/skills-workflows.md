---
audience: user
---

# How to Reuse Context with Skills

Every new gptme session starts blank. If your project has conventions (a naming
scheme, a non-obvious architecture decision, a required step before running
tests), you end up pasting the same explanation repeatedly — or getting wrong
first drafts because the agent lacked context.

A *skill* is a plain markdown document (`SKILL.md`) that gptme injects into the
session context when its name is requested. Write the explanation **once**,
commit it, and gptme makes it available on demand. Use this guide to create a
skill and to compose skills into multi-step workflows.

## Create a skill

Skills live in named subdirectories under `skills/` at your project root, or
under `~/.config/gptme/skills/` for user-global skills. For example, a skill for
your project's database migration convention:

````bash
mkdir -p skills/db-migration
cat > skills/db-migration/SKILL.md << 'EOF'
---
name: db-migration
description: Django migration conventions and pre-migration checklist
---

# Migration Workflow

## Before creating a migration

1. Run `make lint` — schema changes that fail lint cause corrupt migrations.
2. Squash pending migrations if there are more than 10.
3. Never create a migration on an unmerged feature branch.

## Creating a migration

```bash
python manage.py makemigrations --name describe_the_change
```

## Reverting a migration

```bash
python manage.py migrate myapp 0042  # the migration before yours
```
EOF
````

gptme discovers skills in the project's `skills/` directory automatically — no
configuration needed.

## Use a skill

Skills are loaded by name. Request one explicitly in your prompt:

```bash
gptme "Using the db-migration skill, help me add a nullable email field to the User model"
```

## Compose skills into a workflow

Real workflows are multi-step: run tests, update a changelog, create a tag, push.
A single monolithic prompt for this is brittle — it's hard to update one step
without breaking others, and you can't reuse the steps elsewhere.

Instead, keep detailed conventions in focused sub-skills and sequence them in a
workflow skill. Three focused sub-skills, each in its own `SKILL.md`:

```markdown
# skills/release-check-tests/SKILL.md
---
name: release-check-tests
description: Verify the test suite passes before any release action
---
# Check Tests Before Release

Always run the full test suite before any release action:

    make test

If any test fails, STOP and fix it before proceeding.
```

```markdown
# skills/release-update-changelog/SKILL.md
---
name: release-update-changelog
description: Changelog update convention for releases
---
# Changelog Update Convention

Format: `## [X.Y.Z] - YYYY-MM-DD` with sections Added / Changed / Fixed / Removed.
Always add the new version block at the TOP of CHANGELOG.md, above previous versions.
Never delete old entries.
```

```markdown
# skills/release-tag-and-push/SKILL.md
---
name: release-tag-and-push
description: Git tagging and push convention for releases
---
# Tag and Push Convention

Create an annotated tag:

    git tag -a v$VERSION -m "Release v$VERSION" && git push origin master --tags

Do not push without a passing CI run on master.
```

A workflow skill that chains all three:

```markdown
# skills/release-workflow/SKILL.md
---
name: release-workflow
description: Full release pipeline — test, changelog, tag, push
---
# Release Workflow

Steps in order:
1. Run `make test` (stop on failure)
2. Update CHANGELOG.md with the new version
3. Commit the changelog: `git commit CHANGELOG.md -m "chore: update changelog for vX.Y.Z"`
4. Create the annotated tag and push: `git tag -a v$VERSION -m "Release v$VERSION" && git push origin master --tags`
```

Name the workflow **and** every sub-skill you want loaded — mentioning the
workflow alone does not recursively load the skills it references:

```bash
gptme "Using the release-workflow, release-check-tests, release-update-changelog, and release-tag-and-push skills, do a release for v1.4.2"
```

## Tips

- **Keep sub-skills short**: under ~50 lines each. Long skills dilute focus; split them.
- **Commit skills to the repo** so all contributors benefit, and use
  `git log skills/` to audit how your workflows evolved.
- **Write once, use everywhere**: skills also work in other runtimes, such as
  Claude Code with the hook adapter.
- See {doc}`../skills` for the full skill format reference, and {doc}`../lessons`
  for guidance that is injected automatically by keyword instead of by name.
