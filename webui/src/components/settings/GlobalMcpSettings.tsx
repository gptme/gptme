import { useEffect, useState } from 'react';
import { useFieldArray, useForm } from 'react-hook-form';
import { useApi } from '@/contexts/ApiContext';
import { Form } from '@/components/ui/form';
import { Button } from '@/components/ui/button';
import { McpConfiguration } from './McpConfiguration';
import type { FormSchema } from '@/schemas/conversationSettings';
import type { McpConfig, McpServerConfig } from '@/types/api';
import { ToolFormat } from '@/types/api';
import { toast } from 'sonner';

type GlobalMcpConfig = Omit<McpConfig, 'servers'> & {
  servers: (McpServerConfig & { url: string; headers: Record<string, string> })[];
  path: string;
};

function toForm(config: GlobalMcpConfig): FormSchema {
  return {
    chat: { stream: true, interactive: false, workspace: '', tool_format: ToolFormat.MARKDOWN },
    mcp: {
      enabled: config.enabled,
      auto_start: config.auto_start,
      servers: config.servers.map((server) => ({
        ...server,
        args: server.args.join(', '),
        original_args: server.args,
        env: Object.entries(server.env).map(([key, value]) => ({ key, value })),
      })),
    },
  };
}

async function readResponse(response: Response): Promise<GlobalMcpConfig> {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Failed to read MCP configuration');
  return data;
}

/** The same server-owned editor is used by browsers and the Tauri webview. */
export function GlobalMcpSettings() {
  const { api } = useApi();
  const [config, setConfig] = useState<GlobalMcpConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const form = useForm<FormSchema>();
  const serverFields = useFieldArray({ control: form.control, name: 'mcp.servers' });
  const { isDirty, isSubmitting } = form.formState;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setConfig(null);
    setError(null);
    fetch(`${api.baseUrl}/api/v2/user/config/mcp`, {
      headers: api.authHeader ? { Authorization: api.authHeader } : {},
      signal: controller.signal,
    })
      .then(readResponse)
      .then((data) => {
        if (controller.signal.aborted) return;
        setConfig(data);
        form.reset(toForm(data));
      })
      .catch((err) => {
        if (!controller.signal.aborted)
          setError(err instanceof Error ? err.message : 'Failed to load MCP configuration');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api.baseUrl, api.authHeader, form]);

  const save = async (values: FormSchema) => {
    setError(null);
    try {
      const servers = (values.mcp.servers ?? []).map((server) => ({
        name: server.name,
        enabled: server.enabled,
        command: server.command,
        args:
          server.original_args && server.args === server.original_args.join(', ')
            ? server.original_args
            : server.args
                .split(',')
                .map((arg) => arg.trim())
                .filter(Boolean),
        env: Object.fromEntries((server.env ?? []).map(({ key, value }) => [key, value])),
        url: server.url ?? '',
        headers: server.headers ?? {},
      }));
      const data = await readResponse(
        await fetch(`${api.baseUrl}/api/v2/user/config/mcp`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            ...(api.authHeader ? { Authorization: api.authHeader } : {}),
          },
          body: JSON.stringify({
            enabled: values.mcp.enabled,
            auto_start: values.mcp.auto_start,
            servers,
          }),
        })
      );
      setConfig(data);
      form.reset(toForm(data));
      toast.success('Global MCP settings saved. Restart running sessions to apply changes.');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to save MCP configuration';
      setError(message);
      toast.error(message);
    }
  };

  if (loading) return <p className="text-sm text-muted-foreground">Loading global MCP settings…</p>;
  return (
    <div className="space-y-4">
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {config && (
        <>
          <p className="text-sm text-muted-foreground">
            Global defaults in <code>{config.path}</code>. Per-chat settings and local config may
            override these. HTTP URLs and headers are preserved; edit them using Config files below.
            Secret values shown as *** are kept unchanged on save.
          </p>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(save)} className="space-y-4">
              <McpConfiguration
                form={form}
                serverFields={serverFields}
                isSubmitting={isSubmitting}
              />
              <Button type="submit" disabled={!isDirty || isSubmitting}>
                {isSubmitting ? 'Saving…' : 'Save global MCP settings'}
              </Button>
            </form>
          </Form>
        </>
      )}
    </div>
  );
}
