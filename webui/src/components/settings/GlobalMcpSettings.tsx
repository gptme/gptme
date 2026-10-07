import { useEffect, useRef, useState } from 'react';
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
        args: JSON.stringify(server.args),

        original_name: server.name,
        rename_locked: [...Object.values(server.env), ...Object.values(server.headers)].includes(
          '***'
        ),
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
  return (
    <GlobalMcpForm
      key={JSON.stringify([api.baseUrl, api.authHeader])}
      baseUrl={api.baseUrl}
      authHeader={api.authHeader}
    />
  );
}

function GlobalMcpForm({ baseUrl, authHeader }: { baseUrl: string; authHeader: string | null }) {
  const active = useRef(true);
  const [config, setConfig] = useState<GlobalMcpConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const form = useForm<FormSchema>();
  const serverFields = useFieldArray({ control: form.control, name: 'mcp.servers' });
  const { isDirty, isSubmitting } = form.formState;

  useEffect(() => {
    const controller = new AbortController();
    active.current = true;
    setLoading(true);
    setConfig(null);
    setError(null);
    fetch(`${baseUrl}/api/v2/user/config/mcp`, {
      headers: authHeader ? { Authorization: authHeader } : {},
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
    return () => {
      active.current = false;
      controller.abort();
    };
  }, [baseUrl, authHeader, form]);

  const save = async (values: FormSchema) => {
    setError(null);
    try {
      const servers = (values.mcp.servers ?? []).map((server) => {
        if (server.rename_locked && server.name !== server.original_name) {
          throw new Error('Rename servers with hidden secrets using Config files.');
        }
        let args: unknown;
        try {
          args = JSON.parse(server.args || '[]');
        } catch {
          throw new Error('Arguments must be a JSON array of strings.');
        }
        if (!Array.isArray(args) || !args.every((arg) => typeof arg === 'string')) {
          throw new Error('Arguments must be a JSON array of strings.');
        }
        return {
          name: server.name,
          enabled: server.enabled,
          command: server.command,
          args,
          env: Object.fromEntries((server.env ?? []).map(({ key, value }) => [key, value])),
          url: server.url ?? '',
          headers: server.headers ?? {},
        };
      });
      const data = await readResponse(
        await fetch(`${baseUrl}/api/v2/user/config/mcp`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            ...(authHeader ? { Authorization: authHeader } : {}),
          },
          body: JSON.stringify({
            enabled: values.mcp.enabled,
            auto_start: values.mcp.auto_start,
            servers,
          }),
        })
      );
      if (!active.current) return;
      setConfig(data);
      form.reset(toForm(data));
      toast.success('Global MCP settings saved. Restart running sessions to apply changes.');
    } catch (err) {
      if (!active.current) return;
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
                argsFormat="json"
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
