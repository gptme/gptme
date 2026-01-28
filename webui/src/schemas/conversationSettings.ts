import { z } from 'zod';
import { ToolFormat } from '@/types/api';

export const mcpServerSchema = z.object({
  name: z.string().min(1, 'Server name cannot be empty'),
  enabled: z.boolean(),
  command: z.string().min(1, 'Command cannot be empty'),
  args: z.string(),
  env: z
    .array(
      z.object({
        key: z.string().min(1, 'Variable name cannot be empty'),
        value: z.string(),
      })
    )
    .optional(),
});

export const formSchema = z.object({
  chat: z.object({
    name: z.union([z.string().min(1, 'Chat name cannot be empty'), z.literal('')]).optional(),
    model: z.string().optional(),
    tools: z.array(z.object({ name: z.string().min(1, 'Tool name cannot be empty') })).optional(),
    tool_format: z.nativeEnum(ToolFormat).nullable().optional(),
    stream: z.boolean(),
    interactive: z.boolean(),
    workspace: z.string().min(1, 'Workspace directory is required'),
    env: z
      .array(
        z.object({ key: z.string().min(1, 'Variable name cannot be empty'), value: z.string() })
      )
      .optional(),
  }),
  mcp: z.object({
    enabled: z.boolean(),
    auto_start: z.boolean(),
    servers: z.array(mcpServerSchema).optional(),
  }),
});

export type FormSchema = z.infer<typeof formSchema>;

export const defaultMcpServer: z.infer<typeof mcpServerSchema> = {
  name: '',
  enabled: true,
  command: '',
  args: '',
  env: [],
};
