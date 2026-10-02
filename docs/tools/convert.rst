:audience: power-user

Convert
=======

The ``convert`` tool reformats files offline using system converters, falling
back gracefully when a specific one is unavailable. Choose the destination
format by the ``output_path`` extension (``.png``, ``.jpg``, ``.txt``, ``.md``),
and pass ``dry_run`` to see the converter plan without executing:

.. code-block:: text

    convert
    input_path: report.pdf
    output_path: report-page-1.png
    quality: high
    dry_run: true

.. automodule:: gptme.tools.convert
    :members:
    :noindex:
