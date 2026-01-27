CLI Reference
=============

gptme provides the following commands:

.. contents:: Commands
   :depth: 1
   :local:
   :backlinks: none

This is the full CLI reference. For a more concise version, run ``gptme --help``.

.. click:: gptme.cli.main:main
   :prog: gptme
   :nested: full

.. click:: gptme.server:main
   :prog: gptme-server
   :nested: full

.. click:: gptme.eval:main
   :prog: gptme-eval
   :nested: full

.. click:: gptme.cli.util:main
   :prog: gptme-util
   :nested: full
