:audience: power-user

Convert
=======

The ``convert`` tool reformats files offline using system converters, falling
back gracefully when a specific one is unavailable. Choose the destination
format by the ``output_path`` extension (``.png``, ``.jpg``, ``.txt``, ``.md``),
and pass ``dry_run`` to see the converter plan without executing. Image-to-image
output paths are literal filenames: ``asset%03d.jpg`` writes that exact name,
not a numbered image sequence. It has no
markdown-fence block type, so ask for it in natural language and let the model
call it with structured arguments:

.. code-block:: text

    gptme "convert report.pdf to report-page-1.png at high quality, dry run first"

When converting images to PNG or JPEG (``.png``, ``.jpg``, ``.jpeg``), animated
input produces its first frame at the exact requested output path, not a numbered
image sequence. Animation-capable GIF and WebP targets are not subject to this
first-frame selection.

Percent-bearing image source paths are literal too: ``scan%03d.png`` reads
that file, not ``scan001.png``. For FFmpeg, these inputs are copied into a
private temporary directory under a safe basename, preserving normal content
probing (including animated PNG and GIF). This uses additional temporary disk
space equal to the source size; the copy is removed after conversion.

.. automodule:: gptme.tools.convert
    :members:
    :noindex:
