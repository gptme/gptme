# v0.17.0

These are the release notes for gptme version v0.17.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare

## Changelog

Changes since v0.16.0:


## 📦 gptme

### ✨ Features (1)

 - feat: add support for XML-formatted tool calls ([#121](https://github.com/gptme/gptme/issues/121)) ([`d0b070fc`](https://github.com/gptme/gptme/commit/d0b070fc))

### 🐛 Fixes (23)
<details><summary>Click to expand</summary>
<p>

 - fix: changed warning log to debug ([`0c4c1869`](https://github.com/gptme/gptme/commit/0c4c1869))
 - fix: minor eval refactor (improved type names), clarified python tool instructions ([`e0c79a41`](https://github.com/gptme/gptme/commit/e0c79a41))
 - fix: disable tqdm in tests ([`77ef4fa1`](https://github.com/gptme/gptme/commit/77ef4fa1))
 - fix: reduced number of decimals in eval output ([`b216179b`](https://github.com/gptme/gptme/commit/b216179b))
 - fix: removed spammy message when not in a git repo ([`be3d0232`](https://github.com/gptme/gptme/commit/be3d0232))
 - fix: futher reliability improvements to evals ([`622e5744`](https://github.com/gptme/gptme/commit/622e5744))
 - fix: added tqdm progress bar to eval ([`cd33e06d`](https://github.com/gptme/gptme/commit/cd33e06d))
 - fix: nitpick ([`11f1e7ee`](https://github.com/gptme/gptme/commit/11f1e7ee))
 - fix: improved typing in gptme.evals.run ([`6d00be7a`](https://github.com/gptme/gptme/commit/6d00be7a))
 - fix: more fixes, speed up list_chats and search_chats by lazily searching chronologically ([`8c3cb778`](https://github.com/gptme/gptme/commit/8c3cb778))
 - fix: more fixes and store eval case results in result directory ([`1e23ecbb`](https://github.com/gptme/gptme/commit/1e23ecbb))
 - fix: comment out warning for unknown codeblock types, add lru_cache to frequently called get_tool_for_langtag, added wip llm_openai.list_models ([`aff213f6`](https://github.com/gptme/gptme/commit/aff213f6))
 - fix: refactored evals, fixed leaked semaphore warnings, read logs and naively compute tokens from output ([`3bbd88b4`](https://github.com/gptme/gptme/commit/3bbd88b4))
 - fix: improved system prompt, added system prompt to docs ([#123](https://github.com/gptme/gptme/issues/123)) ([`62220b1a`](https://github.com/gptme/gptme/commit/62220b1a))
 - fix: refactored and improved evals ([#122](https://github.com/gptme/gptme/issues/122)) ([`e4eb81ca`](https://github.com/gptme/gptme/commit/e4eb81ca))
 - fix: improved eval stream capturing logic ([`eecabac8`](https://github.com/gptme/gptme/commit/eecabac8))
 - fix: improved evals output capturing, don't capture by default if a single test is run, and write streams to results directory ([`8e9ad4c1`](https://github.com/gptme/gptme/commit/8e9ad4c1))
 - fix: process eval run futures in the order they are finished, instead of waiting in order ([`5601fae5`](https://github.com/gptme/gptme/commit/5601fae5))
 - fix: remove the missing datascience packages warning at python tool init ([`09f115f2`](https://github.com/gptme/gptme/commit/09f115f2))
 - fix: add ignorelist for certain known non-executable codeblock langs ([`7b82b033`](https://github.com/gptme/gptme/commit/7b82b033))
 - fix: lowered logging level for some spammy messages ([`9a729af0`](https://github.com/gptme/gptme/commit/9a729af0))
 - fix: disable placeholder-aware patching if placeholders in original file, improve error message if file not found ([`1a1e9fb6`](https://github.com/gptme/gptme/commit/1a1e9fb6))
 - fix: dont ask for version in bump_version script if already on tag and pyproject updated ([`68aae660`](https://github.com/gptme/gptme/commit/68aae660))

</p>
</details>

### 🔨 Misc (11)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.17.0 ([`3044149e`](https://github.com/gptme/gptme/commit/3044149e))
 - tests: fixed test_search_chats test ([`56ffb5a7`](https://github.com/gptme/gptme/commit/56ffb5a7))
 - docs: updated README ([`a837b327`](https://github.com/gptme/gptme/commit/a837b327))
 - tests: added particle effect integration-test example ([`5aa79555`](https://github.com/gptme/gptme/commit/5aa79555))
 - tests: fixed broken test in CI ([`49472653`](https://github.com/gptme/gptme/commit/49472653))
 - refactor: refactored Message into a frozen dataclass ([`8887ca8b`](https://github.com/gptme/gptme/commit/8887ca8b))
 - refactor: moved openai model metadata into seperate file, added make update-models to use gptme to update it ([`11221dbc`](https://github.com/gptme/gptme/commit/11221dbc))
 - chore: updated gitignore ([`f2b8be1b`](https://github.com/gptme/gptme/commit/f2b8be1b))
 - docs: updated README ([`ee875999`](https://github.com/gptme/gptme/commit/ee875999))
 - tests: fixed test_eval_cli running on models than intended, and not the tested provider ([`d0ab0429`](https://github.com/gptme/gptme/commit/d0ab0429))
 - refactor: refactored eval TypedDict types to dataclasses ([`25de7f71`](https://github.com/gptme/gptme/commit/25de7f71))

</p>
</details>

*(excluded 5 less relevant [commits](https://github.com/gptme/gptme/compare/v0.16.0...v0.17.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.16.0...v0.17.0
