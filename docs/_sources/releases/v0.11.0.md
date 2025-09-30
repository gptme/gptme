# v0.11.0

These are the release notes for gptme version v0.11.0.

## Contributors

Thanks to everyone who contributed to this release:

@AntonOsika, @erikbjare

## Changelog

Changes since v0.10.5:


## 📦 gptme

### ✨ Features (2)

 - feat: mirror working directory in shell and Python process ([#49](https://github.com/gptme/gptme/issues/49)) ([`0b9c3228`](https://github.com/gptme/gptme/commit/0b9c3228))
 - feat: support paths/URLs in any prompt, refactored entrypoint to call a new public API with core logic ([#37](https://github.com/gptme/gptme/issues/37)) ([`aaf60e57`](https://github.com/gptme/gptme/commit/aaf60e57))

### 🐛 Fixes (9)
<details><summary>Click to expand</summary>
<p>

 - fix: exit with appropriate exitcode if evals pass/fail ([`3a0e4dca`](https://github.com/gptme/gptme/commit/3a0e4dca))
 - fix: fixed shell cd test ([`9932b27a`](https://github.com/gptme/gptme/commit/9932b27a))
 - fix: fixed bugs in eval, upload/download binary files, cd to cwd before every shell command ([`cefbbe86`](https://github.com/gptme/gptme/commit/cefbbe86))
 - fix: fixed shell output printing (no extra newlines) ([`cf91873c`](https://github.com/gptme/gptme/commit/cf91873c))
 - fix: fixed a spelling error ([`8c1eadab`](https://github.com/gptme/gptme/commit/8c1eadab))
 - fix: import NotRequired from typing_extensions ([`2718ebac`](https://github.com/gptme/gptme/commit/2718ebac))
 - fix: improved path detection in prompt ([`3f74635d`](https://github.com/gptme/gptme/commit/3f74635d))
 - fix: add price_input and price_output to model metadata, refactored ModelDict TypedDict into ModelMeta dataclass ([`a0f1a731`](https://github.com/gptme/gptme/commit/a0f1a731))
 - fix: switched to ipython for handling Python execution ([#41](https://github.com/gptme/gptme/issues/41)) ([`b75182c7`](https://github.com/gptme/gptme/commit/b75182c7))

</p>
</details>

### 🔨 Misc (14)
<details><summary>Click to expand</summary>
<p>

 - docs: improved entrypoints' docs for better cli docs ([`89506507`](https://github.com/gptme/gptme/commit/89506507))
 - docs: added demos page to docs ([`ab2687c9`](https://github.com/gptme/gptme/commit/ab2687c9))
 - test: run evals as tests, refactor evals, added python-xdist for parallel testing ([`14ca2df6`](https://github.com/gptme/gptme/commit/14ca2df6))
 - test: switch from gpt-3.5-turbo to gpt-4-1106-preview ("gpt-4-turbo") in cli tests ([`75e79bda`](https://github.com/gptme/gptme/commit/75e79bda))
 - refactor: made eval abstractions more general ([#48](https://github.com/gptme/gptme/issues/48)) ([`bf64f208`](https://github.com/gptme/gptme/commit/bf64f208))
 - refactor: moved init code into init.py ([`0e1a0f5e`](https://github.com/gptme/gptme/commit/0e1a0f5e))
 - test: minor improvements to eval ([`e3aa3363`](https://github.com/gptme/gptme/commit/e3aa3363))
 - test: further eval improvements ([`bfc2f14f`](https://github.com/gptme/gptme/commit/bfc2f14f))
 - test: added eval test that accepts stdin ([`28e3a3cc`](https://github.com/gptme/gptme/commit/28e3a3cc))
 - test: fixed bugs and improved output in evals ([`953614f5`](https://github.com/gptme/gptme/commit/953614f5))
 - test: continued work on evals ([`0c070ec2`](https://github.com/gptme/gptme/commit/0c070ec2))
 - test: added basic eval code ([`be678f3a`](https://github.com/gptme/gptme/commit/be678f3a))
 - docs: fixed link to demo ([`66e9e49a`](https://github.com/gptme/gptme/commit/66e9e49a))
 - docs: added more demos ([`7de6c74a`](https://github.com/gptme/gptme/commit/7de6c74a))

</p>
</details>

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.10.5...v0.11.0
