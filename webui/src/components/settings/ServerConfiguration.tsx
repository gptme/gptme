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
import { Pencil, Trash2, Plus, Check } from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import {
  serverRegistry$,
  setActiveServer,
  addServer,
  updateServer,
  removeServer,
} from '@/stores/servers';
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

export const ServerConfiguration: FC = () => {
  const { connect } = useApi();
  const registry = use$(serverRegistry$);

  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingServerId, setEditingServerId] = useState<string | null>(null);
  const [formState, setFormState] = useState<ServerFormState>(emptyForm);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);

  const handleSwitchActive = async (serverId: string) => {
    if (serverId === registry.activeServerId) return;

    const server = registry.servers.find((s) => s.id === serverId);
    if (!server) return;

    const previousId = registry.activeServerId;
    setActiveServer(serverId);
    try {
      await connect({
        baseUrl: server.baseUrl,
        authToken: server.authToken,
        useAuthToken: server.useAuthToken,
      });
    } catch {
      // Rollback on failed connection
      setActiveServer(previousId);
      toast.error(`Failed to connect to "${server.name}"`);
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
        // Editing existing server
        updateServer(editingServerId, {
          name: formState.name.trim() || 'Server',
          baseUrl: formState.baseUrl.trim(),
          authToken: formState.useAuthToken ? formState.authToken : null,
          useAuthToken: formState.useAuthToken,
        });
        toast.success('Server updated');

        // Reconnect if we edited the active server
        if (editingServerId === registry.activeServerId) {
          await connect({
            baseUrl: formState.baseUrl.trim(),
            authToken: formState.useAuthToken ? formState.authToken : null,
            useAuthToken: formState.useAuthToken,
          });
        }
      } else {
        // Adding new server
        const server = addServer({
          name: formState.name.trim() || (() => { try { return new URL(formState.baseUrl).hostname; } catch { return 'Server'; } })(),
          baseUrl: formState.baseUrl.trim(),
          authToken: formState.useAuthToken ? formState.authToken : null,
          useAuthToken: formState.useAuthToken,
        });
        toast.success('Server added');

        // Switch to the new server
        setActiveServer(server.id);
        await connect({
          baseUrl: server.baseUrl,
          authToken: server.authToken,
          useAuthToken: server.useAuthToken,
        });
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

      // Reconnect if we deleted the active server
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
          Manage gptme server connections. Click a server to make it active.
        </p>
      </div>

      <div className="space-y-3">
        {registry.servers.map((server) => {
          const isActive = server.id === registry.activeServerId;

          return (
            <div
              key={server.id}
              onClick={() => handleSwitchActive(server.id)}
              className={cn(
                'flex cursor-pointer items-center justify-between rounded-lg border p-3 transition-colors hover:bg-muted/40',
                isActive && 'border-green-500/50 bg-green-50/50 dark:bg-green-950/20'
              )}
            >
              <div className="flex items-center gap-3">
                {isActive && <Check className="h-4 w-4 shrink-0 text-green-600" />}
                <div className={cn(!isActive && 'ml-7')}>
                  <div className="text-sm font-medium">{server.name}</div>
                  <div className="text-xs text-muted-foreground">{server.baseUrl}</div>
                  {server.useAuthToken && (
                    <div className="text-xs text-muted-foreground">Auth enabled</div>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-1">
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
                {deleteConfirmId === server.id ? (
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <Button
                      variant="destructive"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => handleDelete(server.id)}
                      disabled={registry.servers.length <= 1}
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
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={(e) => {
                      e.stopPropagation();
                      setDeleteConfirmId(server.id);
                    }}
                    disabled={registry.servers.length <= 1}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
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
