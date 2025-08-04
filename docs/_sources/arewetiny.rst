Are we tiny?
============

gptme is intended to be small and simple, and focus on doing the right thing in the right way, rather than all the things in all the ways.

The benefits of this approach are many:

- It is easier to understand and maintain.
- It is easier to contribute to.
- It is easier to learn.
- It is easier to extend.
- It is more fun to work on.

Being aggressive about keeping things small and simple is a way to keep the project maintainable and fun to work on. The fastest way to kill a project is to make it too big and complex, and suffer burnout as a result.

Another major benefit of keeping things small and simple is that it makes it easier for AI to understand and work with the codebase.
This is a major goal of the project, and it is important to keep in mind that the simpler the codebase is, the easier it will be for AI to work with it:

..

    *"The simpler your API is, the more effectively the AI can harness it when generating code."*

    -- `Kenneth Reitz <https://x.com/kennethreitz42/status/1852750768920518768>`_ (and many others)


To that end, in this document we will present some statistics about the current state of the project, trying to be mindful to keep an eye on this page and make sure we are not growing too much.

Startup time
------------

.. command-output:: make bench-import
   :cwd: ..

.. command-output:: make bench-startup
   :cwd: ..


Lines of code
-------------

LoC Core
********

.. command-output:: make cloc-core
   :cwd: ..

LoC LLM
*******

.. command-output:: make cloc-llm
   :cwd: ..

LoC Tools
*********

.. command-output:: make cloc-tools
   :cwd: ..

LoC Server
***********

.. command-output:: make cloc-server
   :cwd: ..

LoC Tests
**********

.. command-output:: make cloc-tests
   :cwd: ..

LoC Eval
********

.. command-output:: make cloc-eval
   :cwd: ..

LoC Total
*********

.. command-output:: make cloc-total
   :cwd: ..

Code Metrics
------------

.. command-output:: make metrics
   :cwd: ..

The metrics above show:

- **Project Overview**: Basic stats about the codebase size and complexity
- **Complex Functions**: Functions rated D+ (high complexity, should be refactored)
- **Large Files**: Files over 300 SLOC (should be split into smaller modules)
- **Duplicated Files**: Using `jscpd` to find duplicated code

We should aim to:

- Keep average complexity below 4.0
- Have no E-rated functions (extremely complex)
- Have few D-rated functions (very complex)
- Keep files under 300 SLOC where possible
