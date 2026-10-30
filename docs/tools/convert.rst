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
call it with structured arguments.

Conversions stage their single output beside the destination and replace the
requested file only after success. A failed conversion leaves an existing file
unchanged and does not publish a partial new file. Existing regular-file symlinks
are followed without replacing the link; the requested extension still selects
the format. Existing permission bits are retained, but replacement creates a new
inode (other hardlinks keep the old contents); ownership, ACLs and extended
attributes are not preserved. The destination directory must be writable even
when the old output file itself is writable. This is not crash durability or
protection against concurrent pathname changes. A cleanup problem after
successful publication is reported as a warning, not as failed conversion.
Custom ``Converter`` subclasses can implement ``_convert`` to use this staging
boundary. Existing subclasses that override ``convert`` remain supported and
retain their own publication behavior; overriding ``convert`` bypasses staging.

For example:

.. code-block:: text

    gptme "convert report.pdf to report-page-1.png at high quality, dry run first"

When converting images to PNG or JPEG (``.png``, ``.jpg``, ``.jpeg``), animated
input produces its first frame at the exact requested output path, not a numbered
image sequence. Animation-capable GIF and WebP targets are not subject to this
first-frame selection.

TIFF-to-TIFF conversion preserves document pages in their original order using
ImageMagick. If a TIFF has multiple pages and ImageMagick is unavailable, the
conversion fails explicitly without writing the destination; FFmpeg alone cannot
preserve these pages. Single-page TIFF conversions can still use FFmpeg.

Image and video sources passed to FFmpeg are local filesystem paths, not
protocol URLs. A relative filename such as ``file:photo.png`` selects that
literal file, not a different file named ``photo.png``.

.. automodule:: gptme.tools.convert
    :members:
    :noindex:
