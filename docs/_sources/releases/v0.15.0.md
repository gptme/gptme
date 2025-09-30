# v0.15.0

These are the release notes for gptme version v0.15.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare

## Changelog

Changes since v0.14.2:


## 📦 gptme

### ✨ Features (4)

 - feat: added `screenshot_url` function to browser tool ([`9cd38500`](https://github.com/gptme/gptme/commit/9cd38500))
 - feat(bot): support non-change questions/answers ([`de9844d6`](https://github.com/gptme/gptme/commit/de9844d6))
 - feat: added special prompting for --non-interactive ([`9d912c37`](https://github.com/gptme/gptme/commit/9d912c37))
 - feat(github-action): use Docker for gptme execution ([`97f87247`](https://github.com/gptme/gptme/commit/97f87247))

### 🐛 Fixes (11)
<details><summary>Click to expand</summary>
<p>

 - fix: minor improvement to patch tool prompt ([`722b2c4a`](https://github.com/gptme/gptme/commit/722b2c4a))
 - fix: fixed bug with extra 'Skipped hidden system messages' lines from workspace prompt ([`5937cb7a`](https://github.com/gptme/gptme/commit/5937cb7a))
 - fix(anthropic): fixed vision and other issues with preparing messages ([`b9b84554`](https://github.com/gptme/gptme/commit/b9b84554))
 - fix: don't include paths for slash-command arguments, dont inlude workspace prompt on resume ([`4900d190`](https://github.com/gptme/gptme/commit/4900d190))
 - fix: catch rich.print() errors and fall back to builtins.print() when printing messages ([`8698befd`](https://github.com/gptme/gptme/commit/8698befd))
 - fix: fixed bug checking for browser tool when not available ([`f86569df`](https://github.com/gptme/gptme/commit/f86569df))
 - fix: set session size for tmux tool, fixed wrong tmux examples (terminal instead of tmux) ([`1ef45875`](https://github.com/gptme/gptme/commit/1ef45875))
 - fix: changed tabulate tablefmt in eval output ([`42518547`](https://github.com/gptme/gptme/commit/42518547))
 - fix: fixed docker workspace permissions in bot action ([`aee7f95b`](https://github.com/gptme/gptme/commit/aee7f95b))
 - fix: switch from timeout-minutes to using `timeout` command in bot action step ([`fa23c669`](https://github.com/gptme/gptme/commit/fa23c669))
 - fix: add 'shell' to shell tool's block_types ([`d2c48790`](https://github.com/gptme/gptme/commit/d2c48790))

</p>
</details>

### 🔨 Misc (9)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.15.0 ([`01c48121`](https://github.com/gptme/gptme/commit/01c48121))
 - docs: added TODO comment in python tool about which venv the repl should ideally run in ([`0d8eb85f`](https://github.com/gptme/gptme/commit/0d8eb85f))
 - docs: fixed bad reference ([`780b3c85`](https://github.com/gptme/gptme/commit/780b3c85))
 - refactor(eval): refactored gptme.eval module, splitting gptme.eval.evals into gptme.eval.suites.{basic, init-project, browser} ([`bfe5e1b8`](https://github.com/gptme/gptme/commit/bfe5e1b8))
 - docs: removed Inputs section from bot.md ([`e18dcb1e`](https://github.com/gptme/gptme/commit/e18dcb1e))
 - docs: renamed webui.rst to server.rst, added bot.md to index and updated instructions ([`18b7268d`](https://github.com/gptme/gptme/commit/18b7268d))
 - docs: added example of eval run output ([`c1987fa3`](https://github.com/gptme/gptme/commit/c1987fa3))
 - tests: fixed blinking test ([`c6953b39`](https://github.com/gptme/gptme/commit/c6953b39))
 - tests: clarified that we're testing so that it doesnt try to show runnable examples ([`8514bd33`](https://github.com/gptme/gptme/commit/8514bd33))

</p>
</details>

*(excluded 20 less relevant [commits](https://github.com/gptme/gptme/compare/v0.14.2...v0.15.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.14.2...v0.15.0
