# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

# Add the current directory to the path (should be project root)
project_root = Path.cwd()
sys.path.insert(0, str(project_root))

# Define data files to include
datas = [
    # Include server static files
    (str(project_root / 'gptme/server/static'), 'gptme/server/static'),
    # Include the logo if needed
    (str(project_root / 'media/logo.png'), 'media'),
]

# Hidden imports - modules that PyInstaller might miss
hiddenimports = [
    'gptme.server.cli',
    'gptme.server.api',
    'gptme.server.api_v2',
    'gptme.server.workspace_api',
    'gptme.tools',
    'gptme.tools.shell',
    'gptme.tools.python',
    'gptme.tools.patch',
    'gptme.tools.save',
    'gptme.tools.read',
    'gptme.tools.browser',
    'gptme.tools.vision',
    'gptme.tools.screenshot',
    'gptme.tools.computer',
    'gptme.tools.gh',
    'gptme.tools.tmux',
    'gptme.tools.chats',
    'gptme.tools.rag',
    'gptme.tools.tts',
    'gptme.tools.youtube',
    'gptme.tools.subagent',
    'gptme.llm.llm_openai',
    'gptme.llm.llm_anthropic',
    'gptme.llm.models',
    'gptme.mcp.client',
    'flask',
    'flask_cors',
    'werkzeug',
    'jinja2',
    'markupsafe',
    'itsdangerous',
    'click',
    'blinker',
    'importlib_metadata',
    'platformdirs',
    'tiktoken_ext.openai_public',
    'tiktoken_ext',
]

# Exclude modules that might cause issues or aren't needed
excludes = [
    'matplotlib',
    'tkinter',
    'IPython.kernel',
    'jupyter',
    'notebook',
    'pytest',
    'sphinx',
    'scipy',  # TTS functionality not needed for server
    'sounddevice',  # TTS functionality not needed for server
    'numpy',  # TTS functionality not needed for server
]

a = Analysis(
    [str(project_root / 'gptme/server/__main__.py')],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='gptme-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
