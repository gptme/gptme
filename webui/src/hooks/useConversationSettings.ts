import { useApi } from '@/contexts/ApiContext';
import { conversations$, updateConversation } from '@/stores/conversations';
import { useForm, useFieldArray } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { formSchema, type FormSchema } from '@/schemas/conversationSettings';
import { toast } from 'sonner';
import type { ChatConfig } from '@/types/api';
import { useEffect, useState } from 'react';
import { use$ } from '@legendapp/state/react';
import { ToolFormat } from '@/types/api';
import { demoConversations } from '@/democonversations';

const chatConfigToFormValues = (config: ChatConfig | null): FormSchema => ({
  chat: {
    name: config?.chat.name || '',
    model: config?.chat.model || '',
    tools: config?.chat.tools?.map((tool) => ({ name: tool })) || [],
    tool_format: config?.chat.tool_format || ToolFormat.MARKDOWN,
    stream: config?.chat.stream ?? true,
    interactive: config?.chat.interactive ?? false,
    workspace: config?.chat.workspace || '',
    env: config?.env ? Object.entries(config.env).map(([key, value]) => ({ key, value })) : [],
  },
  mcp: {
    enabled: config?.mcp?.enabled ?? false,
    auto_start: config?.mcp?.auto_start ?? false,
    servers:
      config?.mcp?.servers?.map((server) => ({
        name: server.name || '',
        enabled: server.enabled ?? false,
        command: server.command || '',
        args: server.args?.join(', ') || '',
        env: server.env ? Object.entries(server.env).map(([key, value]) => ({ key, value })) : [],
      })) || [],
  },
});

export const useConversationSettings = (conversationId: string) => {
  const api = useApi();
  const conversation$ = conversations$.get(conversationId);
  const chatConfig = use$(conversation$?.chatConfig);
  const [configError, setConfigError] = useState<string | null>(null);
  const [isLoadingConfig, setIsLoadingConfig] = useState(false);

  const form = useForm<FormSchema>({
    resolver: zodResolver(formSchema),
    defaultValues: chatConfigToFormValues(chatConfig),
  });

  const toolFields = useFieldArray({
    control: form.control,
    name: 'chat.tools',
  });

  const envFields = useFieldArray({
    control: form.control,
    name: 'chat.env',
  });

  const serverFields = useFieldArray({
    control: form.control,
    name: 'mcp.servers',
  });

  // Reset form when chatConfig loads or changes, or when conversation changes
  // Note: We intentionally exclude 'form' from dependencies to prevent multiple resets
  useEffect(() => {
    form.reset(chatConfigToFormValues(chatConfig));
  }, [chatConfig, conversationId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset error state when conversation changes
  useEffect(() => {
    setConfigError(null);
  }, [conversationId]);

  // Load the chat config if it's not already loaded
  useEffect(() => {
    // Skip loading config for demo conversations
    const isDemo = demoConversations.some((conv) => conv.id === conversationId);

    if (!chatConfig && !isLoadingConfig && !isDemo && !configError) {
      setIsLoadingConfig(true);
      setConfigError(null);

      api
        .getChatConfig(conversationId)
        .then((config) => {
          updateConversation(conversationId, { chatConfig: config });
          setConfigError(null);
        })
        .catch((error) => {
          console.error('Failed to load chat config:', error);
          const errorMessage =
            error instanceof Error ? error.message : 'Failed to load configuration';
          setConfigError(errorMessage);
        })
        .finally(() => {
          setIsLoadingConfig(false);
        });
    }
  }, [api, chatConfig, conversationId, isLoadingConfig, configError]);

  const onSubmit = async (values: FormSchema) => {
    const originalConfig = chatConfig;
    if (!originalConfig) {
      console.error('Original chatConfig not found, cannot submit.');
      toast.error('Cannot save settings: Original configuration missing.');
      return;
    }

    // Capture original tools for comparison later
    const originalTools = originalConfig.chat.tools;

    const toolsStringArray = values.chat.tools?.map((tool) => tool.name);
    const newTools = toolsStringArray?.length ? toolsStringArray : null;
    const newEnv =
      values.chat.env?.reduce(
        (acc, { key, value }) => {
          if (key.trim()) acc[key.trim()] = value;
          return acc;
        },
        {} as Record<string, string>
      ) || {};

    const newMcpServers = values.mcp?.servers?.map((server) => ({
      name: server.name,
      enabled: server.enabled,
      command: server.command,
      args: server.args
        .split(',')
        .map((arg) => arg.trim())
        .filter(Boolean),
      env:
        server.env?.reduce(
          (acc, { key, value }) => {
            if (key.trim()) acc[key.trim()] = value;
            return acc;
          },
          {} as Record<string, string>
        ) || {},
    }));

    const newConfig: ChatConfig = {
      ...originalConfig,
      chat: {
        ...originalConfig.chat,
        name: values.chat.name || null,
        model: values.chat.model || null,
        tools: newTools,
        tool_format: values.chat.tool_format || null,
        stream: values.chat.stream,
        interactive: values.chat.interactive,
        workspace: values.chat.workspace,
      },
      env: newEnv,
      mcp: {
        enabled: values.mcp.enabled,
        auto_start: values.mcp.auto_start,
        servers: newMcpServers || [],
      },
    };

    try {
      await api.updateChatConfig(conversationId, newConfig);

      const toolsChanged =
        JSON.stringify(originalTools?.slice().sort()) !==
        JSON.stringify(newConfig.chat.tools?.slice().sort());
      const mcpChanged =
        originalConfig.mcp?.enabled !== newConfig.mcp?.enabled ||
        originalConfig.mcp?.auto_start !== newConfig.mcp?.auto_start;
      const mcpServersChanged =
        JSON.stringify(originalConfig.mcp?.servers?.slice().sort()) !==
        JSON.stringify(newConfig.mcp?.servers?.slice().sort());

      if (toolsChanged || mcpChanged || mcpServersChanged) {
        const conversationData = await api.getConversation(conversationId);
        updateConversation(conversationId, { data: conversationData, chatConfig: newConfig });
      } else {
        updateConversation(conversationId, { chatConfig: newConfig });
      }
      toast.success('Settings updated successfully!');
    } catch (error) {
      console.error('Failed to update chat config:', error);
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      toast.error(`Failed to update settings: ${errorMessage}`);
    }
  };

  return {
    form,
    toolFields,
    envFields,
    serverFields,
    onSubmit,
    chatConfig,
    configError,
    isLoadingConfig,
  };
};
