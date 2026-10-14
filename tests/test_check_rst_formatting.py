import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_rst_formatting.py"
_SPEC = importlib.util.spec_from_file_location("check_rst_formatting", _SCRIPT)
assert _SPEC and _SPEC.loader
check_rst_formatting = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_rst_formatting)

check_grid_tables = check_rst_formatting.check_grid_tables


def test_grid_table_aligned():
    table = """
+------+------+
| a    | b    |
+======+======+
| c    | d    |
| more | more |
+------+------+
""".splitlines()
    assert check_grid_tables(table) == []


def test_grid_table_separator_on_some_lines():
    # the original bug: docutils silently reads this as a column-spanning cell
    table = """
+------+------+
| a    | b    |
+------+------+
| text | d    |
| more text| d|
+------+------+
""".splitlines()
    issues = check_grid_tables(table)
    assert [(kind, line) for kind, _, line, _ in issues] == [("grid_table", 6)]


def test_grid_table_overflowing_row():
    table = """
+------+------+
| wider than it should be|      |
+------+------+
""".splitlines()
    assert len(check_grid_tables(table)) == 1


def test_grid_table_column_span():
    table = """
+------+------+
| spans both  |
+------+------+
| c    | d    |
+------+------+
""".splitlines()
    assert check_grid_tables(table) == []


def test_grid_table_wide_chars():
    # emoji take two columns, as docutils counts them
    table = """
+------+------+
| ✅ ok| b    |
+------+------+
""".splitlines()
    assert check_grid_tables(table) == []


def test_grid_table_row_span():
    table = """
+------+------+
| a    | b    |
|      +------+
|      | c    |
+------+------+
""".splitlines()
    assert check_grid_tables(table) == []


def test_grid_table_literal_pipe_in_cell():
    table = """
+------+------+
| a|b  | c    |
+------+------+
""".splitlines()
    assert check_grid_tables(table) == []


def test_grid_table_in_code_block_skipped():
    text = """
Example of a broken table:

.. code-block:: text

   +------+------+
   | too wide here|      |
   +------+------+

And as a literal block::

   +------+------+
   | too wide here|      |
   +------+------+
""".splitlines()
    assert check_grid_tables(text) == []


def test_grid_table_in_table_directive_checked():
    text = """
.. table:: Caption

   +------+------+
   | too wide here|      |
   +------+------+
""".splitlines()
    assert len(check_grid_tables(text)) == 1


def test_docstring_grid_table_checked():
    docstring = """Summary.

    +------+------+
    | too wide here|      |
    +------+------+
    """
    issues = check_rst_formatting.check_docstring(docstring, start_line=10)
    assert [(kind, line) for kind, _, line, _ in issues] == [("grid_table", 13)]
