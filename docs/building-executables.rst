Building Executables
===================

gptme supports building standalone executables using PyInstaller for easier distribution.

Building gptme-server Executable
--------------------------------

To build a standalone executable for gptme-server:

1. **Install dependencies** (including PyInstaller):

   .. code-block:: bash

      poetry install --extras server --with dev

2. **Build the executable**:

   .. code-block:: bash

      make build-server-exe

   Or manually:

   .. code-block:: bash

      ./scripts/build_server_executable.sh

3. **Find the executable** in the ``dist/`` directory:

   .. code-block:: bash

      ls -la dist/gptme-server*

Usage
-----

The standalone executable includes all dependencies and can be run without Python installed:

.. code-block:: bash

   # Run the server
   ./dist/gptme-server --host 0.0.0.0 --port 5700

   # Show help
   ./dist/gptme-server --help

The executable includes:

- All Python dependencies (Flask, gptme, etc.)
- Static web UI files
- All gptme tools and functionality

Distribution
------------

The executable is self-contained and can be distributed to systems without Python or gptme installed.

**Note**: The executable is platform-specific (Linux/macOS/Windows).

Cleaning Build Artifacts
------------------------

To clean PyInstaller build artifacts:

.. code-block:: bash

   make clean-build

This removes the ``build/``, ``dist/``, and temporary spec backup files.

Customization
-------------

The PyInstaller configuration is in ``gptme-server.spec``. You can modify this file to:

- Add/remove hidden imports
- Include additional data files
- Change executable options
- Optimize the build

For more details, see the `PyInstaller documentation <https://pyinstaller.org/>`_.
