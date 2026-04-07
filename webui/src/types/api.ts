import type { Message, StreamingMessage } from './conversation';

// External session catalog item (from /api/v2/external-sessions)
export interface ExternalSessionCatalogItem {
  id: string;
  session_id: string;
  harness: string;
  session_name: string | null;
  project: string | null;
  model: string | null;
  started_at: string | null;
  last_activity: string | null;
  capabilities: string[];
  trajectory_path: string;
}

// External session detail (from /api/v2/external-sessions/:id)
export interface ExternalSessionDetail {
  id: string;
  transcript: Record<string, unknown>;
}

// Error response from any endpoint
export interface ApiError {
  error: string;
  status?: number;
}

// Request to create a conversation
export interface CreateConversationRequest {
  messages: Message[];
  config?: {
    chat?: {
      model?: string;
      stream?: boolean;
      workspace?: string;
    };
  };
}

// Request to send a message
export interface SendMessageRequest extends Message {
  branch?: string;
}

// Agent info returned with conversation
export interface AgentInfo {
  name: string;
  avatar?: string;
}

// User identity info from global config
export interface UserInfo {
  name: string;
  avatar?: string;
}

// Response from /api/conversations/<logfile>
export interface ConversationResponse {
  id: string;
  name: string;
  log: (Message | StreamingMessage)[];
  logfile: string;
  branches: Record<string, Message[]>;
  workspace: string;
  agent?: AgentInfo;
}

export enum ToolFormat {
  MARKDOWN = 'markdown',
  XML = 'xml',
  TOOL = 'tool',
}

export interface McpServerConfig {
  name: string;
  enabled: boolean;
  command: string;
  args: string[];
  env: Record<string, string>;
}

export interface McpConfig {
  enabled: boolean;
  auto_start: boolean;
  servers: McpServerConfig[];
}

export interface ChatConfig {
  chat: {
    name: string | null;
    model: string | null;
    tools: string[] | null;
    tool_format: ToolFormat | null;
    stream: boolean;
    interactive: boolean;
    workspace: string;
  };
  env: Record<string, string>;
  mcp: McpConfig;
}
