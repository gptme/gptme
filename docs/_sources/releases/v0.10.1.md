# v0.10.1

These are the release notes for gptme version v0.10.1.

## Contributors

Thanks to everyone who contributed to this release:

@erikbjare

## Changelog

Changes since v0.10.0:


## 📦 gptme

### 🐛 Fixes (6)

 - fix: added some comments, and strip.sh ([`d2f65d21`](https://github.com/gptme/gptme/commit/d2f65d21))
 - fix: added pragma no cover to gptme-server entrypoint ([`c8fbb1e7`](https://github.com/gptme/gptme/commit/c8fbb1e7))
 - fix: major improvements to reduce tool: added tests, added truncate codeblocks, disabled summarize in reduce_log ([`bb5f9dab`](https://github.com/gptme/gptme/commit/bb5f9dab))
 - fix: fixed don't show 'test-server-...' in convo picker ([`656e8d38`](https://github.com/gptme/gptme/commit/656e8d38))
 - fix: fixed error when /dev/tty cannot be opened (such as in CI) ([`aaf5d9eb`](https://github.com/gptme/gptme/commit/aaf5d9eb))
 - fix(tools): set GIT_PAGER=cat on shell init ([`a9257984`](https://github.com/gptme/gptme/commit/a9257984))

### 🔨 Misc (18)
<details><summary>Click to expand</summary>
<p>

 - test: fixed coverage for playwright tests ([`38183d1b`](https://github.com/gptme/gptme/commit/38183d1b))
 - test: added test for /rename ([`12d922f4`](https://github.com/gptme/gptme/commit/12d922f4))
 - test: set `--log-level INFO` in `make test`, add logging to browser tool ([`c24b18d6`](https://github.com/gptme/gptme/commit/c24b18d6))
 - test: added tests and fixes to browser tool ([`e94f7f64`](https://github.com/gptme/gptme/commit/e94f7f64))
 - test: added pragma nocover to interactive lines, added cli test for context via stdin and --version ([`2ee73af0`](https://github.com/gptme/gptme/commit/2ee73af0))
 - test: added test for patch ([`328fa16d`](https://github.com/gptme/gptme/commit/328fa16d))
 - test: improved testing for utils, removed unused functions ([`b7b57b89`](https://github.com/gptme/gptme/commit/b7b57b89))
 - test: improved test_fileblock and fixed --no-confirm for overwrite ([`cbc0ffec`](https://github.com/gptme/gptme/commit/cbc0ffec))
 - test: mark slow tests as slow, print 5 slowest tests on `make test` ([`6d580af3`](https://github.com/gptme/gptme/commit/6d580af3))
 - test: added test fileblock ([`df9c6a17`](https://github.com/gptme/gptme/commit/df9c6a17))
 - test: improved testing for server ([`e66d05b9`](https://github.com/gptme/gptme/commit/e66d05b9))
 - test: improved command testing, fixed prompt parsing when passed command with path ([`ef6b472f`](https://github.com/gptme/gptme/commit/ef6b472f))
 - test: refactored commands and improved testing ([`50c028db`](https://github.com/gptme/gptme/commit/50c028db))
 - refactor: refactored prompts and get_codeblock ([`d0a22453`](https://github.com/gptme/gptme/commit/d0a22453))
 - docs: more wip stuff on finetuning doc ([`32df0155`](https://github.com/gptme/gptme/commit/32df0155))
 - test: fix blinking test ([`dad5a255`](https://github.com/gptme/gptme/commit/dad5a255))
 - docs: added logo to README ([`6d3aa92d`](https://github.com/gptme/gptme/commit/6d3aa92d))
 - chore: added logo ([`4879d652`](https://github.com/gptme/gptme/commit/4879d652))

</p>
</details>

**Full Changelog**: https://github.com/gptme/gptme/compare/v0.10.0...v0.10.1
