Demos
=====

.. note::

   This page is a work in progress, and will be updated with more demos soon.

.. contents:: Table of Contents
   :depth: 1
   :local:
   :backlinks: none


.. rubric:: Snake with curses

Generate a snake game that runs in the terminal using curses, and then modify it to add color.

.. asciinema:: 621992
   :autoplay: true
   :idle-time-limit: 1

Steps

#. Create a snake game with curses to snake.py
#. Running fails, ask gptme to fix a bug
#. Game runs
#. Ask gptme to add color
#. Minor struggles
#. Finished game with green snake and red apple pie!

.. rubric:: Mandelbrot with curses

Generate a program that renders mandelbrot with curses, and then modify it to add color.

.. asciinema:: 621991
   :autoplay: true
   :idle-time-limit: 1

Steps

#. Render mandelbrot with curses to mandelbrot_curses.py
#. Program runs
#. Add color


.. rubric:: Fibonacci

An old demo showing off basic code execution and shell interaction.

.. asciinema:: 606375
   :autoplay: true
   :idle-time-limit: 1

Steps

#. Create a new dir 'gptme-test-fib' and git init
#. Write a fib function to fib.py, commit
#. Create a public repo and push to GitHub


.. rubric:: Answer question from URL

Showing off basic URL loading from the prompt, and answering questions based on the content.

.. asciinema:: 621997
   :autoplay: true
   :idle-time-limit: 1

Steps

#. Ask who the CEO of Superuser Labs is, passing website URL
#. gptme browses the website, and answers correctly


.. rubric:: Edit history with /edit

The ``/edit`` command allows you to directly edit the conversation history in your text editor. This is useful for:

- Fixing typos or mistakes in previous prompts
- Removing unwanted messages
- Restructuring conversation flow
- Correcting errors before they cascade

**How it works:**

#. The conversation is converted to TOML format
#. Your default editor (``$EDITOR``) opens the TOML file
#. Edit the conversation as needed (add, remove, or modify messages)
#. Save and close the editor
#. gptme validates and applies your changes
#. If there are parsing errors, you'll get a chance to fix them

**Example use cases:**

**Fixing a typo in a prompt:**
   If you made a typo that confused the assistant, use ``/edit`` to correct it. The assistant will see the corrected version.

**Removing a mistake:**
   If the assistant misunderstood and went down the wrong path, use ``/edit`` to remove the problematic messages and restart from a good point.

**Restructuring conversation:**
   You can reorder messages, combine prompts, or split long conversations into cleaner structure.

**Tips:**

- The TOML format is human-readable and easy to edit
- Each message has a ``role`` (user/assistant/system) and ``content``
- Be careful with TOML syntax - gptme will validate before applying
- Use ``/undo`` instead if you just want to undo the last message
- Press ``Ctrl+C`` in the editor to cancel without making changes
