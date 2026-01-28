export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export interface Message {
  role: MessageRole;
  content: string;
  timestamp?: string;
  files?: string[];
  hide?: boolean;
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
  modified: number; // Unix timestamp
  messages: number; // Message count
  branch?: string;
  workspace?: string;
  readonly?: boolean; // For demo conversations
  agent_name?: string;
  agent_path?: string;
}

export interface GenerateCallbacks {
  onToken?: (token: string) => void;
  onComplete?: (message: Message) => void;
  onToolOutput?: (message: Message) => void;
  onError?: (error: string) => void;
}
