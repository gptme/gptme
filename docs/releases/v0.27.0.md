# v0.27.0

These are the release notes for gptme version v0.27.0.

## Contributors

Thanks to everyone who contributed to this release:

@0xbrayo, @erikbjare, @jrmi, @Miyou

## Changelog

Changes since v0.26.0:


## 📦 gptme

### ✨ Features (19)

 - feat: added GPTME_CONTEXT_TREE which includes `tree --gitignore .` output in initial prompt ([#461](https://github.com/gptme/gptme/issues/461)) ([`593ee900`](https://github.com/gptme/gptme/commit/593ee900))
 - feat(style): print `<think>`/`<thinking>` in dimmed text ([`dfab1e05`](https://github.com/gptme/gptme/commit/dfab1e05))
 - feat: added basic files support to server API ([#440](https://github.com/gptme/gptme/issues/440)) ([`24b9dc95`](https://github.com/gptme/gptme/commit/24b9dc95))
 - feat: added script to auto-rename past conversations ([#439](https://github.com/gptme/gptme/issues/439)) ([`9a9f0114`](https://github.com/gptme/gptme/commit/9a9f0114))
 - feat: added terminal title status output ([#438](https://github.com/gptme/gptme/issues/438)) ([`37c877f3`](https://github.com/gptme/gptme/commit/37c877f3))
 - feat: much improved RAG, added LLM post-processing of results ([#435](https://github.com/gptme/gptme/issues/435)) ([`f4b6f02e`](https://github.com/gptme/gptme/commit/f4b6f02e))
 - feat: use gnome-screenshot in wayland ([#436](https://github.com/gptme/gptme/issues/436)) ([`6d1d47a4`](https://github.com/gptme/gptme/commit/6d1d47a4))
 - feat: add status emojis to system messages ([#429](https://github.com/gptme/gptme/issues/429)) ([`69c5f011`](https://github.com/gptme/gptme/commit/69c5f011))
 - feat: added support for nvidia provider ([`7f387d33`](https://github.com/gptme/gptme/commit/7f387d33))
 - feat: auto-enable pre-commit checks when config exists ([#430](https://github.com/gptme/gptme/issues/430)) ([`5cd9b1e8`](https://github.com/gptme/gptme/commit/5cd9b1e8))
 - feat: enhance vision tool with better image processing ([`6a982987`](https://github.com/gptme/gptme/commit/6a982987))
 - feat: add option to override base prompt ([#426](https://github.com/gptme/gptme/issues/426)) ([`78e92091`](https://github.com/gptme/gptme/commit/78e92091))
 - feat(computer): add macOS support for computer tool ([#324](https://github.com/gptme/gptme/issues/324)) ([`7a08e341`](https://github.com/gptme/gptme/commit/7a08e341))
 - feat: added support for GPTME_BREAK_ON_TOOLUSE flag to not stop generation when tooluse occurs in stream ([`e7021b9c`](https://github.com/gptme/gptme/commit/e7021b9c))
 - feat: auto-run precommit checks when tooluse exhausted ([#415](https://github.com/gptme/gptme/issues/415)) ([`254d1b61`](https://github.com/gptme/gptme/commit/254d1b61))
 - feat: added support for deepseek-reasoner ([#410](https://github.com/gptme/gptme/issues/410)) ([`ac3b8258`](https://github.com/gptme/gptme/commit/ac3b8258))
 - feat: added /model command to list/switch models, support mixing providers ([#409](https://github.com/gptme/gptme/issues/409)) ([`a2bc4e00`](https://github.com/gptme/gptme/commit/a2bc4e00))
 - feat: add env var feature flag for llm prompt suggestions ([`978b1c4b`](https://github.com/gptme/gptme/commit/978b1c4b))
 - feat: added gptme-wut ([#402](https://github.com/gptme/gptme/issues/402)) ([`91bdce67`](https://github.com/gptme/gptme/commit/91bdce67))

### 🐛 Fixes (65)
<details><summary>Click to expand</summary>
<p>

 - fix: fixed reduce_context script ([`2f8246c6`](https://github.com/gptme/gptme/commit/2f8246c6))
 - fix: added reduce_context.py script ([`f0edadde`](https://github.com/gptme/gptme/commit/f0edadde))
 - fix: added gh-pr-view-with-pr-comments.sh script ([`3e0dd23d`](https://github.com/gptme/gptme/commit/3e0dd23d))
 - fix: add gemini-2.0-flash-thinking-exp-01-21 to models ([#465](https://github.com/gptme/gptme/issues/465)) ([`489f73e6`](https://github.com/gptme/gptme/commit/489f73e6))
 - fix: fix /export command ([#460](https://github.com/gptme/gptme/issues/460)) ([`0ce55149`](https://github.com/gptme/gptme/commit/0ce55149))
 - fix: fix missing dependency warning ([#455](https://github.com/gptme/gptme/issues/455)) ([`ced1b9ac`](https://github.com/gptme/gptme/commit/ced1b9ac))
 - fix: fixed incorrect reference to GPTME_FRESH_CONTEXT (now just GPTME_FRESH) ([`ce626ed1`](https://github.com/gptme/gptme/commit/ce626ed1))
 - fix: fix circular import ([`417613c1`](https://github.com/gptme/gptme/commit/417613c1))
 - fix: correctly handle too long path names ([`ec1118c6`](https://github.com/gptme/gptme/commit/ec1118c6))
 - fix: dont expand paths in user commands for tools, like /shell ([#453](https://github.com/gptme/gptme/issues/453)) ([`96304695`](https://github.com/gptme/gptme/commit/96304695))
 - fix: fixed a bunch of get_default_model uses, return None instead of exception if unset ([`a3626c82`](https://github.com/gptme/gptme/commit/a3626c82))
 - fix: make model fully optional in prompts.py, no fallback to possibly unset default ([`29a0cb19`](https://github.com/gptme/gptme/commit/29a0cb19))
 - fix: correct content block processing in Anthropic LLM module ([`b4d2d962`](https://github.com/gptme/gptme/commit/b4d2d962))
 - fix(anthropic): use model_meta.supports_reasoning to determine wether to use thinking ([`91af56c1`](https://github.com/gptme/gptme/commit/91af56c1))
 - fix: add supports_reasoning to model_meta, disabling `<thinking>` prompting for such models ([`b15fb6a5`](https://github.com/gptme/gptme/commit/b15fb6a5))
 - fix(anthropic): set max_tokens from model metadata ([`a89ca157`](https://github.com/gptme/gptme/commit/a89ca157))
 - fix: refactor only-once warning of unknown model metadata ([`5d5f0e95`](https://github.com/gptme/gptme/commit/5d5f0e95))
 - fix: disabled `tool` tooluse format for Sonnet 3.7 with thinking, for now ([`4354f3e7`](https://github.com/gptme/gptme/commit/4354f3e7))
 - fix: only log missing model metadata warning once ([`ffb83789`](https://github.com/gptme/gptme/commit/ffb83789))
 - fix: added model metadata for sonnet 3.7 ([`1df392ac`](https://github.com/gptme/gptme/commit/1df392ac))
 - fix: add support for sonnet 3.7 ([`cb83725c`](https://github.com/gptme/gptme/commit/cb83725c))
 - fix: add `model` parameter to `step` function (dont rely on global) ([`ebc076bb`](https://github.com/gptme/gptme/commit/ebc076bb))
 - fix: refactored get_project_dir helper function ([`d231311e`](https://github.com/gptme/gptme/commit/d231311e))
 - fix: prompt to prefer absolute paths ([`ba11552a`](https://github.com/gptme/gptme/commit/ba11552a))
 - fix: fixes after RAG improvement PR ([`486d9858`](https://github.com/gptme/gptme/commit/486d9858))
 - fix: refactor workspace initialization ([`6936a4e1`](https://github.com/gptme/gptme/commit/6936a4e1))
 - fix: fix support for explicitly disabling pre-commit checks ([`603ecaae`](https://github.com/gptme/gptme/commit/603ecaae))
 - fix: added basic `gptme-util chats search` command, fixes to `gptme-util chats` overall ([#434](https://github.com/gptme/gptme/issues/434)) ([`b9a64578`](https://github.com/gptme/gptme/commit/b9a64578))
 - fix: adjust tts speed to 1.0 by default after tts_server.py updated to kokoro 1.0 which seems faster by default ([`6e3af447`](https://github.com/gptme/gptme/commit/6e3af447))
 - fix: upgrade tts_server.py to use kokoro 1.0, fix audio output device on linux ([#432](https://github.com/gptme/gptme/issues/432)) ([`007a0750`](https://github.com/gptme/gptme/commit/007a0750))
 - fix: improved status emojis in formatted messages ([`964b5280`](https://github.com/gptme/gptme/commit/964b5280))
 - fix: automatically clone Kokoro-82M repo in tts_server.py script ([`a6109c71`](https://github.com/gptme/gptme/commit/a6109c71))
 - fix: fixed support for o3 ([`3a810109`](https://github.com/gptme/gptme/commit/3a810109))
 - fix: log reasoning_content when available (i.e. Deepseek R1) ([`e13af3f3`](https://github.com/gptme/gptme/commit/e13af3f3))
 - fix: added support for GPTME_PATCH_RECOVERY where file is returned in error for non-matching patches ([`96ceec24`](https://github.com/gptme/gptme/commit/96ceec24))
 - fix: minor improvements to ipython function description formatting ([`7d2fec38`](https://github.com/gptme/gptme/commit/7d2fec38))
 - fix: auto-step in server (wip), dont use logmanager lock in API ([`c948e3b1`](https://github.com/gptme/gptme/commit/c948e3b1))
 - fix: improve formatting for large patch warnings ([`4ad126e3`](https://github.com/gptme/gptme/commit/4ad126e3))
 - fix: handle None workspace in get_project_config to prevent TypeError ([`d0fee8a6`](https://github.com/gptme/gptme/commit/d0fee8a6))
 - fix: minor logging improvements ([`48782fcd`](https://github.com/gptme/gptme/commit/48782fcd))
 - fix(screenshot): add prompting for screenshot permissions on macos ([`3bc328ea`](https://github.com/gptme/gptme/commit/3bc328ea))
 - fix(vision): rescale large images passed to view_image ([`1a1d969c`](https://github.com/gptme/gptme/commit/1a1d969c))
 - fix(tts): fix logging output in tts server ([`060b5d09`](https://github.com/gptme/gptme/commit/060b5d09))
 - fix(tts): added GPTME_VOICE_FINISH flag to wait for speech to finish before exiting ([`95042d40`](https://github.com/gptme/gptme/commit/95042d40))
 - fix(tts): combine short sentences into larger chunks before sending to tts server ([`d6a941ea`](https://github.com/gptme/gptme/commit/d6a941ea))
 - fix: remove exclusive mode ([#423](https://github.com/gptme/gptme/issues/423)) ([`0c99cb8f`](https://github.com/gptme/gptme/commit/0c99cb8f))
 - fix(tts): make tts server requests non-blocking, improve clean_for_speech ([#422](https://github.com/gptme/gptme/issues/422)) ([`44c3dfe9`](https://github.com/gptme/gptme/commit/44c3dfe9))
 - fix: improve pre-commit output message when files automatically fixed by hook ([`9b4e9117`](https://github.com/gptme/gptme/commit/9b4e9117))
 - fix: added support for knowledge cutoff in model metadata ([`d166b4cb`](https://github.com/gptme/gptme/commit/d166b4cb))
 - fix(eval): use seperate status emoji for timeouts ([`00fdd187`](https://github.com/gptme/gptme/commit/00fdd187))
 - fix: locate espeak library more intelligently on macOS ([`583a6b43`](https://github.com/gptme/gptme/commit/583a6b43))
 - fix: strip leading/trailing silence from tts output ([#420](https://github.com/gptme/gptme/issues/420)) ([`96c54c41`](https://github.com/gptme/gptme/commit/96c54c41))
 - fix: fix call_id regex format for deepseek ([`ec30e9b0`](https://github.com/gptme/gptme/commit/ec30e9b0))
 - fix: fixed tts_server.py on macOS ([#418](https://github.com/gptme/gptme/issues/418)) ([`477a81e2`](https://github.com/gptme/gptme/commit/477a81e2))
 - fix: enable `tool` format for deepseek provider ([`7fda8bdc`](https://github.com/gptme/gptme/commit/7fda8bdc))
 - fix: fixed summarization for openrouter and deepseek ([`d8a9bec6`](https://github.com/gptme/gptme/commit/d8a9bec6))
 - fix: added TODOs for better openrouter support ([`e56491f1`](https://github.com/gptme/gptme/commit/e56491f1))
 - fix: fixed output of codeblocks with unescaped rich [style] tags ([`07ed85fd`](https://github.com/gptme/gptme/commit/07ed85fd))
 - fix: detect tooluses with common path characters when cleaning for speech ([#412](https://github.com/gptme/gptme/issues/412)) ([`9124640b`](https://github.com/gptme/gptme/commit/9124640b))
 - fix: improve default models+toolformats to run in evals (autodetect from available keys) ([`598f4096`](https://github.com/gptme/gptme/commit/598f4096))
 - fix: fixed broken openrouter support due to missing entry in model metadata ([`0cd60a08`](https://github.com/gptme/gptme/commit/0cd60a08))
 - fix: added gptme-eval-docker.sh helper script ([`9e62c3d1`](https://github.com/gptme/gptme/commit/9e62c3d1))
 - fix: broken mixed tool formats execution while using  `tool` format ([#407](https://github.com/gptme/gptme/issues/407)) ([`bcf45cd5`](https://github.com/gptme/gptme/commit/bcf45cd5))
 - fix: improved logging output for gptme-rag calls (incl time taken) ([`33db67ec`](https://github.com/gptme/gptme/commit/33db67ec))
 - fix: cleaned up tts tool, detect if tts server isnt running ([#404](https://github.com/gptme/gptme/issues/404)) ([`d27da5e0`](https://github.com/gptme/gptme/commit/d27da5e0))

</p>
</details>

### 🔨 Misc (42)
<details><summary>Click to expand</summary>
<p>

 - chore: bump version to 0.27.0 ([`96a5b54e`](https://github.com/gptme/gptme/commit/96a5b54e))
 - docs: replaced github.com/ErikBjare/gptme links with github.com/gptme/gptme ([`6ac6f6e7`](https://github.com/gptme/gptme/commit/6ac6f6e7))
 - docs: reference both usage and examples in getting-started guide ([`1bd6a607`](https://github.com/gptme/gptme/commit/1bd6a607))
 - docs: fixed link ([`bb1c3f3f`](https://github.com/gptme/gptme/commit/bb1c3f3f))
 - docs: link to bob from README ([`2349c046`](https://github.com/gptme/gptme/commit/2349c046))
 - refactor: make computer tool share logic with screenshot tool ([#442](https://github.com/gptme/gptme/issues/442)) ([`412bace5`](https://github.com/gptme/gptme/commit/412bace5))
 - docs: mention Claude Code in alternatives ([`05648fb4`](https://github.com/gptme/gptme/commit/05648fb4))
 - refactor: refactored include_paths in chat.py by moving in and helpers into gptme.util.context ([`f008b6a9`](https://github.com/gptme/gptme/commit/f008b6a9))
 - docs: update the evals page to mention recommended model (sonnet) and available evals ([#451](https://github.com/gptme/gptme/issues/451)) ([`92965cd1`](https://github.com/gptme/gptme/commit/92965cd1))
 - refactor: extract get_default_model_summary ([`161451e1`](https://github.com/gptme/gptme/commit/161451e1))
 - docs: fixed broken document_prompt_function ([`004ced9e`](https://github.com/gptme/gptme/commit/004ced9e))
 - refactor: separate all get_default_model logic from get_model ([`5e3aa80a`](https://github.com/gptme/gptme/commit/5e3aa80a))
 - tests: uncomment test and mark to skip instead ([`40dc44c1`](https://github.com/gptme/gptme/commit/40dc44c1))
 - tests: disable broken search_ddg test ([`4a241f4c`](https://github.com/gptme/gptme/commit/4a241f4c))
 - refactor: refactored retry_(generator_)on_overloaded (almost-duplicate decorator) ([`e64006cc`](https://github.com/gptme/gptme/commit/e64006cc))
 - docs: add OpenHands to alternatives ([`1edf80d9`](https://github.com/gptme/gptme/commit/1edf80d9))
 - docs: split sections from 'dev guide' into new 'about' toctree in index ([`8845a1a6`](https://github.com/gptme/gptme/commit/8845a1a6))
 - chore: updated gitignore ([`2bdad1a7`](https://github.com/gptme/gptme/commit/2bdad1a7))
 - docs: improved lead in README ([`d07e799c`](https://github.com/gptme/gptme/commit/d07e799c))
 - docs: improved tools page ([`dae30218`](https://github.com/gptme/gptme/commit/dae30218))
 - docs: updated page with alternatives/comparison ([`5d53aa42`](https://github.com/gptme/gptme/commit/5d53aa42))
 - test: fixed conversation search test ([`8dfc3a06`](https://github.com/gptme/gptme/commit/8dfc3a06))
 - refactor: move default rag post-process prompt from config.py into tools/rag.py ([`0b9ea550`](https://github.com/gptme/gptme/commit/0b9ea550))
 - docs: fixes to config docs ([`2b11a3c7`](https://github.com/gptme/gptme/commit/2b11a3c7))
 - test: add requires_api test mark to run tests without API keys (such as for untrusted PRs) ([#433](https://github.com/gptme/gptme/issues/433)) ([`19895aa8`](https://github.com/gptme/gptme/commit/19895aa8))
 - docs: improve documentation and code style ([`8c536f17`](https://github.com/gptme/gptme/commit/8c536f17))
 - docs: improve TTS documentation and update README ([`00198a35`](https://github.com/gptme/gptme/commit/00198a35))
 - docs: added complexity metrics with radon to arewetiny docs, as makefile target ([#428](https://github.com/gptme/gptme/issues/428)) ([`0bd43f3e`](https://github.com/gptme/gptme/commit/0bd43f3e))
 - docs: fix RST formatting in config.rst ([`2059dddf`](https://github.com/gptme/gptme/commit/2059dddf))
 - docs: document potential DeepSeek/Gemini reasoning content support ([`ab780b99`](https://github.com/gptme/gptme/commit/ab780b99))
 - style: add newline between patch success message and warnings ([`8698758b`](https://github.com/gptme/gptme/commit/8698758b))
 - style: improve readability of base_prompt assignment ([`79df878a`](https://github.com/gptme/gptme/commit/79df878a))
 - refactor: improve imports and error handling in cli.py ([`de764dac`](https://github.com/gptme/gptme/commit/de764dac))
 - docs: document environment variables and feature flags ([`693b3be0`](https://github.com/gptme/gptme/commit/693b3be0))
 - chore: added config.toml to gitignore ([`456d9661`](https://github.com/gptme/gptme/commit/456d9661))
 - chore: added config.toml to gitignore ([`87d23cfa`](https://github.com/gptme/gptme/commit/87d23cfa))
 - tests: fixed tests for splitting/chunking sentences in tts ([`af0d3588`](https://github.com/gptme/gptme/commit/af0d3588))
 - test: generate unique run name for retries ([`6ff8b94b`](https://github.com/gptme/gptme/commit/6ff8b94b))
 - test: fixed test expecting 'Cost' which isn't known for some models ([`db212bd7`](https://github.com/gptme/gptme/commit/db212bd7))
 - test: fixed missing argument in test ([`0dbcf09e`](https://github.com/gptme/gptme/commit/0dbcf09e))
 - test: disabled test_search_google since its failing, improved error logging for failed searches with playwright ([`51f54e9a`](https://github.com/gptme/gptme/commit/51f54e9a))
 - chore: updated contributor cache ([`719da49f`](https://github.com/gptme/gptme/commit/719da49f))

</p>
</details>

*(excluded 9 less relevant [commits](https://github.com/gptme/gptme/compare/v0.26.0...v0.27.0))*

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.26.0...v0.27.0
