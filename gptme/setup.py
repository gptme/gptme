"""Setup functionality for gptme configuration and completions."""

import os
import shutil
from pathlib import Path
from typing import get_args

from .config import config_path, get_config, set_config_value
from .llm import get_model_from_api_key, list_available_providers
from .llm.models import Provider, get_default_model
from .util import console


def setup():
    """Setup gptme with completions, configuration, and project setup."""
    print("=== gptme Setup ===\n")

    # 1. Shell completions
    _setup_completions()

    # 2. Show configuration status
    _show_config_status()

    # 3. Project setup
    _setup_project()

    # 4. Pre-commit setup
    _suggest_precommit()

    print("\n✅ Setup complete! You can now use gptme with improved configuration.")


def _detect_shell() -> str | None:
    """Detect the current shell."""
    shell = os.environ.get("SHELL", "").split("/")[-1]
    return shell if shell in ["fish", "bash", "zsh"] else None


def _setup_completions():
    """Setup shell completions."""
    print("🐚 Shell Completions")
    print("=" * 20)

    shell = _detect_shell()
    if not shell:
        print("❌ Could not detect shell type")
        return

    print(f"Detected shell: {shell}")

    if shell == "fish":
        fish_completions_dir = Path.home() / ".config" / "fish" / "completions"
        fish_completions_file = fish_completions_dir / "gptme.fish"

        # Find the gptme installation directory
        try:
            import gptme

            gptme_dir = Path(gptme.__file__).parent.parent
            source_file = gptme_dir / "scripts" / "completions" / "gptme.fish"

            if not source_file.exists():
                print(f"❌ Completions file not found at {source_file}")
                return

            # Create completions directory if it doesn't exist
            fish_completions_dir.mkdir(parents=True, exist_ok=True)

            # Copy or symlink the completions file
            if fish_completions_file.exists():
                print(
                    f"✅ Fish completions already installed at {fish_completions_file}"
                )
            else:
                try:
                    fish_completions_file.symlink_to(source_file)
                    print(f"✅ Fish completions installed at {fish_completions_file}")
                except OSError:
                    # Fallback to copy if symlink fails
                    shutil.copy2(source_file, fish_completions_file)
                    print(f"✅ Fish completions installed at {fish_completions_file}")

                print("   Restart your shell or run 'exec fish' to enable completions")

        except ImportError:
            print("❌ Could not find gptme installation directory")

    elif shell in ["bash", "zsh"]:
        print(f"⚠️  {shell} completions not yet implemented")
        print("   Fish completions are currently supported")

    print()


def _show_config_status():
    """Show current configuration status."""
    print("⚙️  Configuration Status")
    print("=" * 23)

    config = get_config()

    # Show default model
    try:
        model = get_default_model()
        if model:
            print(f"Default model: {model.full}")
            print(f"  Provider: {model.provider}")
            print(f"  Context: {model.context:,} tokens")
            print(f"  Streaming: {'✅' if model.supports_streaming else '❌'}")
            print(f"  Vision: {'✅' if model.supports_vision else '❌'}")
        else:
            print("❌ No default model configured")
    except Exception as e:
        print(f"❌ Error getting default model: {e}")

    print()

    # Show configured providers (check for API keys)
    print("API Keys Status:")

    # Get all possible providers from the literal type
    all_providers = get_args(Provider)
    available_providers = list_available_providers()
    available_provider_names = {provider for provider, _ in available_providers}

    missing_providers = []
    for provider in all_providers:
        # Generate display name
        display_name = provider.replace("-", " ").title()
        if provider == "openai-azure":
            display_name = "Azure OpenAI"
        elif provider == "xai":
            display_name = "XAI"

        if provider in available_provider_names:
            print(f"  {display_name}: ✅")
        else:
            print(f"  {display_name}: ❌")
            missing_providers.append(display_name)

    # Offer to help set up missing API keys
    if missing_providers:
        print()
        response = (
            input("Would you like to set up an API key now? (y/N): ").strip().lower()
        )
        if response in ["y", "yes"]:
            try:
                provider, api_key = ask_for_api_key()
                print(f"✅ Successfully configured {provider} API key!")
                print("   You may need to restart gptme for changes to take effect.")
            except KeyboardInterrupt:
                print("\n❌ API key setup cancelled.")
            except Exception as e:
                print(f"❌ Error setting up API key: {e}")

    print()

    # Show extra features
    print("Extra Features:")
    features = {
        "GPTME_DING": "Bell sound on completion",
        "GPTME_CONTEXT_TREE": "Context tree visualization",
        "GPTME_AUTOCOMMIT": "Automatic git commits",
    }

    for env_var, description in features.items():
        enabled = config.get_env_bool(env_var, False)
        status = "✅" if enabled else "❌"
        print(f"  {description}: {status}")

    print()
    response = (
        input("Would you like to configure extra features? (y/N): ").strip().lower()
    )
    if response in ["y", "yes"]:
        _configure_extra_features(features)

    print()


def _setup_project():
    """Setup project configuration."""
    print("📁 Project Setup")
    print("=" * 15)

    cwd = Path.cwd()
    gptme_toml = cwd / "gptme.toml"
    github_gptme_toml = cwd / ".github" / "gptme.toml"

    if gptme_toml.exists() or github_gptme_toml.exists():
        existing_file = gptme_toml if gptme_toml.exists() else github_gptme_toml
        print(f"✅ Project config already exists at {existing_file}")
        return

    print("No gptme.toml found in current directory")

    response = (
        input("Create a gptme.toml file for this project? (y/N): ").strip().lower()
    )
    if response in ["y", "yes"]:
        # Create basic gptme.toml
        config_content = """# gptme project configuration
# See https://gptme.org/docs/config.html for more options

prompt = "This is my project"

# Files to include in context by default
files = ["README.md"]

# Uncomment to enable RAG (Retrieval-Augmented Generation)
# [rag]
# enabled = true
"""

        gptme_toml.write_text(config_content)
        print(f"✅ Created {gptme_toml}")
        print("   Edit this file to customize your project's gptme configuration")

    print()


def _suggest_precommit():
    """Suggest setting up pre-commit."""
    print("🔍 Pre-commit Setup")
    print("=" * 18)

    cwd = Path.cwd()
    precommit_config = cwd / ".pre-commit-config.yaml"

    if precommit_config.exists():
        print("✅ Pre-commit configuration already exists")
        return

    # Check if this looks like a Python project
    has_python_files = any(cwd.glob("*.py")) or any(cwd.glob("**/*.py"))
    has_git = (cwd / ".git").exists()

    if not has_git:
        print("ℹ️  Not a git repository, skipping pre-commit setup")
        return

    if not has_python_files:
        print("ℹ️  No Python files detected, skipping pre-commit setup")
        return

    print("This appears to be a Python project in a git repository")
    response = (
        input("Would you like help setting up pre-commit hooks? (y/N): ")
        .strip()
        .lower()
    )

    if response in ["y", "yes"]:
        print("💡 You can ask gptme to help you set up pre-commit:")
        print("   Example: 'Set up pre-commit with ruff, mypy, and black'")
        print("   Or: 'Add pre-commit hooks for Python linting and formatting'")

    print()


def _configure_extra_features(features: dict[str, str]):
    """Configure extra features interactively."""
    print("\n🔧 Configure Extra Features")
    print("=" * 25)

    config = get_config()
    changes_made = False

    for env_var, description in features.items():
        current_enabled = config.get_env_bool(env_var, False)
        status = "enabled" if current_enabled else "disabled"

        print(f"\n{description}")
        print(f"  Currently: {status}")

        response = input(f"  Enable {description.lower()}? (y/N): ").strip().lower()

        if response in ["y", "yes"]:
            if not current_enabled:
                set_config_value(f"env.{env_var}", "1")
                print(f"  ✅ Enabled {description.lower()}")
                changes_made = True
            else:
                print("  ℹ️  Already enabled")
        else:
            if current_enabled:
                set_config_value(f"env.{env_var}", "0")
                print(f"  ❌ Disabled {description.lower()}")
                changes_made = True
            else:
                print("  ℹ️  Remains disabled")

    if changes_made:
        print(f"\n✅ Configuration saved to {config_path}")
        print("   Changes will take effect for new gptme sessions")
    else:
        print("\n ℹ️  No changes made")


def _prompt_api_key() -> tuple[str, str, str]:  # pragma: no cover
    """Prompt user for API key and validate it."""
    api_key = input("Your OpenAI, Anthropic, OpenRouter, or Gemini API key: ").strip()
    if (found_model_tuple := get_model_from_api_key(api_key)) is not None:
        return found_model_tuple
    else:
        console.print("Invalid API key format. Please try again.")
        return _prompt_api_key()


def ask_for_api_key():  # pragma: no cover
    """Interactively ask user for API key."""
    console.print("No API key set for OpenAI, Anthropic, OpenRouter, or Gemini.")
    console.print(
        """You can get one at:
 - OpenAI: https://platform.openai.com/account/api-keys
 - Anthropic: https://console.anthropic.com/settings/keys
 - OpenRouter: https://openrouter.ai/settings/keys
 - Gemini: https://aistudio.google.com/app/apikey
 """
    )
    # Save to config
    api_key, provider, env_var = _prompt_api_key()
    set_config_value(f"env.{env_var}", api_key)
    console.print(f"API key saved to config at {config_path}")
    console.print(f"Successfully set up {provider} API key.")
    return provider, api_key
