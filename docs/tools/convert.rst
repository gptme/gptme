:audience: power-user

Convert
=======

The ``convert`` tool reformats files offline using system converters, falling
back gracefully when a specific one is unavailable. Choose the destination
format by the ``output_path`` extension (``.png``, ``.jpg``, ``.txt``, ``.md``),
and pass ``dry_run`` to see the converter plan without executing. It has no
markdown-fence block type, so ask for it in natural language and let the model
call it with structured arguments:

.. code-block:: text

    gptme "convert report.pdf to report-page-1.png at high quality, dry run first"

.. automodule:: gptme.tools.convert
    :members:
    :noindex:
