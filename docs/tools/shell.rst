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

**Allowlisted commands** are auto-confirmed without a prompt. These are
read-only or information-gathering commands considered safe to run
unattended:

.. code-block:: text

    ls  stat  cd  cat  pwd  echo  head  find  rg  ag  tail  grep
    wc  sort  uniq  cut  file  which  type  tree  du  df

**Transparent wrappers** are stripped before the allowlist check, so wrapping
an allowlisted command with a timing or resource-limit prefix does not
require a prompt. Recognised wrappers:

.. code-block:: text

    time [−p]
    timeout [options] <duration>
    nohup
    nice [−n <adj>]
    stdbuf [−i/−o/−e <mode>]
    env [−i / −u VAR]   (bare env, without NAME=value)
    command [−p]
    builtin

For example, ``timeout 60 grep -r foo /var/log`` runs without a prompt
because ``grep`` is allowlisted. Variables (``PATH=. ls``) are deliberately
**not** treated as transparent — they change what the command does and are
always gated.

**Denied commands** are blocked outright and never executed:

- ``git add .`` / ``git add -A`` / ``git commit -a`` — bulk staging
- Destructive git: ``git reset --hard``, ``git clean -f``, ``git push -f``
- ``rm -rf /`` and variants targeting the root
- ``chmod 777``
- ``pkill`` / ``killall``
- Piping to shell interpreters: ``... | bash``, ``... | python3``

Everything else falls through to the normal confirmation prompt.

Command Parsing
---------------

Since v0.34.0, gptme uses `tree-sitter-bash
<https://github.com/tree-sitter/tree-sitter-bash>`_ to split multi-command
scripts into individual commands for sequential execution and to detect
background operators (``&``). This replaced the former ``bashlex`` parser,
which could not handle several common constructs (``time``, ``timeout``,
process substitution ``<(…)``, arithmetic expansion ``$(( ))``) and reported
them as syntax errors to the model.

The allowlist and denylist checks always operate on the **raw command text**
and are unaffected by the parser.
