:audience: power-user

Shell
=====

.. automodule:: gptme.tools.shell
    :members:
    :noindex:

Shell State and Background Jobs
-------------------------------

Foreground calls share a persistent Bash session: directory changes, exported
variables and shell-local variables remain available to later foreground calls.
There are three distinct ways for work to continue in the background:

- **Explicit background calls** (the structured ``background: true`` parameter)
  start a separate Bash process. It inherits the Python parent's current working
  directory and environment, not the persistent foreground shell's state.
  Foreground ``cd`` and ``export`` changes are therefore not a reliable setup
  step for these jobs; shell-local variables and functions do not transfer.
  The Python process's directory can differ between CLI and workspace/server
  sessions, so specify the directory inside every explicit background call.
- **Automatic foreground promotion** leaves the already-running command in its
  existing shell when it exceeds the soft timeout. It does not relaunch that
  command using the explicit-background inheritance rules.
- **Bash job control** (``command &``) launches from the current shell and inherits
  its state at launch. It is not the structured ``background`` parameter.

Make explicit background calls self-contained. Use an absolute directory and
``&&`` so a failed ``cd`` cannot run tests in the wrong checkout. Set required
variables in that same call and verify a project marker or Git root before
running a build/test command. For a disposable worktree, a harmless check is:

.. code-block:: bash

    cd -- /absolute/path/to/disposable-worktree &&
      test -f pyproject.toml &&
      git rev-parse --show-toplevel

Do not set a path variable in one foreground call and assume it exists in a
later explicit background call. Files remain shared; shell process state does
not. Managed jobs report their completion automatically. Inspect them with
``output N``, ``wait N [timeout]``, ``kill N`` or ``jobs`` as a complete shell-tool
call, not as a Bash pipeline or part of a script.

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
