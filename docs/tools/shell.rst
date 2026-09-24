:audience: power-user

Shell
=====

.. automodule:: gptme.tools.shell
    :members:
    :noindex:

Command Confirmation
--------------------

Not every shell command requires user confirmation. gptme uses a three-tier
model:

**Allowlisted commands** are *eligible* for auto-confirmation (no prompt).
The command name is not enough on its own: arguments, flags, and shell
syntax still have to pass the safety checks below. Names considered
eligible:

.. code-block:: text

    ls  stat  cd  cat  pwd  echo  head  find  rg  ag  tail  grep
    wc  sort  uniq  cut  file  which  type  tree  du  df

Even with one of those names, a prompt is still required when the command
has:

- File redirections (``ls > files.txt``, ``echo x >> out``)
- Sensitive paths (``cat /etc/shadow``, ``ls /root``)
- Command substitution (``echo $(whoami)``, backticks)
- Unpermitted flags (``find . -delete``, ``rg --pre``, ``sort -o``)

**Transparent wrappers** are stripped before the allowlist check, so wrapping
an allowlisted command with a timing or resource-limit prefix does not
itself require a prompt. Recognised wrappers:

.. code-block:: text

    time [−p]
    timeout [options] <duration>
    nohup
    nice [−n <adj>]
    stdbuf [−i/−o/−e <mode>]
    env [−i / −u VAR]   (bare env, without NAME=value)
    command [−p]
    builtin

For example, ``timeout 60 grep -r foo /var/log`` is judged as ``grep`` and
runs without a prompt *if* ``grep`` also passes the checks above. Variables
(``PATH=. ls``) are deliberately **not** treated as transparent — they
change what the command does and are always gated.

**Denied commands** are blocked outright and never executed. These match
specific command-text patterns, not every equivalent spelling:

- ``git add .`` / ``git add -A`` / ``git commit -a`` — bulk staging
- Destructive git: ``git reset --hard``, ``git clean -f``, ``git push -f``
- ``rm -rf /`` (also ``/./``, ``/*``, quoted ``/``), ``sudo rm -rf /``, and
  ``rm -rf *``. Flag-order variants such as ``rm -fr /`` or ``rm -r -f /``
  are **not** hard-denied; they reach the confirmation prompt.
- ``chmod 777``
- ``pkill`` / ``killall``
- Piping to shell interpreters: ``... | bash``, ``... | python3``

Everything else falls through to the normal confirmation prompt.

Command Parsing
---------------

Since v0.34.0, gptme uses `tree-sitter-bash
<https://github.com/tree-sitter/tree-sitter-bash>`_ as the **primary** parser
to split multi-command scripts into individual commands for sequential
execution and to detect background operators (``&``). ``bashlex`` remains a
compatibility fallback when tree-sitter reports a parse error or a generated
split fails syntax validation — which is why some scripts can still receive
the legacy splitting behavior.

The former ``bashlex``-only parser could not handle several common constructs
(``time``, ``timeout``, process substitution ``<(…)``, arithmetic expansion
``$(( ))``) and reported them as syntax errors to the model.

The allowlist and denylist checks always operate on the **raw command text**
and are unaffected by the parser.
