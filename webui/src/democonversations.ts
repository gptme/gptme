import type { Message, ConversationSummary } from '@/types/conversation';

// Demo conversation message data
const demoMessages: Record<string, Message[]> = {
  introduction: [
    {
      role: 'assistant',
      content:
        "Hello! I'm gptme, your AI programming assistant. I can help you write code, manage projects, and solve programming problems. I can use various tools and directly interact with your system.\n\nLet me show you some of my capabilities!",
      timestamp: new Date().toISOString(),
    },
    {
      role: 'user',
      content: 'Show me how you can help with Python development',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content:
        '<thinking>\nI\'ll demonstrate:\n1. Creating a Python file\n2. Running Python code\n3. Making changes with patches\n4. Using the shell\n</thinking>\n\nFirst, let\'s create a simple Python script:\n\n```save hello.py\ndef greet(name: str) -> str:\n    return f"Hello, {name}!"\n\nif __name__ == "__main__":\n    print(greet("World"))\n```',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'system',
      content: 'Saved to hello.py',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content: "Now let's run it:\n\n```shell\npython hello.py\n```",
      timestamp: new Date().toISOString(),
    },
    {
      role: 'system',
      content: '```stdout\nHello, World!\n```',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content:
        'Let\'s improve the script by adding some documentation:\n\n```patch hello.py\n<<<<<<< ORIGINAL\ndef greet(name: str) -> str:\n=======\ndef greet(name: str) -> str:\n    """Greet someone by name.\n    \n    Args:\n        name: The name of the person to greet\n    Returns:\n        A friendly greeting\n    """\n>>>>>>> UPDATED\n```',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'system',
      content: 'Patch successfully applied to hello.py',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content:
        'We can also use Python interactively:\n\n```ipython\nimport numpy as np\nx = np.array([1, 2, 3])\nprint(f"Mean: {x.mean()}")\n```',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'system',
      content: '```stdout\nMean: 2.0\n```',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'user',
      content: 'What tools do you have available?',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content:
        'I have several tools at my disposal:\n\n1. **File Operations**:\n   - `save`: Create or overwrite files\n   - `append`: Append to files\n   - `patch`: Make targeted changes to files\n   - `read`: Read file contents\n\n2. **Code Execution**:\n   - `shell`: Run shell commands\n   - `ipython`: Execute Python code\n   - `tmux`: Run long-lived processes\n\n3. **Web & Research**:\n   - `browser`: Browse websites and take screenshots\n   - `youtube`: Fetch and summarize video transcripts\n   - `rag`: Search through project documentation\n\n4. **Other**:\n   - `screenshot`: Take screenshots\n   - `gh`: Interact with GitHub\n   - `chats`: Search conversation history\n\nLet me demonstrate the browser tool:\n\n```ipython\nsearch("gptme github repository")\n```',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'system',
      content:
        '```result\n1. [GitHub - gptme/gptme: A CLI tool for interacting with LLMs](https://github.com/gptme/gptme)\n...\n```',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content: 'And show you how to use git:\n\n```shell\ngit status\n```',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'system',
      content:
        "```stdout\nOn branch main\nYour branch is up to date with 'origin/main'\n\nnothing to commit, working tree clean\n```",
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content:
        'I can also help with more complex tasks like setting up CI/CD, managing dependencies, or debugging issues. Just let me know what you need help with!',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'user',
      content: 'Can you show an example of a failed patch?',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content:
        "Here's an attempt to patch a file that doesn't match the expected content:\n\n```patch hello.py\n<<<<<<< ORIGINAL\ndef something_that_doesnt_exist():\n    pass\n=======\ndef new_function():\n    pass\n>>>>>>> UPDATED\n```",
      timestamp: new Date().toISOString(),
    },
    {
      role: 'system',
      content: 'Error: Patch failed to apply. The original content was not found in the file.',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content:
        'When patches fail, I can attempt to apply them in different ways or re-read the file to ensure the content matches. I can recover from many common such issues without needing help.',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'user',
      content: 'Tell me about the web UI features',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content:
        "The gptme web UI offers several advantages over the CLI interface:\n\n1. **Rich Message Display**:\n   - Syntax highlighted code blocks\n   - Collapsible sections for code and thinking\n   - Different styles for user/assistant/system messages\n   - Emoji indicators for different types of content:\n     - 📄 File paths\n     - 🛠️ Tool usage\n     - 📤 Command output\n     - 💻 Code blocks\n\n2. **Interactive Features**:\n   - Real-time streaming of responses\n   - Easy navigation between conversations\n   - Ability to view and restore conversation history\n\n3. **Integration with gptme-server**:\n   - Connects to your local gptme instance\n   - Access to all local tools and capabilities\n   - Secure local execution of commands\n\nHere's an example showing different types of content:\n\n```/path/to/file.py\n# This shows as a file path\n```\n\n```shell\n# This shows as a tool\nls -la\n```\n\n```stdout\n# This shows as command output\ntotal 0\n```\n\n<thinking>\nThinking blocks are collapsible and help show my reasoning process\n</thinking>\n\nThis web UI is a work in progress and will be improved over time (see the [remaining issues](https://github.com/gptme/gptme-webui/issues)).",
      timestamp: new Date().toISOString(),
    },
    {
      role: 'user',
      content: 'How do I use the web UI?',
      timestamp: new Date().toISOString(),
    },
    {
      role: 'assistant',
      content:
        "To use the gptme web UI, you'll need to run `gptme-server` locally first:\n\n```shell\ngptme-server --cors-origin='<origin>'  # Replace <origin> with the web UI URL\n```\n\nThen you have two options for accessing the web UI:\n\n1. **Use the hosted version** at [chat.gptme.org](https://chat.gptme.org):\n   - Use `--cors-origin='https://chat.gptme.org'` when starting the server\n   - Click the 'Connect' button in the top-right corner\n   - Enter the server URL (default: http://127.0.0.1:5700)\n\n2. **Run the web UI locally**:\n   ```shell\n   git clone https://github.com/gptme/gptme-webui\n   cd gptme-webui\n   npm install\n   npm run dev\n   ```\n   Then:\n   - Use `--cors-origin='http://localhost:5701'` when starting the server\n   - Open http://localhost:5701 in your browser\n   - Click 'Connect' and enter the server URL\n\nBoth options provide the same features, just choose what works best for you!",
      timestamp: new Date().toISOString(),
    },
  ],
};

// Demo conversations (as ConversationSummary objects)
export const demoConversations: ConversationSummary[] = [
  {
    id: 'introduction',
    name: 'Introduction to gptme',
    modified: Math.floor(Date.now() / 1000), // Unix timestamp
    messages: demoMessages.introduction.length,
    readonly: true,
    workspace: '/demo/workspace',
  },
];

// Helper function to get demo conversation messages
export function getDemoMessages(conversationId: string): Message[] {
  return demoMessages[conversationId] || [];
}
