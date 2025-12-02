"""
Prompt queueing for interactive user input during agent generation.

This module provides functionality to allow users to type and submit
prompts while the agent is generating a response, similar to Claude Code.

Design (per Erik's feedback on PR #907):
1. During generation, user can type at any time
2. When user presses Enter, show dialog: Run now / Queue / Discard
3. Keep implementation simple without fancy TUI tricks
"""
import logging
import select
import sys
import threading
from dataclasses import dataclass
from enum import Enum

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


class QueueAction(Enum):
    """Action to take with queued input."""
    RUN_NOW = "run"      # Interrupt generation, run immediately
    QUEUE = "queue"      # Queue for after generation completes
    DISCARD = "discard"  # Discard the input


@dataclass
class QueuedInput:
    """Input captured during generation."""
    text: str
    action: QueueAction


class InputMonitor:
    """Monitor stdin for user input during generation.
    
    Runs in a background thread to detect when users type
    and press Enter during agent generation.
    """
    
    def __init__(self):
        self._captured_input: str | None = None
        self._input_ready = threading.Event()
        self._active = threading.Event()
        self._thread: threading.Thread | None = None
    
    def start(self):
        """Start monitoring stdin in background."""
        if not sys.stdin.isatty():
            return
        
        self._active.set()
        self._input_ready.clear()
        self._captured_input = None
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop monitoring stdin."""
        self._active.clear()
        if self._thread:
            self._thread.join(timeout=0.5)
            self._thread = None
    
    def _monitor_loop(self):
        """Background thread that monitors stdin for input."""
        buffer: list[str] = []
        while self._active.is_set():
            try:
                readable, _, _ = select.select([sys.stdin], [], [], 0.1)
            except (ValueError, OSError):
                break
            
            if not readable:
                continue
            
            try:
                char = sys.stdin.read(1)
                if not char:
                    break
                
                if char == '\n':
                    text = ''.join(buffer).strip()
                    if text:
                        self._captured_input = text
                        self._input_ready.set()
                        return
                    buffer.clear()
                else:
                    buffer.append(char)
            except Exception:
                break
    
    def has_input(self) -> bool:
        """Check if user input has been captured."""
        return self._input_ready.is_set()
    
    def get_input(self) -> str | None:
        """Get captured input if available."""
        if self._input_ready.is_set():
            return self._captured_input
        return None
    
    def clear(self):
        """Clear captured input and reset state."""
        self._captured_input = None
        self._input_ready.clear()


def prompt_queue_dialog(captured_text: str) -> QueueAction:
    """Show dialog when user input is detected during generation.
    
    Args:
        captured_text: The text user typed during generation
    
    Returns:
        QueueAction indicating what to do with the input
    """
    # Show what was captured
    preview = captured_text[:50] + ('...' if len(captured_text) > 50 else '')
    console.print(f"\n[bold yellow]Input detected:[/bold yellow] {preview}")
    console.print("  [bold]R[/bold] - Run now (interrupt generation)")
    console.print("  [bold]Q[/bold] - Queue (run after generation)")  
    console.print("  [bold]D[/bold] - Discard")
    
    while True:
        try:
            choice = input("Choice [R/Q/D]: ").strip().lower()
            if choice in ('r', 'run'):
                return QueueAction.RUN_NOW
            elif choice in ('q', 'queue'):
                return QueueAction.QUEUE
            elif choice in ('d', 'discard'):
                return QueueAction.DISCARD
            else:
                console.print("[red]Invalid choice. Enter R, Q, or D.[/red]")
        except (EOFError, KeyboardInterrupt):
            return QueueAction.DISCARD


# Global instance for use during generation
_monitor: InputMonitor | None = None
_queued_prompt: str | None = None


def start_input_monitoring():
    """Start monitoring for user input during generation."""
    global _monitor
    if _monitor is None:
        _monitor = InputMonitor()
    _monitor.start()


def stop_input_monitoring():
    """Stop monitoring for user input."""
    global _monitor
    if _monitor:
        _monitor.stop()


def check_for_input() -> QueuedInput | None:
    """Check if user submitted input during generation.
    
    If input was submitted, shows dialog and returns QueuedInput
    with the user's chosen action.
    
    Returns:
        QueuedInput if user submitted input, None otherwise
    """
    global _monitor, _queued_prompt
    
    if _monitor is None or not _monitor.has_input():
        return None
    
    captured = _monitor.get_input()
    if not captured:
        return None
    
    # Show dialog and get user's choice
    action = prompt_queue_dialog(captured)
    _monitor.clear()
    
    if action == QueueAction.QUEUE:
        _queued_prompt = captured
        console.print("[dim]Input queued. Continuing generation...[/dim]\n")
        # Restart monitoring for more input
        _monitor.start()
        return QueuedInput(text=captured, action=action)
    elif action == QueueAction.RUN_NOW:
        return QueuedInput(text=captured, action=action)
    else:
        console.print("[dim]Input discarded. Continuing generation...[/dim]\n")
        _monitor.start()
        return QueuedInput(text=captured, action=action)


def get_queued_prompt() -> str | None:
    """Get any prompt that was queued during generation."""
    global _queued_prompt
    prompt = _queued_prompt
    _queued_prompt = None
    return prompt


def clear_queued_prompt():
    """Clear any queued prompt."""
    global _queued_prompt
    _queued_prompt = None
