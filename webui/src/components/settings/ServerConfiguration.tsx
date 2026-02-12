import { useState } from 'react';
import type { FC } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Pencil, Trash2, Plus, Plug, Unplug, Copy } from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import {
  serverRegistry$,
  addServer,
  updateServer,
  removeServer,
  connectServer,
  disconnectServer,
} from '@/stores/servers';
import { getClientForServer } from '@/stores/serverClients';
import type { ServerConfig } from '@/types/servers';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';

interface ServerFormState {
  name: string;
  baseUrl: string;
  authToken: string;
  useAuthToken: boolean;
}

const emptyForm: ServerFormState = {
  name: '',
  baseUrl: '',
  authToken: '',
  useAuthToken: false,
};

function serverToForm(server: ServerConfig): ServerFormState {
  return {
    name: server.name,
    baseUrl: server.baseUrl,
    authToken: server.authToken || '',
    useAuthToken: server.useAuthToken,
  };
}

/** Status dot showing actual per-server connectivity. */
const ServerDot: FC<{ serverId: string; className?: string }> = ({ serverId, className }) => {
  const client = getClientForServer(serverId);
  const reachable = use$(client?.isConnected$ ?? null) ?? false;
  return (
    <span
      className={cn(
        'inline-block h-3 w-3 shrink-0 rounded-full',
        reachable ? 'bg-green-500' : 'bg-gray-400',
        className
      )}
    />
  );
};

export const ServerConfiguration: FC = () => {
  const { connect, switchServer } = useApi();
  const registry = use$(serverRegistry$);

  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingServerId, setEditingServerId] = useState<string | null>(null);
  const [formState, setFormState] = useState<ServerFormState>(emptyForm);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);

  const handleSetPrimary = async (serverId: string) => {
    if (serverId === registry.activeServerId) return;
    const wasInList = registry.connectedServerIds.includes(serverId);
    try {
      await switchServer(serverId);
    } catch {
      if (!wasInList) {
        disconnectServer(serverId);
      }
      const server = registry.servers.find((s) => s.id === serverId);
      toast.error(`Failed to connect to "${server?.name || 'server'}"`);
    }
  };

  const handleToggleConnection = async (serverId: string) => {
    const isInList = registry.connectedServerIds.includes(serverId);
    const server = registry.servers.find((s) => s.id === serverId);
    if (!server) return;

    if (isInList) {
      if (registry.connectedServerIds.length <= 1) {
        toast.error('At least one server must be connected');
        return;
      }
      // Clear the client's live connectivity status so the dot turns gray
      const client = getClientForServer(serverId);
      if (client) {
        client.setConnected(false);
      }
      disconnectServer(serverId);
      toast.success(`Disconnected from "${server.name}"`);
    } else {
      connectServer(serverId);
      if (!registry.connectedServerIds.includes(registry.activeServerId)) {
        try {
          await switchServer(serverId);
        } catch {
          disconnectServer(serverId);
          toast.error(`Failed to connect to "${server.name}"`);
          return;
        }
      } else {
        // Verify the server is reachable
        const client = getClientForServer(serverId);
        if (client) {
          const ok = await client.checkConnection();
          if (!ok) {
            disconnectServer(serverId);
            toast.error(`Failed to connect to "${server.name}"`);
            return;
          }
        }
      }
      toast.success(`Connected to "${server.name}"`);
    }
  };

  const handleOpenAdd = () => {
    setEditingServerId(null);
    setFormState(emptyForm);
    setEditDialogOpen(true);
  };

  const handleOpenEdit = (server: ServerConfig) => {
    setEditingServerId(server.id);
    setFormState(serverToForm(server));
    setEditDialogOpen(true);
  };

  const handleSave = async () => {
    if (!formState.baseUrl.trim()) {
      toast.error('Server URL is required');
      return;
    }

    try {
      if (editingServerId) {
        updateServer(editingServerId, {
          name: formState.name.trim() || 'Server',
          baseUrl: formState.baseUrl.trim(),
          authToken: formState.useAuthToken ? formState.authToken : null,
          useAuthToken: formState.useAuthToken,
        });
        toast.success('Server updated');

        if (editingServerId === registry.activeServerId) {
          await connect({
            baseUrl: formState.baseUrl.trim(),
            authToken: formState.useAuthToken ? formState.authToken : null,
            useAuthToken: formState.useAuthToken,
          });
        }
      } else {
        const server = addServer({
          name:
            formState.name.trim() ||
            (() => {
              try {
                return new URL(formState.baseUrl).hostname;
              } catch {
                return 'Server';
              }
            })(),
          baseUrl: formState.baseUrl.trim(),
          authToken: formState.useAuthToken ? formState.authToken : null,
          useAuthToken: formState.useAuthToken,
        });
        toast.success('Server added');

        await switchServer(server.id);
      }

      setEditDialogOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to save server');
    }
  };

  const handleDelete = async (serverId: string) => {
    const wasActive = serverId === registry.activeServerId;

    try {
      removeServer(serverId);
      setDeleteConfirmId(null);
      toast.success('Server removed');

      if (wasActive) {
        const newActive = serverRegistry$.get().servers[0];
        if (newActive) {
          try {
            await connect({
              baseUrl: newActive.baseUrl,
              authToken: newActive.authToken,
              useAuthToken: newActive.useAuthToken,
            });
          } catch {
            toast.error(`Failed to connect to "${newActive.name}"`);
          }
        }
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to remove server');
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="mb-1 text-lg font-medium">Servers</h3>
        <p className="mb-4 text-sm text-muted-foreground">
          Manage gptme server connections. Click a server to make it the primary connection.
        </p>
      </div>

      <div className="space-y-3">
        {registry.servers.map((server) => {
          const isPrimary = server.id === registry.activeServerId;
          const isInList = registry.connectedServerIds.includes(server.id);

          return (
            <div
              key={server.id}
              onClick={() => handleSetPrimary(server.id)}
              className={cn(
                'flex cursor-pointer items-center justify-between rounded-lg border p-3 transition-colors hover:bg-muted/40',
                isPrimary && 'border-primary/30 bg-primary/5'
              )}
            >
              <div className="flex items-center gap-3">
                <ServerDot serverId={server.id} />
                <div>
                  <div className="flex items-center gap-2 text-sm font-medium">
                    {server.name}
                    {isPrimary && (
                      <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                        primary
                      </span>
                    )}
                    {server.isPreset && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        preset
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground">{server.baseUrl}</div>
                  {server.useAuthToken && (
                    <div className="text-xs text-muted-foreground">Auth enabled</div>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-1">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleToggleConnection(server.id);
                      }}
                    >
                      {isInList ? (
                        <Unplug className="h-3.5 w-3.5 text-muted-foreground" />
                      ) : (
                        <Plug className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>{isInList ? 'Disconnect' : 'Connect'}</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleOpenEdit(server);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Edit</TooltipContent>
                </Tooltip>
                {deleteConfirmId === server.id ? (
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <Button
                      variant="destructive"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => handleDelete(server.id)}
                    >
                      Confirm
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => setDeleteConfirmId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteConfirmId(server.id);
                        }}
                        disabled={!!server.isPreset}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Delete</TooltipContent>
                  </Tooltip>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <Button variant="outline" onClick={handleOpenAdd}>
        <Plus className="mr-2 h-4 w-4" />
        Add Server
      </Button>

      <div className="space-y-2">
        <h4 className="text-sm font-medium">Start the server with:</h4>
        <div className="flex items-center gap-2 rounded-md bg-muted p-2">
          <code className="flex-1 text-sm">{`gptme-server --cors-origin='${window.location.origin}'`}</code>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0"
                onClick={() => {
                  navigator.clipboard.writeText(
                    `gptme-server --cors-origin='${window.location.origin}'`
                  );
                  toast.success('Command copied to clipboard');
                }}
              >
                <Copy className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Copy command</TooltipContent>
          </Tooltip>
        </div>
        <p className="text-xs text-muted-foreground">
          See the{' '}
          <a
            href="https://gptme.org/docs/server.html"
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-500 hover:underline"
          >
            server documentation
          </a>{' '}
          for more details.
        </p>
      </div>

      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editingServerId ? 'Edit Server' : 'Add Server'}</DialogTitle>
            <DialogDescription>
              {editingServerId
                ? 'Update server connection details.'
                : 'Add a new gptme server connection.'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="edit-server-name">Name</Label>
              <Input
                id="edit-server-name"
                value={formState.name}
                onChange={(e) => setFormState((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="e.g. Production, Staging"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-server-url">Server URL</Label>
              <Input
                id="edit-server-url"
                value={formState.baseUrl}
                onChange={(e) => setFormState((prev) => ({ ...prev, baseUrl: e.target.value }))}
                placeholder="http://127.0.0.1:5700"
              />
            </div>
            <div className="flex items-center space-x-2">
              <Checkbox
                id="edit-server-use-auth"
                checked={formState.useAuthToken}
                onCheckedChange={(checked) =>
                  setFormState((prev) => ({ ...prev, useAuthToken: checked === true }))
                }
              />
              <Label htmlFor="edit-server-use-auth" className="cursor-pointer text-sm">
                Add Authorization header
              </Label>
            </div>
            {formState.useAuthToken && (
              <div className="space-y-2">
                <Label htmlFor="edit-server-auth-token">User Token</Label>
                <Input
                  id="edit-server-auth-token"
                  value={formState.authToken}
                  onChange={(e) => setFormState((prev) => ({ ...prev, authToken: e.target.value }))}
                  placeholder="Your authentication token"
                />
              </div>
            )}
            <Button onClick={handleSave} className="w-full">
              {editingServerId ? 'Save Changes' : 'Add & Connect'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
};
