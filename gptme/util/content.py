def extract_content_summary(content: str, max_words: int = 100) -> str:
    """
    Extract a summary from message content.

    Filters out tool usage blocks, XML tags, and technical artifacts, focusing on the main content.
    """
    import re

    # Remove code blocks with various backtick patterns (handles escaped and malformed ones)
    content = re.sub(r"`{3,4}[\s\S]*?`{3,4}", "", content)
    content = re.sub(r"` ` `[\s\S]*?` ` `", "", content)  # Handle spaced backticks

    # Remove inline code and single backticks that might be artifacts
    content = re.sub(r"`[^`\n]*`", "", content)

    # Remove all XML-like tags (thinking, think, and any others)
    content = re.sub(r"<[^>]*>[\s\S]*?</[^>]*>", "", content)

    # Remove standalone XML tags
    content = re.sub(r"<[^>]*>", "", content)

    # Remove shell command patterns that might be artifacts
    content = re.sub(r"\$\([^)]*\)", "", content)
    content = re.sub(r'EOF["\']?\)', "", content)

    # Remove interrupted patterns from the end
    content = re.sub(
        r"\.\.\.?\s*\^\s*C\s+Interrupted\s*$", "", content, flags=re.IGNORECASE
    )
    content = re.sub(
        r"\s*(interrupted|aborted|cancelled)\s*$", "", content, flags=re.IGNORECASE
    )

    # Clean up extra whitespace and newlines
    content = re.sub(r"\s+", " ", content)

    # Remove common technical artifacts
    content = re.sub(r"\{[^}]*\}", "", content)  # Remove template-like content
    content = re.sub(r"[\[\](){}]", " ", content)  # Remove brackets and parens

    # Remove **bold** and __underline__ markers
    content = re.sub(r"(\*\*|__)(.*?)\1", r"\2", content)

    # Clean up and truncate
    words = content.strip().split()
    if len(words) > max_words:
        words = words[:max_words]
        return " ".join(words) + "..."

    return " ".join(words).strip()
