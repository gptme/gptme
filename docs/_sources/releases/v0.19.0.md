# v0.19.0

These are the release notes for gptme version v0.19.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare

## Changelog

Changes since v0.18.2:


## 📦 gptme

### ✨ Features (3)

 - feat: added screenshot tool ([#92](https://github.com/gptme/gptme/issues/92)) ([`f4c63c2a`](https://github.com/gptme/gptme/commit/f4c63c2a))
 - feat: added vision tool ([`597c66c5`](https://github.com/gptme/gptme/commit/597c66c5))
 - feat: added -t/--tools option to specify which tools to load ([`48d559b4`](https://github.com/gptme/gptme/commit/48d559b4))

### 🐛 Fixes (13)
<details><summary>Click to expand</summary>
<p>

 - fix: fixed lint ([`a3bcf495`](https://github.com/gptme/gptme/commit/a3bcf495))
 - fix: removed incorrect calls to register_function, removed dead code ([`a4fa62ef`](https://github.com/gptme/gptme/commit/a4fa62ef))
 - fix: more patch tool refactor ([`9627b73b`](https://github.com/gptme/gptme/commit/9627b73b))
 - fix: removed emoji from ask_execute, added secret `auto` answer in ask_execute ([`5eddc6a5`](https://github.com/gptme/gptme/commit/5eddc6a5))
 - fix: added patch previews ([`dd7be0dd`](https://github.com/gptme/gptme/commit/dd7be0dd))
 - fix: completed diff_minimal ([`df9d83cf`](https://github.com/gptme/gptme/commit/df9d83cf))
 - fix: flush stdin before asking to execute (prevent unread input from answering before asked) ([`6ab4dbe2`](https://github.com/gptme/gptme/commit/6ab4dbe2))
 - fix: implemented example_to_xml to support xml-ify prompt ([#146](https://github.com/gptme/gptme/issues/146)) ([`5f37d104`](https://github.com/gptme/gptme/commit/5f37d104))
 - fix: init tools in evals ([`44e76ff4`](https://github.com/gptme/gptme/commit/44e76ff4))
 - fix: improved patch warning message on large patches ([`5e635b6c`](https://github.com/gptme/gptme/commit/5e635b6c))
 - fix: froze more dataclasses ([`953f8016`](https://github.com/gptme/gptme/commit/953f8016))
 - fix: improve tool init logic for tools needing it (python), added toolspec args docs ([`f8e5cd68`](https://github.com/gptme/gptme/commit/f8e5cd68))
 - fix: use prompt chaining in subagent task to improve reliability ([`0dd6583c`](https://github.com/gptme/gptme/commit/0dd6583c))

</p>
</details>

### 🔨 Misc (8)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.19.0 ([`bbe3586a`](https://github.com/gptme/gptme/commit/bbe3586a))
 - docs: remove completed TODOs ([`337b025c`](https://github.com/gptme/gptme/commit/337b025c))
 - refactor: refactor patch tool, with plans for producing minimal diffs to replace inefficient diffs in log ([`e2b2d6a0`](https://github.com/gptme/gptme/commit/e2b2d6a0))
 - docs: fixed docstring for gptme.prompts ([`027cb06f`](https://github.com/gptme/gptme/commit/027cb06f))
 - docs: keep copyright year up-to-date ([`1ac4aee0`](https://github.com/gptme/gptme/commit/1ac4aee0))
 - docs: fixed automation docs ([`a70b5b5e`](https://github.com/gptme/gptme/commit/a70b5b5e))
 - docs: changed erik.bjareholt.com/gptme/ links to gptme.org/ ([`52baf5b9`](https://github.com/gptme/gptme/commit/52baf5b9))
 - docs: added minimal automation docs ([#144](https://github.com/gptme/gptme/issues/144)) ([`3902b865`](https://github.com/gptme/gptme/commit/3902b865))

</p>
</details>

*(excluded 2 less relevant [commits](https://github.com/gptme/gptme/compare/v0.18.2...v0.19.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.18.2...v0.19.0
