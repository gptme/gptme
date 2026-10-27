import base64
import hashlib
import html
import json
import os
import re
import tempfile
import urllib.request
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from ..logmanager import Log


def _cdn_cache_dir() -> Path:
    """Return the gptme CDN cache directory, creating it if needed."""
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    cache_dir = base / "gptme" / "cdn"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _verify_cdn_integrity(content: bytes, integrity: str, url: str) -> None:
    """Check SRI metadata on the original bytes, including cached resources."""
    if not integrity:
        return
    hashes: dict[str, list[str]] = {"sha256": [], "sha384": [], "sha512": []}
    for token in integrity.split():
        algorithm, _, digest = token.partition("-")
        if algorithm in hashes:
            hashes[algorithm].append(digest)
    # SRI uses only the strongest algorithm present, not any weaker match.
    for algorithm in ("sha512", "sha384", "sha256"):
        if hashes[algorithm]:
            actual = base64.b64encode(hashlib.new(algorithm, content).digest()).decode()
            if actual in hashes[algorithm]:
                return
            break
    raise RuntimeError(f"CDN resource integrity check failed: {url}")


def _fetch_cdn_resource(url: str, integrity: str = "") -> str:
    """Fetch and verify a CDN resource, using a best-effort atomic disk cache."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    suffix = Path(url.split("?")[0].split("/")[-1]).suffix or ".js"
    cache_path = None
    try:
        cache_path = _cdn_cache_dir() / f"{url_hash}{suffix}"
        cached = cache_path.read_bytes()
        _verify_cdn_integrity(cached, integrity, url)
    except (OSError, RuntimeError):
        pass  # Missing, inaccessible or invalid cache entries need a fresh download.
    else:
        return cached.decode("utf-8")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gptme/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
    except Exception as e:
        raise RuntimeError(
            f"Failed to fetch CDN resource for offline embedding.\n"
            f"  URL: {url}\n"
            f"  Error: {e}\n"
            "HTML export requires network access to bundle external dependencies.\n"
            "Subsequent exports will use the cached copy."
        ) from e

    _verify_cdn_integrity(content, integrity, url)
    decoded = content.decode("utf-8")
    if cache_path is not None:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=cache_path.parent, delete=False) as f:
                temp_path = Path(f.name)
                f.write(content)
            temp_path.replace(cache_path)
        except OSError:
            pass  # Caching is optional; the verified download is still usable.
        finally:
            if temp_path is not None:
                with suppress(OSError):
                    temp_path.unlink(missing_ok=True)
    return decoded


def _embed_cdn_resources(html_content: str) -> str:
    """Replace external CDN script/stylesheet links with inline content.

    Makes the exported HTML self-contained so it renders without network access.
    Fetched resources are cached to ~/.cache/gptme/cdn/ to avoid re-downloading.
    """

    def inline_resource(m: re.Match) -> str:
        integrity = re.search(r'\bintegrity="([^"]*)"', m.group(0))
        url = m.group(1) or m.group(2)
        content = _fetch_cdn_resource(url, integrity.group(1) if integrity else "")
        if m.group(1):
            # HTML closes raw-text elements even inside JavaScript string literals.
            content = re.sub(r"</(?=script\b)", r"<\\/", content, flags=re.IGNORECASE)
            return f"<script>{content}</script>"
        # CSS escapes, unlike HTML entities, retain the literal in CSS strings.
        content = re.sub(r"<(?=/style\b)", r"\\3c ", content, flags=re.IGNORECASE)
        return f"<style>{content}</style>"

    # One pass over the template: never scan downloaded source as HTML markup.
    return re.sub(
        r'<script\s+src="(https://[^"]+)"[^>]*></script>'
        r'|<link\s+rel="stylesheet"\s+href="(https://[^"]+)"[^>]*>',
        inline_resource,
        html_content,
    )


def replace_or_fail(content: str, old: str, new: str, desc: str = "") -> str:
    """Replace a string and fail if nothing was replaced"""
    result = content.replace(old, new)
    if result == content:
        raise ValueError(f"Failed to replace {desc or old!r}")
    return result


def export_chat_to_html(name: str, chat_data: Log, output_path: Path) -> None:
    """Export a chat log to a self-contained HTML file"""

    # Read the template files
    template_dir = Path(__file__).parent / "export_templates"

    with open(template_dir / "index.html", encoding="utf-8") as f:
        html_template = f.read()
    with open(template_dir / "style.css", encoding="utf-8") as f:
        css = f.read()
    with open(template_dir / "main.js", encoding="utf-8") as f:
        js = f.read()

    # Only trusted template tags may cause downloads, never conversation data.
    html_template = _embed_cdn_resources(html_template)

    # No need to modify JavaScript since it now handles embedded data

    # Prepare the chat data, escaping any HTML in the content
    chat_data_list = []
    for msg in chat_data.messages:
        msg_dict = msg.to_dict()
        # Escape HTML in the content, but preserve newlines
        msg_dict["content"] = html.escape(msg_dict["content"], quote=False)
        chat_data_list.append(msg_dict)

    chat_data_json = json.dumps(chat_data_list, indent=2)
    chat_name_json = json.dumps(name).replace("<", "\\u003c")

    # Embed script and conversation
    standalone_html = replace_or_fail(
        html_template,
        '<script type="module" src="/static/main.js"></script>',
        f"""
<script>
window.CHAT_NAME = {chat_name_json};
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
        f"<title>{html.escape(name)} - gptme</title>",
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


def _format_timestamp(timestamp: datetime | None) -> str:
    """Format a message timestamp for display."""
    if timestamp is None:
        return ""
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")


def _role_label(role: str) -> str:
    """Map role to a readable label."""
    labels = {"user": "User", "assistant": "Assistant", "system": "System"}
    return labels.get(role, role.capitalize())


def export_chat_to_markdown(name: str, chat_data: Log, output_path: Path) -> None:
    """Export a chat log to a markdown file."""
    lines: list[str] = []
    lines.append(f"# {name}")

    for msg in chat_data.messages:
        # Skip hidden messages (markdown is static so hidden messages are excluded)
        if msg.hide:
            continue

        role = _role_label(msg.role)
        ts = _format_timestamp(msg.timestamp)
        header = f"## {role}"
        if ts:
            header += f"  ({ts})"
        lines.append(header)
        lines.append("")
        lines.append(msg.content)
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
