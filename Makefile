.PHONY: test docs build build-docker check-rst install-completions help

# set default shell
SHELL := $(shell which bash)

# src dirs and files
SRCDIRS = gptme tests scripts
SRCFILES_RAW = $(shell find gptme tests -name '*.py' && find scripts -name '*.py' -not -path "scripts/Kokoro-82M/*" -not -path "*/Kokoro-82M/*")

# exclude files, such as uv scripts
EXCLUDES = tests/output scripts/build_changelog.py scripts/tts_server.py scripts/tts_kokoro.py scripts/tts_chatterbox.py scripts/generate_sounds.py
SRCFILES = $(shell echo "${SRCFILES_RAW}" | tr ' ' '\n' | grep -v -f <(echo "${EXCLUDES}" | tr ' ' '\n') | tr '\n' ' ')

# radon args
RADON_ARGS = --exclude "scripts/Kokoro-82M/*" --exclude "*/Kokoro-82M/*"

build: ## Build the project (install dependencies)
	poetry install

build-docker: ## Build Docker images
	docker build . -t gptme:latest -f scripts/Dockerfile
	docker build . -t gptme-server:latest -f scripts/Dockerfile.server --build-arg BASE=gptme:latest
	docker build . -t gptme-eval:latest -f scripts/Dockerfile.eval
	# docker build . -t gptme-eval:latest -f scripts/Dockerfile.eval --build-arg RUST=yes --build-arg BROWSER=yes

build-docker-computer: ## Build Docker image for gptme-computer
	docker build . -t gptme-computer:latest -f scripts/Dockerfile.computer

build-docker-dev: ## Build Docker image for development
	docker build . -t gptme-dev:latest -f scripts/Dockerfile.dev

build-docker-full: ## Build full Docker images with Rust and Playwright
	docker build . -t gptme:latest -f scripts/Dockerfile
	docker build . -t gptme-eval:latest -f scripts/Dockerfile.eval --build-arg RUST=yes --build-arg PLAYWRIGHT=no

build-server-exe: ## Build gptme-server executable with PyInstaller
	./scripts/build_server_executable.sh

test: ## Run tests
	@# if SLOW is not set, pass `-m "not slow"` to skip slow tests
	poetry run pytest ${SRCDIRS} -v --log-level INFO --durations=5 \
		--cov=gptme --cov-report=xml --cov-report=term-missing --cov-report=html --junitxml=junit.xml \
		-n 16 \
		$(if $(EVAL), , -m "not eval") \
		$(if $(SLOW), --timeout 60 --retries 2 --retry-delay 5, --timeout 10 -m "not slow and not eval") \
		$(if $(PROFILE), --profile-svg)

eval: ## Run evaluation suite
	poetry run gptme-eval

typecheck: ## Run mypy type checking
	poetry run mypy ${SRCDIRS} $(if $(EXCLUDES),$(foreach EXCLUDE,$(EXCLUDES),--exclude $(EXCLUDE)))

RUFF_ARGS=${SRCDIRS} $(foreach EXCLUDE,$(EXCLUDES),--exclude $(EXCLUDE))

pre-commit:  ## Run pre-commit hooks
	poetry run pre-commit run --all-files

lint: ## Run linters
	@# check there is no `ToolUse("python"` in the code (should be `ToolUse("ipython"`)
	! grep -r 'ToolUse("python"' ${SRCDIRS}
	@# check that there are nu uses of tmp_path fixture (see https://github.com/gptme/gptme/issues/709)
	@#! grep -r 'def.*tmp_path[:,)]' ${SRCDIRS} tests/
	@# ruff
	poetry run ruff check ${RUFF_ARGS}
	@# pylint (always pass, just output duplicates)
	poetry run pylint --disable=all --enable=duplicate-code --exit-zero gptme/

format: ## Format code
	poetry run ruff check --fix-only ${RUFF_ARGS}
	poetry run ruff format ${RUFF_ARGS}

update-models:
	wayback_url=$$(curl "https://archive.org/wayback/available?url=openai.com/api/pricing/" | jq -r '.archived_snapshots.closest.url') && \
		gptme 'update the model metadata from this page' gptme/models.py gptme/llm_openai_models.py "$${wayback_url}" --non-interactive

precommit: format lint typecheck check-rst  ## Run all pre-commit checks

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

version:  ## Bump version using ./scripts/bump_version.sh
	@./scripts/bump_version.sh

./scripts/build_changelog.py:
	wget -O $@ https://raw.githubusercontent.com/ActivityWatch/activitywatch/master/scripts/build_changelog.py
	chmod +x $@

.PHONY: dist/CHANGELOG.md
dist/CHANGELOG.md: ./scripts/build_changelog.py
	@# Use clean version if on tagged commit, otherwise use descriptive version
	@POETRY_VERSION=v$$(poetry version --short) && \
	GIT_VERSION=$$(git describe --tags) && \
	if [ "$$POETRY_VERSION" = "$$GIT_VERSION" ]; then \
		VERSION=$$POETRY_VERSION; \
	else \
		VERSION=$$GIT_VERSION; \
	fi && \
	make docs/releases/$${VERSION}.md && \
	cp docs/releases/$${VERSION}.md $@

docs/releases/%.md: ./scripts/build_changelog.py
	@mkdir -p docs/changelog
	# version is the % in the target
	VERSION=$* && \
	PREV_VERSION=$$(./scripts/get-last-version.sh $${VERSION}) && \
		./scripts/build_changelog.py --range $${PREV_VERSION}...$${VERSION} --project-title gptme --org gptme --repo gptme --output $@ --add-version-header

release: version dist/CHANGELOG.md  ## Release new version
	# Insert new version at top of changelog toctree
	# Stage changelog and release notes with version bump
	# Amend version commit to include changelog
	# Force-update tag to amended commit
	@VERSION=v$$(poetry version --short) && \
		echo "Releasing version $${VERSION}"; \
		grep $${VERSION} docs/changelog.rst || (awk '/^   releases\// && !done { \
			print "   releases/'"$${VERSION}"'.md"; \
			done=1; \
		} \
		{print}' docs/changelog.rst > docs/changelog.rst.tmp && \
		mv docs/changelog.rst.tmp docs/changelog.rst) && \
		git add docs/changelog.rst docs/releases/$${VERSION}.md && \
		git commit --amend --no-edit && \
		git tag -f $${VERSION} && \
		echo "✓ Updated commit and tag with changelog" && \
		read -p "Press enter to push" && \
		git push origin master && \
		git push origin $${VERSION} --force && \
		gh release create $${VERSION} -t $${VERSION} -F dist/CHANGELOG.md

install-completions: ## Install shell completions (Fish)
	@echo "Installing shell completions..."
	@mkdir -p ~/.config/fish/completions
	@ln -sf $(PWD)/scripts/completions/gptme.fish ~/.config/fish/completions/gptme.fish
	@echo "✅ Fish completions installed to ~/.config/fish/completions/"
	@echo "Restart your shell or run 'exec fish' to enable completions"

clean: clean-docs clean-site clean-test clean-build  ## Clean all build artifacts
	@echo "Cleaned all build artifacts."

clean-site:  ## Clean generated site files
	rm -rf site/dist

clean-docs:  ## Clean generated documentation
	poetry run make -C docs clean

clean-test:  ## Clean test logs and outputs
	echo $$HOME/.local/share/gptme/logs/*test-*-test_*
	rm -I $$HOME/.local/share/gptme/logs/*test-*-test_*/*.jsonl || true
	rm --dir $$HOME/.local/share/gptme/logs/*test-*-test_*/ || true

clean-build: ## Clean PyInstaller build artifacts
	rm -rf build/ dist/ *.spec.bak

rename-logs:
	./scripts/auto_rename_logs.py $(if $(APPLY),--no-dry-run) --limit $(or $(LIMIT),10)

cloc: cloc-core cloc-tools cloc-server cloc-tests  ## Run cloc to count lines of code

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

metrics:  ## Generate code metrics report
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
	@make metrics-duplicates

metrics-duplicates:  ## Find duplicated code using jscpd
	@echo "Most Duplicated Files:"
	@npx jscpd gptme/** docs/**.{md,rst} scripts/**.{sh,py} | perl -pe 's/\e\[[0-9;]*m//g'

bench-import:  ## Benchmark import time
	@echo "Benchmarking import time for gptme"
	time poetry run python -X importtime -m gptme --model openai --non-interactive 2>&1 | grep "import time" | cut -d'|' -f 2- | sort -n | tail -n 10
	@#time poetry run python -X importtime -m gptme --model openrouter --non-interactive 2>&1 | grep "import time" | cut -d'|' -f 2- | sort -n
	@#time poetry run python -X importtime -m gptme --model anthropic --non-interactive 2>&1 | grep "import time" | cut -d'|' -f 2- | sort -n

bench-startup:  ## Benchmark startup time
	@echo "Benchmarking startup time for gptme"
	hyperfine "poetry run gptme '/exit'" -M 5 || poetry run gptme '/exit' || exit 1

help:  ## Show this help message
	@echo $(MAKEFILE_LIST)
	@echo "gptme Makefile commands:"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Run 'make <command>' to execute a command."
