import logging
from datetime import datetime, timezone

from gptme.logmanager import ConversationMeta, Log, get_user_conversations

# Set up logging
logging.basicConfig(level=logging.ERROR)


def print_user_messages(conv: ConversationMeta):
    """
    Print all user messages from a single conversation.

    :param conversation: A dictionary containing conversation details
    """
    lines = []
    for message in Log.read_jsonl(conv.path):
        if message.role == "user":
            first_line = message.content.split("\n")[0]
            if first_line.startswith("<system>"):
                continue
            lines.append(
                f"{message.timestamp} - User: {first_line[:100]}{'...' if len(first_line) > 100 else ''}"
            )
    if not lines:
        return
    print(f"Conversation: {conv.name}")
    print(f"Created:  {datetime.fromtimestamp(conv.created, tz=timezone.utc)}")
    print(f"Modified: {datetime.fromtimestamp(conv.modified, tz=timezone.utc)}")
    print("---")
    print("\n".join(lines))
    print("---")
    print()


def process_all_conversations(limit_conversations=None):
    """
    Process all conversations and print user messages.

    :param limit_conversations: Maximum number of conversations to process
    """
    i = 0
    for i, conv in enumerate(get_user_conversations()):
        if limit_conversations is not None and i >= limit_conversations:
            break
        print_user_messages(conv)
    print(f"Total conversations processed: {i}")


if __name__ == "__main__":
    process_all_conversations(limit_conversations=100)
