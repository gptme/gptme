"""
Perplexity search backend for the browser tool.
"""

import logging
import os
from pathlib import Path

import tomlkit

logger = logging.getLogger(__name__)

USER_PROMPT = """
# Best Practices for Prompting Web Search Models
## Be Specific and Contextual
Unlike traditional LLMs, our web search models require specificity to retrieve relevant search results. Adding just 2-3 extra words of context can dramatically improve performance.Good Example: “Explain recent advances in climate prediction models for urban planning”Poor Example: “Tell me about climate models”
## Avoid Few-Shot Prompting
While few-shot prompting works well for traditional LLMs, it confuses web search models by triggering searches for your examples rather than your actual query.Good Example: “Summarize the current research on mRNA vaccine technology”Poor Example: “Here’s an example of a good summary about vaccines: [example text]. Now summarize the current research on mRNA vaccines.”
## Think Like a Web Search User
Craft prompts with search-friendly terms that would appear on relevant web pages. Consider how experts in the field would describe the topic online.Good Example: “Compare the energy efficiency ratings of heat pumps vs. traditional HVAC systems for residential use”Poor Example: “Tell me which home heating is better”

## Provide Relevant Context
Include critical context to guide the web search toward the most relevant content, but keep prompts concise and focused.

Good Example: “Explain the impact of the 2023 EU digital markets regulations on app store competition for small developers”
Poor Example: “What are the rules for app stores?”

# Web Search Model Pitfalls to Avoid

## Overly Generic Questions
Generic prompts lead to scattered web search results and unfocused responses. Always narrow your scope.Avoid: “What’s happening in AI?”Instead: “What are the three most significant commercial applications of generative AI in healthcare in the past year?”

## Traditional LLM Techniques
Prompting strategies designed for traditional LLM often don’t work well with web search models. Adapt your approach accordingly.Avoid: “Act as an expert chef and give me a recipe for sourdough bread. Start by explaining the history of sourdough, then list ingredients, then…”Instead: “What’s a reliable sourdough bread recipe for beginners? Include ingredients and step-by-step instructions.”

## Complex Multi-Part Requests
Complex prompts with multiple unrelated questions can confuse the search component. Focus on one topic per query.Avoid: “Explain quantum computing, and also tell me about regenerative agriculture, and provide stock market predictions.”Instead: “Explain quantum computing principles that might impact cryptography in the next decade.”

## Assuming Search Intent
Don’t assume the model will search for what you intended without specific direction. Be explicit about exactly what information you need.Avoid: “Tell me about the latest developments.”Instead: “What are the latest developments in offshore wind energy technology announced in the past 6 months?”
""".strip()

SYSTEM_PROMPT = """
You are a helpful AI assistant.

Rules:
1. Provide only the final answer. It is important that you do not include any explanation on the steps below.
2. Do not show the intermediate steps information.

Steps:
1. Decide if the answer should be a brief sentence or a list of suggestions.
2. If it is a list of suggestions, first, write a brief and natural introduction based on the original query.
3. Followed by a list of suggestions, each suggestion should be split by two newlines.
""".strip()


def search_perplexity(query: str) -> str:
    """Search using Perplexity AI API."""
    try:
        # Try to import OpenAI
        try:
            from openai import OpenAI  # fmt: skip
        except ImportError:
            return (
                "Error: OpenAI package not installed. Install with: pip install openai"
            )

        # Get API key
        api_key = os.getenv("PERPLEXITY_API_KEY")
        if not api_key:
            # Try config file
            config_path = Path.home() / ".config" / "gptme" / "config.toml"
            if config_path.exists():
                with open(config_path) as f:
                    config = tomlkit.load(f)
                    api_key = config.get("env", {}).get("PERPLEXITY_API_KEY")

        if not api_key:
            return "Error: Perplexity API key not found. Set PERPLEXITY_API_KEY environment variable or add it to ~/.config/gptme/config.toml"

        # Create client and search
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.perplexity.ai",
        )

        response = client.chat.completions.create(
            model="sonar",
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": query,
                },
            ],
        )

        msg = response.choices[0].message
        if not msg.content:
            return "Error: No response from Perplexity API"

        return msg.content

    except Exception as e:
        return f"Error searching with Perplexity: {str(e)}"


def has_perplexity_key() -> bool:
    """Check if Perplexity API key is available."""
    if os.getenv("PERPLEXITY_API_KEY"):
        return True

    # Try config file
    config_path = Path.home() / ".config" / "gptme" / "config.toml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = tomlkit.load(f)
                return bool(config.get("env", {}).get("PERPLEXITY_API_KEY"))
        except Exception:
            pass

    return False
