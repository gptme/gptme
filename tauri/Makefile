build: prebuild
	@# Set NO_STRIP=true on Linux to avoid "failed to run linuxdeploy" error
	@if [ "$$(uname)" = "Linux" ]; then \
		echo "Building on Linux, setting NO_STRIP=true..."; \
		NO_STRIP=true npm run tauri build; \
	else \
		echo "Building on non-Linux platform..."; \
		npm run tauri build; \
	fi

dev: prebuild
	npm run tauri dev

src-tauri/icons/icon.png:
	npm run tauri icon "./public/logo.png"

# In the monorepo, webui is at ../webui/ (not gptme/webui/ as in the standalone repo)
../webui/dist:
	cd ../webui && npm i && npm run build

gptme-server-build:
	@if [ ! -f bins/gptme-server-$$(rustc -Vv | grep host | cut -f2 -d' ') ]; then \
		echo "Building gptme-server sidecar..."; \
		./scripts/build-sidecar.sh; \
	else \
		echo "gptme-server sidecar already exists, skipping"; \
	fi

prebuild: ../webui/dist src-tauri/icons/icon.png
	@$(MAKE) gptme-server-build

precommit: format check

format:
	cd src-tauri && cargo fmt

check:
	cd src-tauri && cargo check && cargo clippy

test:
	cd src-tauri && cargo test
