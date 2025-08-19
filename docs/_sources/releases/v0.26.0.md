# v0.26.0

These are the release notes for gptme version v0.26.0.

## Contributors

Thanks to everyone who contributed to this release:

@0xbrayo, @chenrui333, @erikbjare, @jrmi

## Changelog

Changes since v0.25.0:


## 📦 gptme

### ✨ Features (4)

 - feat: added local TTS support using Kokoro ([#401](https://github.com/gptme/gptme/issues/401)) ([`3e8e8698`](https://github.com/gptme/gptme/commit/3e8e8698))
 - feat: allow to load tools from external modules ([#336](https://github.com/gptme/gptme/issues/336)) ([`dd99731c`](https://github.com/gptme/gptme/commit/dd99731c))
 - feat: add support for different tool formats in eval system ([#380](https://github.com/gptme/gptme/issues/380)) ([`5fbc0acb`](https://github.com/gptme/gptme/commit/5fbc0acb))
 - feat: improve tool use response format ([#306](https://github.com/gptme/gptme/issues/306)) ([`1934fcf1`](https://github.com/gptme/gptme/commit/1934fcf1))

### 🐛 Fixes (16)
<details><summary>Click to expand</summary>
<p>

 - fix: fixed sending whitespace-only messages to tts server ([`51ce6aea`](https://github.com/gptme/gptme/commit/51ce6aea))
 - fix: more precise __version__ when installed in editable mode, with git info ([#400](https://github.com/gptme/gptme/issues/400)) ([`9cbaa5da`](https://github.com/gptme/gptme/commit/9cbaa5da))
 - fix: allow overriding logs folder via GPTME_LOGS_HOME ([`a34276c7`](https://github.com/gptme/gptme/commit/a34276c7))
 - fix: dont store invalid commands in log ([`aaf2e9e9`](https://github.com/gptme/gptme/commit/aaf2e9e9))
 - fix: fix /summarize command ([#390](https://github.com/gptme/gptme/issues/390)) ([`c41df98e`](https://github.com/gptme/gptme/commit/c41df98e))
 - fix(server): init tools in server api ([#389](https://github.com/gptme/gptme/issues/389)) ([`b6f337ac`](https://github.com/gptme/gptme/commit/b6f337ac))
 - fix: tool api call broken when user answer no to when asked for confirmation ([#371](https://github.com/gptme/gptme/issues/371)) ([`72fb003d`](https://github.com/gptme/gptme/commit/72fb003d))
 - fix: several fixes to tmux tool, incl support for multiple commands per tooluse ([#382](https://github.com/gptme/gptme/issues/382)) ([`8dbed8eb`](https://github.com/gptme/gptme/commit/8dbed8eb))
 - fix: rename python tool to ipython for better tooluse format adherence ([#361](https://github.com/gptme/gptme/issues/361)) ([`d4407e30`](https://github.com/gptme/gptme/commit/d4407e30))
 - fix: improve smaller model response on user refusals ([#374](https://github.com/gptme/gptme/issues/374)) ([`11708dd1`](https://github.com/gptme/gptme/commit/11708dd1))
 - fix: create workspace symlink in log folder, load on resume ([#368](https://github.com/gptme/gptme/issues/368)) ([`03475102`](https://github.com/gptme/gptme/commit/03475102))
 - fix: shorten command descriptions ([`0d642c22`](https://github.com/gptme/gptme/commit/0d642c22))
 - fix: remove conflicting -n alias for --name, keep as --non-interactive alias (fixes [#360](https://github.com/gptme/gptme/issues/360)) ([`dd26dc23`](https://github.com/gptme/gptme/commit/dd26dc23))
 - fix: prettier /command completions ([`f2e01f3b`](https://github.com/gptme/gptme/commit/f2e01f3b))
 - fix: retry on anthropic overloaded ([#356](https://github.com/gptme/gptme/issues/356)) ([`50763762`](https://github.com/gptme/gptme/commit/50763762))
 - fix: fixed `auto n` option on ask_execute, added log message with remaining skips ([`cc1af13a`](https://github.com/gptme/gptme/commit/cc1af13a))

</p>
</details>

### 🔨 Misc (10)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.26.0 ([`3fef9941`](https://github.com/gptme/gptme/commit/3fef9941))
 - chore: fixed venv docker/gitignore ([`a32fc3bc`](https://github.com/gptme/gptme/commit/a32fc3bc))
 - docs: improve custom tools docs, mentioning simple script tools ([#391](https://github.com/gptme/gptme/issues/391)) ([`d33ff353`](https://github.com/gptme/gptme/commit/d33ff353))
 - test: simplified tool format test, dont skip cli url test if lynx available ([`cdbfae8b`](https://github.com/gptme/gptme/commit/cdbfae8b))
 - chore: added .aider and .env to gitignore ([`54253a5d`](https://github.com/gptme/gptme/commit/54253a5d))
 - test: fix reset_default_model fixture if no default set ([`a5687952`](https://github.com/gptme/gptme/commit/a5687952))
 - test: reset default model in openai tests to fix flaky test ([`7ff84922`](https://github.com/gptme/gptme/commit/7ff84922))
 - test: fix typo in model name ([`dec7a60f`](https://github.com/gptme/gptme/commit/dec7a60f))
 - chore: update brayo-pip -> 0xbrayo ([`871cf63c`](https://github.com/gptme/gptme/commit/871cf63c))
 - chore: updated changelog_contributors.csv cache ([`7abeb1eb`](https://github.com/gptme/gptme/commit/7abeb1eb))

</p>
</details>

*(excluded 11 less relevant [commits](https://github.com/gptme/gptme/compare/v0.25.0...v0.26.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.25.0...v0.26.0
