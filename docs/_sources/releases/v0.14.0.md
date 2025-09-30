# v0.14.0

These are the release notes for gptme version v0.14.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare

## Changelog

Changes since v0.13.2:


## 📦 gptme

### ✨ Features (2)

 - feat: anthropic prompt caching beta ([`bb3d9a58`](https://github.com/gptme/gptme/commit/bb3d9a58))
 - feat: started working on vision ([`7b0c5b0f`](https://github.com/gptme/gptme/commit/7b0c5b0f))

### 🐛 Fixes (25)
<details><summary>Click to expand</summary>
<p>

 - fix: improved test flakiness ([`0758c4d4`](https://github.com/gptme/gptme/commit/0758c4d4))
 - fix: check if gitignore exists ([`d664f4b2`](https://github.com/gptme/gptme/commit/d664f4b2))
 - fix: moved vue@create example into bash tool (dont try to do it interactively) ([`5ed36eb8`](https://github.com/gptme/gptme/commit/5ed36eb8))
 - fix: switch to ```ipython syntax for executing with python tool, to differentiate from code samples (see [#67](https://github.com/gptme/gptme/issues/67)) ([`42ee56ab`](https://github.com/gptme/gptme/commit/42ee56ab))
 - fix: changed save format to be more explicit ([`68077b6d`](https://github.com/gptme/gptme/commit/68077b6d))
 - fix: removed spammy warning ([`90aa4a62`](https://github.com/gptme/gptme/commit/90aa4a62))
 - fix: removed use of NotRequired for TypedDict (not in Python 3.10) ([`cfce1303`](https://github.com/gptme/gptme/commit/cfce1303))
 - fix: use anthropic api max_retries instead of custom retry_anthropic decorator ([`008f58c5`](https://github.com/gptme/gptme/commit/008f58c5))
 - fix: added retry_anthropic decorator for rate limits ([`d37d7fbc`](https://github.com/gptme/gptme/commit/d37d7fbc))
 - fix: fixed to evals, capture eval output on timeout/terminate ([`cd0862a7`](https://github.com/gptme/gptme/commit/cd0862a7))
 - fix: fixed spammy prints ([`855a46b0`](https://github.com/gptme/gptme/commit/855a46b0))
 - fix: clarified return format for subagent ([`e5e2a9ab`](https://github.com/gptme/gptme/commit/e5e2a9ab))
 - fix: fixed bug in transform_examples_to_chat_directives ([`2997aa43`](https://github.com/gptme/gptme/commit/2997aa43))
 - fix: print logs dir with --version command ([`1ca6127f`](https://github.com/gptme/gptme/commit/1ca6127f))
 - fix: enabled stricter linting and fixed lints (apparently needed given a25aa7d369dec79341500bcf735e4237def53052) ([`bf67b323`](https://github.com/gptme/gptme/commit/bf67b323))
 - fix: fixed nasty bug with mutable argument default ([`a25aa7d3`](https://github.com/gptme/gptme/commit/a25aa7d3))
 - fix: switch recommended openai model to gpt-4o ([`b3582aca`](https://github.com/gptme/gptme/commit/b3582aca))
 - fix: moved len_tokens and msgs2dicts from util.py to message.py ([`94bade33`](https://github.com/gptme/gptme/commit/94bade33))
 - fix: support nested codeblocks, rewrote/refactored codeblock parsing/management ([`3e291a4f`](https://github.com/gptme/gptme/commit/3e291a4f))
 - fix: fixed incorrect storage format for `Message.to_dict` ([`07f1cbbf`](https://github.com/gptme/gptme/commit/07f1cbbf))
 - fix: added preliminary nested codeblock support ([`885e544b`](https://github.com/gptme/gptme/commit/885e544b))
 - fix: added openai vision support ([`6bbec93b`](https://github.com/gptme/gptme/commit/6bbec93b))
 - fix: completed basic vision support ([`f1846079`](https://github.com/gptme/gptme/commit/f1846079))
 - fix: correct extremely short 'Thinking...' message, now wait until first character ([`b25e576c`](https://github.com/gptme/gptme/commit/b25e576c))
 - fix: typing for subagent tool, added --check-untypes-defs to mypy ([`d94a71e5`](https://github.com/gptme/gptme/commit/d94a71e5))

</p>
</details>

### 🔨 Misc (20)
<details><summary>Click to expand</summary>
<p>

 - chore: bumped version to v0.14.0 ([`d4c55b61`](https://github.com/gptme/gptme/commit/d4c55b61))
 - refactor: renamed terminal tool to tmux ([`79355bc6`](https://github.com/gptme/gptme/commit/79355bc6))
 - tests: increase max tokens for full system prompt significantly ([`c785cfef`](https://github.com/gptme/gptme/commit/c785cfef))
 - tests: made test_subprocess less flaky ([`34234742`](https://github.com/gptme/gptme/commit/34234742))
 - tests: added difficult but reasonably simple integration test example (create vite project, build simple app) ([`57b6febb`](https://github.com/gptme/gptme/commit/57b6febb))
 - docs(README): minor improvements ([`c43cc8a1`](https://github.com/gptme/gptme/commit/c43cc8a1))
 - docs(README): misc minor improvements ([`c705c801`](https://github.com/gptme/gptme/commit/c705c801))
 - tests: removed uninteresting examples from test-integration.sh ([`18c1afa0`](https://github.com/gptme/gptme/commit/18c1afa0))
 - docs: added links to top of README ([`b15d6865`](https://github.com/gptme/gptme/commit/b15d6865))
 - docs(README): added mention that vision works, 'GPTMe' -> 'gptme' for title, mention OpenRouter support ([`4e39f634`](https://github.com/gptme/gptme/commit/4e39f634))
 - tests: run both eval tests (cli and direct call) for better coverage somehow ([`859efbec`](https://github.com/gptme/gptme/commit/859efbec))
 - tests: fixed tests failing due to new file ([`706f13c9`](https://github.com/gptme/gptme/commit/706f13c9))
 - refactor: refactored provider-specific code into new files llm_openai.py and llm_anthropic.py ([`eec82155`](https://github.com/gptme/gptme/commit/eec82155))
 - tests: fix coverage for eval tests (multiprocessing workaround) ([`9ef1ec46`](https://github.com/gptme/gptme/commit/9ef1ec46))
 - tests: added test_eval_cli and cleaned up eval code ([`c0e5c874`](https://github.com/gptme/gptme/commit/c0e5c874))
 - tests: basic test for evals ([`770ed2ca`](https://github.com/gptme/gptme/commit/770ed2ca))
 - tests: fix --version test ([`4ca5078a`](https://github.com/gptme/gptme/commit/4ca5078a))
 - tests: remove ambiguity in subagent fib test prompt ([`fe063ec1`](https://github.com/gptme/gptme/commit/fe063ec1))
 - tests: added test for vision ([`a740194b`](https://github.com/gptme/gptme/commit/a740194b))
 - refactor: moved eval code into `gptme.eval`, added `gptme-eval` entrypoint, fixed typing ([`8a1bb097`](https://github.com/gptme/gptme/commit/8a1bb097))

</p>
</details>

*(excluded 14 less relevant [commits](https://github.com/gptme/gptme/compare/v0.13.2...v0.14.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.13.2...v0.14.0
