PR lifecycle
============

This page describes what usually happens after you open a pull request against
``gptme``.

It is intentionally written as a snapshot of **observed repo behavior**, not a
promise. Numbers below were measured from the last 50 merged PRs as of
2026-07-25, and repo policy details were checked on 2026-07-26.

Quick expectations
------------------

- Automated feedback usually lands first. Across the last 50 merged PRs, the
  median first non-author response was **0.04 hours** (about **2.4 minutes**),
  mostly from bots and CI.

- Merges are currently fast when a PR is small and green. Across the same 50
  merged PRs, the median time from open to merge was **2.29 hours**.

- Human maintainer follow-up is less uniform than bot feedback. Only 11 of those
  50 merged PRs had on-thread maintainer follow-up, but when that
  happened the median first maintainer response was **1.32 hours**.

- ``master`` currently has **no GitHub branch-protection rule**. Merge decisions
  are made by maintainer judgment, not by a rigid GitHub gate.

What happens after you open a PR
--------------------------------

1. GitHub Actions runs the project checks. Expect CI signal quickly.

2. Automated reviewers may comment early. In practice this often includes
   Greptile and Codecov before a human replies.

3. Maintainers look for a narrow scope, clear rationale, and whether the change
   is actually ready to merge rather than still in the "design in the PR
   thread" phase.

4. If the change is small, tested, and easy to verify, it may merge without a
   long discussion.

What maintainers look for
-------------------------

These are the current merge criteria in practice:

- **Green CI**. There is no hard GitHub branch rule right now, but merged PRs
  are typically clean.

- **Clear scope**. Small, surgical PRs move faster than mixed refactor +
  feature bundles.

- **Enough verification**. Add or update tests when the change affects behavior.

- **Context**. Link the issue when there is one, and explain the user-facing or
  architectural reason for the change in the PR body.

- **Resolvable review feedback**. If Greptile, Codecov, or a maintainer flags a
  real issue, address it or explain why it is a false positive.

How to make your PR easy to merge
---------------------------------

- Keep the diff focused on one problem.

- Run the relevant tests locally before you push.

- Include screenshots or terminal output when the change affects UX.

- Say what you verified, not just what you changed.

- If the work is non-trivial, open or link an issue first so the PR is not the
  first place design context appears.

What slows review down
----------------------

- Red CI or missing verification

- A PR that mixes unrelated fixes

- No explanation of why the change exists

- Large diffs that require reconstructing design intent from code alone

- Repeated force-pushes that invalidate earlier review context

Current contributor reality
---------------------------

The repo is moving quickly right now. That is good for throughput, but it means
the safest assumption is:

- automation will respond first,

- maintainers will optimize for clear, mergeable slices,

- and the fastest path is a small PR with obvious verification.

If you want a concrete starting point before coding, use the issue labels in
:doc:`contributing` and comment on the issue so other contributors know you are
on it.
