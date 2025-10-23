# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html
# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

import re
from datetime import date

from docutils import nodes
from docutils.parsers.rst import Directive

year = date.today().year
project = "gptme"
copyright = f"{year}, Erik Bjäreholt"
author = "Erik Bjäreholt"


class ChatDirective(Directive):
    required_arguments = 0
    optional_arguments = 0
    has_content = True

    def run(self):
        self.assert_has_content()
        # parse "User:", "Assistant:" and "System:" lines as chat messages
        # msgs = [
        #     {"role": line.split(":"), "content": line.split(":", 1)[1].strip()}
        #     for line in self.content
        #     if any(line.startswith(role) for role in ["User", "Assistant", "System"])
        # ]
        msgs = []
        for line in self.content:
            if any(
                line.lstrip().startswith(role)
                for role in ["User", "Assistant", "System"]
            ):
                role, content = line.split(":", 1)
                msgs.append({"role": role.strip(), "content": content.strip()})
            else:
                if msgs:
                    msgs[-1]["content"] += f"\n{line}"
                else:
                    raise Exception(f"no start of message found for line: {line}")

        for msg in msgs:
            if msg["role"] == "User":
                msg["role_style"] = "color: #5555cc;"
            elif msg["role"] == "Assistant":
                msg["role_style"] = "color: #44cc44"
            elif msg["role"] == "System":
                msg["role_style"] = "color: #AAAAAA"

            # if contains codeblocks, we want to put them into their own scrolling <pre> block
            msg["content"] = re.sub(
                r"\n```([^\n]*?)\n(.*?)\n\s*```",
                r'<div style="opacity: 0.8;"><div style="display: inline-block; margin-bottom: -.5px; margin-top: 1em; border: 0 #888 solid; border-width: 1px 1px 0 1px; border-radius: 3px 3px 0 0; padding: 0.3em 0.6em; font-size: 0.7em; background-color: #000; color: #FFF">\1</div>\n<pre style="background-color: #000; color: #ccc; margin-right: 1em; margin-top: 0; padding: 5px; margin-bottom: 0.5em; overflow: scroll;">\2\n</pre></div>',
                msg["content"],
                flags=re.DOTALL,
            ).rstrip()

        # set up table
        src = f'''
<table style="width: 100%; margin-bottom: 1em">
  {"".join(f"""
<tr>
  <td style="text-align: right; padding: 0 1em 0 1em; width: 0.1%; font-weight: bold; {msg["role_style"]}">{msg["role"]}</td>
  <td>
    <pre style="margin-right: 1em; padding: 5px; margin-bottom: 0.5em; white-space: pre-wrap;">{msg["content"]}</pre>
  </td>
</tr>""" for msg in msgs)}
</table>
'''.strip()

        return [nodes.raw("", src, format="html")]


def setup(app):
    app.add_directive("chat", ChatDirective)


# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "myst_parser",
    "sphinx.ext.napoleon",
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.extlinks",
    "sphinx.ext.autosectionlabel",
    "sphinxcontrib.mermaid",
    "sphinx_click",
    "sphinxcontrib.programoutput",
    "sphinxcontrib.asciinema",
    "sphinx_sitemap",
    "sphinx_llms_txt",
]

# Intersphinx mapping for cross-references to Python standard library
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

html_baseurl = "https://gptme.org/docs/"  # used to build sitemap
sitemap_url_scheme = "{link}"
sitemap_excludes = [
    "search.html",
    "genindex.html",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "lessons/**"]

extlinks = {
    "issue": ("https://github.com/gptme/gptme/issues/%s", "issue #"),
}

# Prefix each section label with the name of the document it is in, followed by a colon.
# For example, index:Introduction for a section called Introduction that appears in document index.rst.
# Useful for avoiding ambiguity when the same section heading appears in different documents.
autosectionlabel_prefix_document = True

# Only autolabel sections up to a certain depth to avoid conflicts with generated CLI docs
autosectionlabel_maxdepth = 2

autodoc_typehints_format = "short"
autodoc_class_signature = "separated"
napoleon_attr_annotations = False

nitpicky = True
nitpick_ignore = [
    ("py:class", "collections.abc.Generator"),
    ("py:class", "collections.abc.Callable"),
    ("py:class", "pathlib.Path"),
    ("py:class", "pathlib._local.Path"),
    ("py:class", "flask.app.Flask"),
    ("py:class", "gptme.tools.python.T"),
    ("py:class", "threading.Thread"),
    ("py:class", "gptme.tools.computer.ScalingSource"),
    ("py:class", "gptme.config.RagConfig"),
    ("py:class", "ToolFormat"),
    ("py:class", "ConfirmFunc"),
    ("py:class", "Path"),
    ("py:class", "gptme.tools.subagent.SubtaskDef"),
]

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "sphinx_book_theme"
html_static_path = ["_static"]
html_css_files = ["custom.css"]

html_title = "gptme"
html_logo = "../media/logo.png"
html_favicon = "../media/logo.png"

html_theme_options = {
    "repository_url": "https://github.com/gptme/gptme",
    "path_to_docs": "docs",
    "use_repository_button": True,
    "use_edit_page_button": True,
    # "extra_navbar": """
    # <p>
    #     Back to <a href="https://github.com/gptme/gptme">GitHub</a>
    # </p>""",
}

show_navbar_depth = 2
