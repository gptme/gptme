export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export interface Message {
  role: MessageRole;
  content: string;
  timestamp?: string;
  files?: string[];
  hide?: boolean;
  /** Client-only: tracks send status for optimistic messages */
  _status?: 'pending' | 'sent' | 'failed';
  /** Client-only: error message when _status is 'failed' */
  _error?: string;
}

export interface StreamingMessage extends Message {
  isComplete: boolean;
}

export interface ToolUse {
  tool: string;
  args: string[];
  content: string;
}

export interface ConversationSummary {
  id: string;
  name: string;
  created?: number; // Unix timestamp of first message
  modified: number; // Unix timestamp of last file modification
  messages: number; // Message count
  branch?: string;
  workspace?: string;
  readonly?: boolean; // For demo conversations
  agent_name?: string;
  agent_path?: string;
  agent_avatar?: string;
  agent_urls?: Record<string, string>;
  last_message_role?: 'user' | 'assistant';
  last_message_preview?: string;
  serverId?: string; // which server this conversation is from (multi-backend)
  serverName?: string; // display name for the server label
}

export interface GenerateCallbacks {
  onToken?: (token: string) => void;
  onComplete?: (message: Message) => void;
  onToolOutput?: (message: Message) => void;
  onError?: (error: string) => void;
}
