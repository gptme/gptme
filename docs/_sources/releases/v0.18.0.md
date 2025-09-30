# v0.18.0

These are the release notes for gptme version v0.18.0.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare

## Changelog

Changes since v0.17.0:


## 📦 gptme

### ✨ Features (1)

 - feat: added better interrupt handling, requiring two interrupts within 1 sec to exit (when not generating response or executing tools) ([`70290590`](https://github.com/gptme/gptme/commit/70290590))

### 🐛 Fixes (16)
<details><summary>Click to expand</summary>
<p>

 - fix: nit to python tool instructions ([`94b1aaf1`](https://github.com/gptme/gptme/commit/94b1aaf1))
 - fix: refactored cli params, removed special 'ask' value for name ([`cbf1d12b`](https://github.com/gptme/gptme/commit/cbf1d12b))
 - fix: persist pinned and hide to jsonl, only include files, pinned, etc in toml output if set ([`c54c1f24`](https://github.com/gptme/gptme/commit/c54c1f24))
 - fix: fixed bug in refactor ([`43f912eb`](https://github.com/gptme/gptme/commit/43f912eb))
 - fix: fix resume after refactor ([`5d1a7612`](https://github.com/gptme/gptme/commit/5d1a7612))
 - fix: minor fixes, set Console.log_path=False, undo /exit message before exit ([`711cab4c`](https://github.com/gptme/gptme/commit/711cab4c))
 - fix: fixes to cli, improved interrupt, refactored conversation picking to not run when piped, dont run assistant until user message present (project context fix) ([`417b319b`](https://github.com/gptme/gptme/commit/417b319b))
 - fix: improve rich usage, change calls to use gptme.util.console.{print,input,log} ([`8cf53cbd`](https://github.com/gptme/gptme/commit/8cf53cbd))
 - fix: improved browser tool search output, if python tool had result then skip stdout in msg ([`4aaf2023`](https://github.com/gptme/gptme/commit/4aaf2023))
 - fix: updated system prompt to mention `<thinking>` tags ([`c686dab8`](https://github.com/gptme/gptme/commit/c686dab8))
 - fix: limit shell output ([`8a62859b`](https://github.com/gptme/gptme/commit/8a62859b))
 - fix: limited default number of listed conversations to 20 in webui ([`84ab2201`](https://github.com/gptme/gptme/commit/84ab2201))
 - fix: added OpenRouter url when asking for API key ([`87280127`](https://github.com/gptme/gptme/commit/87280127))
 - fix: fix conversation list order in picker, lazily load conversation metadata, add get_user_conversations(), add ?limit=`<int>` to /api/conversations and use it in webui ([`9c53aa0f`](https://github.com/gptme/gptme/commit/9c53aa0f))
 - fix: set gptme.__version__ ([`abcfec0a`](https://github.com/gptme/gptme/commit/abcfec0a))
 - fix: fixed prompt chaining, added test (fixes [#106](https://github.com/gptme/gptme/issues/106)) ([`deac8dba`](https://github.com/gptme/gptme/commit/deac8dba))

</p>
</details>

### 🔨 Misc (15)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.18.0 ([`4a7404f4`](https://github.com/gptme/gptme/commit/4a7404f4))
 - refactor: separated cli/interactive stuff from chat() into main() ([`9808166d`](https://github.com/gptme/gptme/commit/9808166d))
 - docs: added more examples ([`da88a5f5`](https://github.com/gptme/gptme/commit/da88a5f5))
 - docs: improved docs config, fixed warnings, build in strict mode in CI (no warning allowed) ([`ae45141f`](https://github.com/gptme/gptme/commit/ae45141f))
 - tests: fixed browser search test ([`a106d5c0`](https://github.com/gptme/gptme/commit/a106d5c0))
 - docs(README): added ToC ([`e88d4265`](https://github.com/gptme/gptme/commit/e88d4265))
 - docs: added link to examples from intro ([`6d1471e0`](https://github.com/gptme/gptme/commit/6d1471e0))
 - refactor: renamed function to remove 'private' underscore prefix ([`a7a5cf66`](https://github.com/gptme/gptme/commit/a7a5cf66))
 - docs: updated README ([`5983f5b5`](https://github.com/gptme/gptme/commit/5983f5b5))
 - tests: fixed test ([`d0a946b2`](https://github.com/gptme/gptme/commit/d0a946b2))
 - docs: added TODO comment ([`8ad35e15`](https://github.com/gptme/gptme/commit/8ad35e15))
 - docs: minor improved examples ([`1158d95b`](https://github.com/gptme/gptme/commit/1158d95b))
 - docs: improved docs structure (User & Dev guide), improved CLI & API Reference, extracted Prompts as new page, added Examples ([`e1b881a3`](https://github.com/gptme/gptme/commit/e1b881a3))
 - format: fixed formatting and typing ([`68c25526`](https://github.com/gptme/gptme/commit/68c25526))
 - docs: added external link to docs index/sidebar ([`c9bc4884`](https://github.com/gptme/gptme/commit/c9bc4884))

</p>
</details>

*(excluded 8 less relevant [commits](https://github.com/gptme/gptme/compare/v0.17.0...v0.18.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.17.0...v0.18.0
