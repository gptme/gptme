#!/usr/bin/env python3
"""Test script for the chat history context feature."""

import os
import tempfile
import traceback
from pathlib import Path

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.prompts import prompt_chat_history, use_chat_history_context

# Set up environment to enable chat history
os.environ["GPTME_CHAT_HISTORY"] = "true"

# Import gptme modules


def test_chat_history_context():
    """Test the chat history context functionality."""

    # Check if feature is enabled
    print("Chat history context enabled:", use_chat_history_context())

    # Create some mock conversations for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_dir = Path(tmpdir)

        # Create a few mock conversations
        for i in range(3):
            conv_dir = logs_dir / f"test-conversation-{i}"
            conv_dir.mkdir(parents=True)

            # Create some sample messages
            messages = [
                Message("user", f"Hello, can you help me with project {i}?"),
                Message("assistant", f"Sure! I'd be happy to help with project {i}."),
                Message(
                    "user", f"Let's work on implementing feature X for project {i}"
                ),
                Message(
                    "assistant",
                    f"I've implemented feature X for project {i}. The work is complete.",
                ),
            ]

            # Save to conversation log
            log_manager = LogManager(messages, logdir=conv_dir, lock=False)
            log_manager.write()

    # Test the prompt generation
    try:
        history_messages = list(prompt_chat_history())
        print(f"Generated {len(history_messages)} history messages")

        for msg in history_messages:
            print(f"Message role: {msg.role}")
            print(f"Content length: {len(msg.content)}")
            print(
                "Content preview:",
                msg.content[:200] + "..." if len(msg.content) > 200 else msg.content,
            )
            print("-" * 50)

    except Exception as e:
        print(f"Error generating chat history: {e}")

        traceback.print_exc()


if __name__ == "__main__":
    test_chat_history_context()
