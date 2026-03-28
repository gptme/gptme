const markedHighlight = globalThis.markedHighlight.markedHighlight;
const Marked = globalThis.marked.Marked;
const hljs = globalThis.hljs;

const apiRoot = "/api/conversations";

const marked = new Marked(
  markedHighlight({
    langPrefix: "hljs language-",
    highlight(code, lang, info) {
      // check if info has ext, if so, use that as lang
      lang = info.split(".")[1] || lang;
      console.log(info);
      console.log(lang);
      const language = hljs.getLanguage(lang) ? lang : "plaintext";
      return hljs.highlight(code, { language }).value;
    },
  })
);

new Vue({
  el: "#app",
  data: {
    // List of conversations
    conversations: [],

    // Name/ID of the selected conversation
    selectedConversation: null,

    // List of messages in the selected conversation
    branch: "main",
    chatLog: [],

    // Options
    sortBy: "modified",
    showSystemMessages: false, // hide initial system messages

    // Inputs
    newMessage: "",

    // Status
    cmdout: "",
    error: "",
    generating: false,

    // Conversations limit
    conversationsLimit: 20,

    // Agent URLs from gptme.toml [agent.urls] (dashboard, repo, etc.)
    agentUrls: {},

    // Autocomplete state
    commands: [],              // Available slash commands from API
    autocompleteType: null,    // null | 'command' | 'file'
    autocompleteItems: [],     // Items to display in dropdown
    autocompleteIndex: 0,      // Currently highlighted item
    fileCache: {},             // Cache workspace file listings by directory
    fileFetchTimeout: null,    // Debounce timer for file fetching
  },
  async mounted() {
    // Load agent config and available commands
    this.loadAgentConfig();
    this.loadCommands();

    // Check for embedded data first
    if (window.CHAT_DATA) {
      this.conversations = [
        {
          name: CHAT_NAME,
          messages: CHAT_DATA.length,
          modified:
            new Date(CHAT_DATA[CHAT_DATA.length - 1].timestamp).getTime() /
            1000,
        },
      ];
      this.selectedConversation = CHAT_NAME;
      this.chatLog = CHAT_DATA;
      this.branch = "main";
      this.branches = { main: CHAT_DATA };
    } else {
      // Normal API mode
      await this.getConversations();
      // if the hash is set, select that conversation
      if (window.location.hash) {
        await this.selectConversation(window.location.hash.slice(1));
      }
    }
    // remove display-none class from app
    document.getElementById("app").classList.remove("hidden");
    // remove loader animation
    document.getElementById("loader").classList.add("hidden");
  },
  computed: {
    sortedConversations: function () {
      const reverse = this.sortBy[0] === "-";
      const sortBy = reverse ? this.sortBy.slice(1) : this.sortBy;
      return this.conversations.sort(
        (a, b) => b[sortBy] - a[sortBy] * (reverse ? -1 : 1)
      );
    },
    showAutocomplete: function () {
      return this.autocompleteType !== null && this.autocompleteItems.length > 0;
    },
    preparedChatLog: function () {
      // Set hide flag on initial system messages
      for (const msg of this.chatLog) {
        if (msg.role !== "system") break;
        msg.hide = !this.showSystemMessages;
      }

      // Find branch points and annotate messages where branches occur,
      // so that we can show them in the UI, and let the user jump to them.
      this.chatLog.forEach((msg, i) => {
        msg.branches = [this.branch];

        // Check each branch if the fork at the current message
        for (const branch of Object.keys(this.branches)) {
          if (branch === this.branch) continue; // skip main branch

          // Check if the next message in current branch diverges from next message on other branch
          const next_msg = this.branches[this.branch][i + 1];
          const branch_msg = this.branches[branch][i + 1];

          // FIXME: there is a bug here in more complex cases
          if (
            next_msg &&
            branch_msg &&
            branch_msg.timestamp !== next_msg.timestamp
          ) {
            // We found a fork, so annotate the message
            msg.branches.push(branch);
            break;
          }
        }

        // Sort the branches by timestamp
        msg.branches.sort((a, b) => {
          const a_msg = this.branches[a][i + 1];
          const b_msg = this.branches[b][i + 1];
          if (!a_msg) return 1;
          if (!b_msg) return -1;
          const diff = new Date(a_msg.timestamp) - new Date(b_msg.timestamp);
          if (Number.isNaN(diff)) {
            console.error("diff was NaN");
          }
          return diff;
        });
      });

      // Convert markdown to HTML
      return this.chatLog.map((msg) => {
        msg.html = this.mdToHtml(msg.content);
        return msg;
      });
    },
  },
  methods: {
    safeUrl(url) {
      // Only allow http/https to prevent javascript: XSS in Vue 2 :href bindings
      return /^https?:\/\//i.test(url) ? url : '#';
    },

    // --- Autocomplete ---

    async loadCommands() {
      try {
        const res = await fetch("/api/v2/commands");
        if (!res.ok) return;
        const data = await res.json();
        this.commands = data.commands || [];
      } catch (e) {
        console.debug("Could not load commands:", e);
      }
    },
    onInput() {
      const input = this.newMessage;
      const trimmed = input.trimStart();

      // Slash command completion: input starts with / and has no spaces
      if (trimmed.startsWith("/") && !trimmed.includes(" ")) {
        const query = trimmed.slice(1).toLowerCase();
        this.autocompleteItems = this.commands.filter(cmd => {
          const name = cmd.startsWith("/") ? cmd.slice(1) : cmd;
          return name.toLowerCase().startsWith(query);
        });
        this.autocompleteType = this.autocompleteItems.length > 0 ? "command" : null;
        this.autocompleteIndex = 0;
        return;
      }

      // File path completion: find @token being typed
      // Look for @ preceded by whitespace or at start of input
      const textarea = this.$refs.chatInput;
      if (textarea && this.selectedConversation) {
        const cursorPos = textarea.selectionStart;
        const textBeforeCursor = input.slice(0, cursorPos);
        const atMatch = textBeforeCursor.match(/(^|\s)@(\S*)$/);
        if (atMatch) {
          const partialPath = atMatch[2];
          this.fetchFileCompletions(partialPath);
          return;
        }
      }

      // No match — hide autocomplete
      this.autocompleteType = null;
      this.autocompleteItems = [];
    },
    async fetchFileCompletions(partialPath) {
      // Debounce file fetching to avoid excessive API calls
      clearTimeout(this.fileFetchTimeout);
      this.fileFetchTimeout = setTimeout(async () => {
        // Split into directory and filename prefix
        const lastSlash = partialPath.lastIndexOf("/");
        const dir = lastSlash >= 0 ? partialPath.slice(0, lastSlash) : "";
        const prefix = lastSlash >= 0 ? partialPath.slice(lastSlash + 1) : partialPath;

        try {
          // Use workspace browse API
          const dirParam = dir ? `/${dir}` : "";
          const url = `/api/v2/conversations/${this.selectedConversation}/workspace${dirParam}`;

          let files;
          if (this.fileCache[url]) {
            files = this.fileCache[url];
          } else {
            const res = await fetch(url);
            if (!res.ok) {
              this.autocompleteType = null;
              this.autocompleteItems = [];
              return;
            }
            const data = await res.json();
            files = (Array.isArray(data) ? data : []).map(f => ({
              name: f.name,
              path: f.path,
              type: f.type,
              display: dir ? `${dir}/${f.name}` : f.name,
            }));
            this.fileCache[url] = files;
          }

          // Filter by prefix
          const filtered = files.filter(f =>
            f.name.toLowerCase().startsWith(prefix.toLowerCase())
          );

          this.autocompleteItems = filtered.map(f => ({
            label: f.display + (f.type === "directory" ? "/" : ""),
            value: f.display + (f.type === "directory" ? "/" : ""),
            type: f.type,
          }));
          this.autocompleteType = this.autocompleteItems.length > 0 ? "file" : null;
          this.autocompleteIndex = 0;
        } catch (e) {
          console.debug("Could not fetch file completions:", e);
          this.autocompleteType = null;
          this.autocompleteItems = [];
        }
      }, 150);
    },
    selectAutocompleteItem(item) {
      clearTimeout(this.fileFetchTimeout);
      if (this.autocompleteType === "command") {
        // For commands, replace entire input
        this.newMessage = item + " ";
      } else if (this.autocompleteType === "file") {
        // For files, replace the @token in the input
        const textarea = this.$refs.chatInput;
        const cursorPos = textarea.selectionStart;
        const textBeforeCursor = this.newMessage.slice(0, cursorPos);
        const atMatch = textBeforeCursor.match(/(^|\s)@(\S*)$/);
        if (atMatch) {
          const matchStart = atMatch.index + atMatch[1].length; // position of @
          const value = typeof item === "object" ? item.value : item;
          const suffix = value.endsWith("/") ? "" : " ";
          const before = this.newMessage.slice(0, matchStart);
          const after = this.newMessage.slice(cursorPos);
          this.newMessage = before + "@" + value + suffix + after;
        }
      }
      this.autocompleteType = null;
      this.autocompleteItems = [];
      this.$nextTick(() => {
        this.$refs.chatInput.focus();
      });
    },
    getItemLabel(item) {
      // Autocomplete items can be strings (commands) or objects (files)
      if (typeof item === "string") return item;
      return item.label || item.value || "";
    },

    // --- End Autocomplete ---

    async loadAgentConfig() {
      try {
        const res = await fetch("/api/config");
        if (!res.ok) return;
        const data = await res.json();
        if (data.agent && data.agent.urls) {
          // Filter to only http/https URLs before assigning — prevents non-conforming
          // entries from rendering as broken '#' links and the header from showing
          // when every URL is invalid.
          const filtered = {};
          for (const [key, url] of Object.entries(data.agent.urls)) {
            if (/^https?:\/\//i.test(url)) filtered[key] = url;
          }
          this.agentUrls = filtered;
        }
      } catch (e) {
        // Non-critical: silently ignore if endpoint unavailable
        console.debug("Could not load agent config:", e);
      }
    },
    async getConversations() {
      const res = await fetch(`${apiRoot}?limit=${this.conversationsLimit}`);
      this.conversations = await res.json();
    },
    async selectConversation(path, branch) {
      // set the hash to the conversation name
      window.location.hash = path;

      this.selectedConversation = path;
      // Clear autocomplete state when switching conversations
      clearTimeout(this.fileFetchTimeout);
      this.fileCache = {};
      this.autocompleteType = null;
      this.autocompleteItems = [];

      const res = await fetch(`${apiRoot}/${path}`);

      // check for errors
      if (!res.ok) {
        this.error = res.statusText;
        return;
      }

      try {
        const data = await res.json();
        this.branches = data.branches;
        this.branches["main"] = data.log;
        this.branch = branch || "main";
        this.chatLog = this.branches[this.branch];
      } catch (e) {
        this.error = e.toString();
        console.log(e);
        return;
      }

      // TODO: Only scroll to bottom on conversation load and new messages
      this.$nextTick(() => {
        this.scrollToBottom();
      });
    },
    dismissError() {
      this.error = null;
    },
    async createConversation() {
      const name = prompt("Conversation name");
      if (!name) return;
      const res = await fetch(`${apiRoot}/${name}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify([]),
      });
      if (!res.ok) {
        this.error = res.statusText;
        return;
      }
      await this.getConversations();
      this.selectConversation(name);
    },
    async sendMessage() {
      // Dismiss autocomplete before sending
      clearTimeout(this.fileFetchTimeout);
      this.autocompleteType = null;
      this.autocompleteItems = [];

      const messageContent = this.newMessage;
      // Clear input immediately
      this.newMessage = "";

      // Add message to chat log immediately
      const tempMessage = {
        role: "user",
        content: messageContent,
        timestamp: new Date().toISOString(),
        html: this.mdToHtml(messageContent)
      };
      this.chatLog.push(tempMessage);
      this.scrollToBottom();

      // Send to server
      const payload = JSON.stringify({
        role: "user",
        content: messageContent,
        branch: this.branch,
      });

      try {
        const req = await fetch(`${apiRoot}/${this.selectedConversation}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: payload,
        });

        if (!req.ok) {
            throw new Error(req.statusText);
        }

        await req.json();
        // Reload conversation to get server-side state
        await this.selectConversation(this.selectedConversation, this.branch);
        // Generate response
        this.generate();
      } catch (error) {
        this.error = error.toString();
        // Remove temporary message on error
        this.chatLog.pop();
        // Refill input
        this.newMessage = messageContent;
      }
    },
    async generate() {
      this.generating = true;
      let currentMessage = {
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
      };
      this.chatLog.push(currentMessage);

      try {
        // Create EventSource with POST method using fetch
        const response = await fetch(
          `${apiRoot}/${this.selectedConversation}/generate`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ branch: this.branch }),
          }
        );

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
          const {value, done} = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value);
          // Parse SSE data
          const lines = chunk.split('\n');
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = JSON.parse(line.slice(6));

              if (data.error) {
                this.error = data.error;
                break;
              }

              if (data.stored === false) {
                // Streaming token from assistant
                currentMessage.content += data.content;
                currentMessage.html = this.mdToHtml(currentMessage.content);
                this.scrollToBottom();
              } else {
                // Tool output or stored message
                if (data.role === "system") {
                  this.cmdout = data.content;
                } else {
                  // Add as a new message
                  const newMsg = {
                    role: data.role,
                    content: data.content,
                    timestamp: new Date().toISOString(),
                    html: this.mdToHtml(data.content),
                    files: data.files || [],
                  };
                  this.chatLog.push(newMsg);
                }
              }
            }
          }
        }

        // After streaming is complete, reload to ensure we have the server's state
        this.generating = false;
        await this.selectConversation(this.selectedConversation, this.branch);
      } catch (error) {
        this.error = error.toString();
        this.generating = false;
        // Remove the temporary message on error
        this.chatLog.pop();
      }
    },
    changeBranch(branch) {
      this.branch = branch;
      this.chatLog = this.branches[branch];
    },
    backToConversations() {
      this.getConversations(); // refresh conversations
      this.selectedConversation = null;
      this.chatLog = [];
      window.location.hash = "";
    },
    scrollToBottom() {
      this.$nextTick(() => {
        const container = this.$refs.chatContainer;
        container.scrollTop = container.scrollHeight;
      });
    },
    fromNow(timestamp) {
      return moment(new Date(timestamp)).fromNow();
    },
    mdToHtml(md) {
      // TODO: Use DOMPurify.sanitize
      // First unescape any HTML entities in the markdown
      md = md.replace(/&([^;]+);/g, (match, entity) => {
        const textarea = document.createElement("textarea");
        textarea.innerHTML = match;
        return textarea.value;
      });
      md = this.wrapThinkingInDetails(md);
      let html = marked.parse(md);
      html = this.wrapBlockInDetails(html);
      return html;
    },

    wrapBlockInDetails(text) {
      const codeBlockRegex =
        /<pre><code class="([^"]+)">([\s\S]*?)<\/code><\/pre>/g;
      return text.replace(codeBlockRegex, function (match, classes, code) {
        const langtag = (classes.split(" ")[1] || "Code").replace(
          "language-",
          ""
        );
        return `<details><summary>${langtag}</summary><pre><code class="${classes}">${code}</code></pre></details>`;
      });
    },

    wrapThinkingInDetails(text) {
      // replaces <thinking>...</thinking> with <details><summary>Thinking</summary>...</details>
      const thinkingBlockRegex = /<thinking>([\s\S]*?)<\/thinking>/g;
      return text.replace(thinkingBlockRegex, function (match, content) {
        return `<details><summary>Thinking</summary>\n\n${content}\n\n</details>`;
      });
    },

    changeSort(sortBy) {
      // if already sorted by this field, reverse the order
      if (this.sortBy === sortBy) {
        this.sortBy = `-${sortBy}`;
      } else {
        this.sortBy = sortBy;
      }
    },
    capitalize(string) {
      return string.charAt(0).toUpperCase() + string.slice(1);
    },
    async loadMoreConversations() {
      this.conversationsLimit += 100;
      await this.getConversations();
    },
    isImage(filename) {
      return /\.(jpg|jpeg|png|gif|webp)$/i.test(filename);
    },
    fileUrl(filename) {
      const sanitized = filename.split('/').filter(part => part !== '..').join('/');
      return `${apiRoot}/${this.selectedConversation}/files/${sanitized}`;
    },
    handleKeyDown(e) {
      // Autocomplete navigation
      if (this.showAutocomplete && this.autocompleteItems.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          this.autocompleteIndex = (this.autocompleteIndex + 1) % this.autocompleteItems.length;
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          this.autocompleteIndex = (this.autocompleteIndex - 1 + this.autocompleteItems.length) % this.autocompleteItems.length;
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          this.selectAutocompleteItem(this.autocompleteItems[this.autocompleteIndex]);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          clearTimeout(this.fileFetchTimeout);
          this.autocompleteType = null;
          this.autocompleteItems = [];
          return;
        }
      }
      // If Enter is pressed without Shift
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();  // Prevent default newline
        this.sendMessage();  // Send the message
      }
      // If Shift+Enter, let the default behavior happen (create newline)
    },
  },
});
