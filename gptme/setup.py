"""Setup functionality for gptme configuration and completions."""

import os
import shutil
from pathlib import Path

from .config import get_config
from .llm import list_available_providers
from .llm.models import get_default_model


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

    # Get available providers from the LLM module
    available_providers = list_available_providers()
    available_provider_names = {provider for provider, _ in available_providers}

    # All supported providers (from the LLM module's provider_checks)
    all_providers = [
        ("OpenAI", "OPENAI_API_KEY"),
        ("Anthropic", "ANTHROPIC_API_KEY"),
        ("OpenRouter", "OPENROUTER_API_KEY"),
        ("Gemini", "GEMINI_API_KEY"),
        ("Groq", "GROQ_API_KEY"),
        ("XAI", "XAI_API_KEY"),
        ("DeepSeek", "DEEPSEEK_API_KEY"),
        ("Azure OpenAI", "AZURE_OPENAI_API_KEY"),
    ]

    # Map provider names to their internal names for checking
    provider_name_map = {
        "OpenAI": "openai",
        "Anthropic": "anthropic",
        "OpenRouter": "openrouter",
        "Gemini": "gemini",
        "Groq": "groq",
        "XAI": "xai",
        "DeepSeek": "deepseek",
        "Azure OpenAI": "openai-azure",
    }

    for display_name, _env_var in all_providers:
        internal_name = provider_name_map[display_name]
        if internal_name in available_provider_names:
            print(f"  {display_name}: ✅")
        else:
            print(f"  {display_name}: ❌")

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
        if not enabled:
            print(f"    (Set {env_var}=1 to enable)")

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
