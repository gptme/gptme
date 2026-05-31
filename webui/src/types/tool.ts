export interface ToolParameter {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

export interface Tool {
  name: string;
  desc: string;
  instructions: string;
  block_types: string[];
  is_mcp: boolean;
  is_available: boolean;
  disabled_by_default: boolean;
  parameters: ToolParameter[];
}

export interface ToolListResponse {
  tools: Tool[];
}
