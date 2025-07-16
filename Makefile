.PHONY: docs help check-rst install-completions

# set default shell
SHELL := $(shell which bash)

help:
	@echo "gptme Makefile commands:"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Run 'make <command>' to execute a command."

# src dirs and files
SRCDIRS = gptme tests scripts
SRCFILES_RAW = $(shell find gptme tests -name '*.py' && find scripts -name '*.py' -not -path "scripts/Kokoro-82M/*" -not -path "*/Kokoro-82M/*")

# exclude files, such as uv scripts
EXCLUDES = tests/output scripts/build_changelog.py scripts/tts_server.py scripts/tts_kokoro.py scripts/tts_chatterbox.py scripts/generate_sounds.py
SRCFILES = $(shell echo "${SRCFILES_RAW}" | tr ' ' '\n' | grep -v -f <(echo "${EXCLUDES}" | tr ' ' '\n') | tr '\n' ' ')

# radon args
RADON_ARGS = --exclude "scripts/Kokoro-82M/*" --exclude "*/Kokoro-82M/*"

build:
	poetry install

build-docker:
	docker build . -t gptme:latest -f scripts/Dockerfile
	docker build . -t gptme-server:latest -f scripts/Dockerfile.server --build-arg BASE=gptme:latest
	docker build . -t gptme-eval:latest -f scripts/Dockerfile.eval
	# docker build . -t gptme-eval:latest -f scripts/Dockerfile.eval --build-arg RUST=yes --build-arg BROWSER=yes

build-docker-computer:
	docker build . -t gptme-computer:latest -f scripts/Dockerfile.computer

build-docker-dev:
	docker build . -t gptme-dev:latest -f scripts/Dockerfile.dev

build-docker-full:
	docker build . -t gptme:latest -f scripts/Dockerfile
	docker build . -t gptme-eval:latest -f scripts/Dockerfile.eval --build-arg RUST=yes --build-arg PLAYWRIGHT=no

build-server-exe: ## Build gptme-server executable with PyInstaller
	./scripts/build_server_executable.sh

test:
	@# if SLOW is not set, pass `-m "not slow"` to skip slow tests
	poetry run pytest ${SRCDIRS} -v --log-level INFO --durations=5 \
		--cov=gptme --cov-report=xml --cov-report=term-missing --cov-report=html --junitxml=junit.xml \
		-n 16 \
		$(if $(EVAL), , -m "not eval") \
		$(if $(SLOW), --timeout 60 --retries 2 --retry-delay 5, --timeout 10 -m "not slow and not eval") \
		$(if $(PROFILE), --profile-svg)

eval:
	poetry run gptme-eval

typecheck:
	poetry run mypy ${SRCDIRS} $(if $(EXCLUDES),$(foreach EXCLUDE,$(EXCLUDES),--exclude $(EXCLUDE)))

RUFF_ARGS=${SRCDIRS} $(foreach EXCLUDE,$(EXCLUDES),--exclude $(EXCLUDE))

lint:
	@# check there is no `ToolUse("python"` in the code (should be `ToolUse("ipython"`)
	! grep -r 'ToolUse("python"' ${SRCDIRS}
	@# ruff
	poetry run ruff check ${RUFF_ARGS}
	@# pylint (always pass, just output duplicates)
	poetry run pylint --disable=all --enable=duplicate-code --exit-zero gptme/

format:
	poetry run ruff check --fix-only ${RUFF_ARGS}
	poetry run ruff format ${RUFF_ARGS}

update-models:
	wayback_url=$$(curl "https://archive.org/wayback/available?url=openai.com/api/pricing/" | jq -r '.archived_snapshots.closest.url') && \
		gptme 'update the model metadata from this page' gptme/models.py gptme/llm_openai_models.py "$${wayback_url}" --non-interactive

precommit: format lint typecheck check-rst

check-rst:
	@echo "Checking RST files for proper nested list formatting..."
	poetry run python scripts/check_rst_formatting.py docs/

check-openapi: ## Validate OpenAPI specification
	@echo "Generating OpenAPI spec..."
	@mkdir -p build
	poetry run gptme-server openapi -o build/openapi.json
	@echo "Validating OpenAPI spec..."
	poetry run openapi-spec-validator build/openapi.json
	@echo "✅ OpenAPI spec is valid!"

docs/.clean: docs/conf.py
	poetry run make -C docs clean
	touch docs/.clean

docs: docs/conf.py docs/*.rst docs/.clean check-rst
	if [ ! -e eval_results ]; then \
		if [ -e eval-results/eval_results ]; then \
			ln -s eval-results/eval_results .; \
		else \
			git fetch origin eval-results; \
			git checkout origin/eval-results -- eval_results; \
		fi \
	fi
	poetry run make -C docs html SPHINXOPTS="-W --keep-going"

docs-auto:
	make -C docs livehtml

.PHONY: site
site: site/dist/index.html site/dist/docs
	echo "gptme.org" > site/dist/CNAME

.PHONY: site/dist/index.html
site/dist/index.html: README.md site/dist/style.css site/template.html
	mkdir -p site/dist
	sed '1s/Website/GitHub/;1s|https://gptme.org/|https://github.com/gptme/gptme|' README.md | \
	cat README.md \
		| sed '0,/Website/{s/Website/GitHub/}' - \
		| sed '0,/gptme.org\/\"/{s/gptme.org\/\"/github.com\/ErikBjare\/gptme\"/}' - \
		| pandoc -s -f gfm -t html5 -o $@ --metadata title="gptme - agent in your terminal" --css style.css --template=site/template.html
	cp -r media site/dist

site/dist/style.css: site/style.css
	mkdir -p site/dist
	cp site/style.css site/dist

site/dist/docs: docs
	cp -r docs/_build/html site/dist/docs

version:
	@./scripts/bump_version.sh

./scripts/build_changelog.py:
	wget -O $@ https://raw.githubusercontent.com/ActivityWatch/activitywatch/master/scripts/build_changelog.py
	chmod +x $@

.PHONY: dist/CHANGELOG.md
dist/CHANGELOG.md: ./scripts/build_changelog.py
	VERSION=$$(git describe --tags) && \
	PREV_VERSION=$$(./scripts/get-last-version.sh $${VERSION}) && \
		./scripts/build_changelog.py --range $${PREV_VERSION}...$${VERSION} --project-title gptme --org ErikBjare --repo gptme --output $@

release: version dist/CHANGELOG.md
	@VERSION=$$(git describe --tags --abbrev=0) && \
		echo "Releasing version $${VERSION}"; \
		read -p "Press enter to continue" && \
		git push origin master $${VERSION} && \
		gh release create $${VERSION} -t $${VERSION} -F dist/CHANGELOG.md

install-completions: ## Install shell completions (Fish)
	@echo "Installing shell completions..."
	@mkdir -p ~/.config/fish/completions
	@ln -sf $(PWD)/scripts/completions/gptme.fish ~/.config/fish/completions/gptme.fish
	@echo "✅ Fish completions installed to ~/.config/fish/completions/"
	@echo "Restart your shell or run 'exec fish' to enable completions"

clean: clean-docs clean-site clean-test clean-build

clean-site:
	rm -rf site/dist

clean-docs:
	poetry run make -C docs clean

clean-test:
	echo $$HOME/.local/share/gptme/logs/*test-*-test_*
	rm -I $$HOME/.local/share/gptme/logs/*test-*-test_*/*.jsonl || true
	rm --dir $$HOME/.local/share/gptme/logs/*test-*-test_*/ || true

clean-build: ## Clean PyInstaller build artifacts
	rm -rf build/ dist/ *.spec.bak

rename-logs:
	./scripts/auto_rename_logs.py $(if $(APPLY),--no-dry-run) --limit $(or $(LIMIT),10)

cloc: cloc-core cloc-tools cloc-server cloc-tests

FILES_LLM=gptme/llm/*.py
FILES_CORE=gptme/*.py $(FILES_LLM) gptme/util/*.py gptme/tools/__init__.py gptme/tools/base.py
cloc-core:
	cloc $(FILES_CORE) --by-file

cloc-llm:
	cloc $(FILES_LLM) --by-file

cloc-tools:
	cloc gptme/tools/*.py --by-file

cloc-server:
	cloc gptme/server --by-file

cloc-tests:
	cloc tests --by-file

cloc-eval:
	cloc gptme/eval/**.py --by-file

cloc-total:
	cloc ${SRCFILES} --by-file

# Code metrics
.PHONY: metrics

metrics:
	@echo "=== Code Metrics Summary ==="
	@echo
	@echo "Project Overview:"
	@echo "  Files: $$(find ${SRCDIRS} -name '*.py' | wc -l)"
	@echo "  Total blocks: $$(poetry run radon cc ${SRCFILES} --total-average | grep "blocks" | cut -d' ' -f1 | tr -d '\n')"
	@echo "  Average complexity: $$(poetry run radon cc ${SRCFILES} --average --total-average | grep "Average complexity" | cut -d'(' -f2 | cut -d')' -f1)"
	@echo
	@echo "Most Complex Functions (D+):"
	@poetry run radon cc ${SRCFILES} --min D | grep -v "^$$" | grep -E "^[^ ]|    [FCM].*[DE]" | sed 's/^/  /'
	@echo
	@echo "Largest Files (>300 SLOC):"
	@poetry run radon raw ${SRCFILES} | awk '/^[^ ]/ {file=$$0} /SLOC:/ {if ($$2 > 300) printf "  %4d %s\n", $$2, file}' | sort -nr
	@echo
	@echo "Most Duplicated Files:"
	@npx jscpd gptme/** docs/**.{md,rst} scripts/**.{sh,py} | perl -pe 's/\e\[[0-9;]*m//g'

bench-importtime:
	time poetry run python -X importtime -m gptme --model openai --non-interactive 2>&1 | grep "import time" | cut -d'|' -f 2- | sort -n
	@#time poetry run python -X importtime -m gptme --model openrouter --non-interactive 2>&1 | grep "import time" | cut -d'|' -f 2- | sort -n
	@#time poetry run python -X importtime -m gptme --model anthropic --non-interactive 2>&1 | grep "import time" | cut -d'|' -f 2- | sort -n
