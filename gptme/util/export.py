import html
import json
from pathlib import Path

from ..logmanager import Log


def replace_or_fail(html: str, old: str, new: str, desc: str = "") -> str:
    """Replace a string and fail if nothing was replaced"""
    result = html.replace(old, new)
    if result == html:
        raise ValueError(f"Failed to replace {desc or old!r}")
    return result


def export_chat_to_html(name: str, chat_data: Log, output_path: Path) -> None:
    """Export a chat log to a self-contained HTML file"""

    # Read the template files
    current_dir = Path(__file__).parent
    template_dir = current_dir.parent / "server" / "static"

    with open(template_dir / "index.html", encoding="utf-8") as f:
        html_template = f.read()
    with open(template_dir / "style.css", encoding="utf-8") as f:
        css = f.read()
    with open(template_dir / "main.js", encoding="utf-8") as f:
        js = f.read()

    # No need to modify JavaScript since it now handles embedded data

    # Prepare the chat data, escaping any HTML in the content
    chat_data_list = []
    for msg in chat_data.messages:
        msg_dict = msg.to_dict()
        # Escape HTML in the content, but preserve newlines
        msg_dict["content"] = html.escape(msg_dict["content"], quote=False)
        chat_data_list.append(msg_dict)

    chat_data_json = json.dumps(chat_data_list, indent=2)

    # Embed script and conversation
    standalone_html = replace_or_fail(
        html_template,
        '<script type="module" src="/static/main.js"></script>',
        f"""
<script>
window.CHAT_NAME = {json.dumps(name)};
window.CHAT_DATA = {chat_data_json};
</script>
<script>
    window.addEventListener('load', function() {{
        {js}
        // Remove hidden class after Vue is mounted
        document.getElementById('app').classList.remove('hidden');
    }});
</script>
        """,
        "main.js script tag",
    )

    # Set the title
    standalone_html = replace_or_fail(
        standalone_html,
        "<title>gptme</title>",
        f"<title>{name} - gptme</title>",
        "title",
    )

    # Remove external resources
    standalone_html = replace_or_fail(
        standalone_html,
        '<link rel="icon" type="image/png" href="/favicon.png">',
        "",
        "favicon link",
    )
    standalone_html = replace_or_fail(
        standalone_html,
        '<link rel="stylesheet" href="/static/style.css">',
        f"<style>{css}</style>",
        "style.css link",
    )

    # Remove interactive elements
    standalone_html = replace_or_fail(
        standalone_html,
        '<div class="chat-input',
        '<div style="display: none;" class="chat-input',
        "chat input",
    )
    standalone_html = replace_or_fail(
        standalone_html,
        '<button\n                class="bg-green-500',
        '<button style="display: none;" class="bg-green-500',
        "generate button",
    )

    # Remove the loader since it's not needed in exported version
    standalone_html = replace_or_fail(
        standalone_html,
        "<!-- Loader -->",
        "<!-- Loader removed in export -->",
        "loader comment",
    )
    standalone_html = replace_or_fail(
        standalone_html,
        '<div id="loader"',
        '<div id="loader" style="display: none;"',
        "loader div",
    )

    # Remove the sidebar since we don't need conversation selection in export
    standalone_html = replace_or_fail(
        standalone_html,
        "<!-- Sidebar -->",
        "<!-- Sidebar removed in export -->",
        "sidebar comment",
    )
    standalone_html = replace_or_fail(
        standalone_html,
        '<div class="sidebar',
        '<div style="display: none;" class="sidebar',
        "sidebar div",
    )

    # Write the file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(standalone_html)
