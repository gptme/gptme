import { useState } from 'react';
import type { FC } from 'react';
import { Server, ChevronDown, Plus, Plug, Unplug } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import { serverRegistry$, addServer, connectServer, disconnectServer } from '@/stores/servers';
import { toast } from 'sonner';

export const ServerSelector: FC = () => {
  const { switchServer } = useApi();
  const registry = use$(serverRegistry$);
  const connectedCount = registry.connectedServerIds.length;

  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [formState, setFormState] = useState({
    name: '',
    baseUrl: '',
    authToken: '',
    useAuthToken: false,
  });

  const handleToggleConnection = async (serverId: string) => {
    const isConnected = registry.connectedServerIds.includes(serverId);
    const server = registry.servers.find((s) => s.id === serverId);
    if (!server) return;

    if (isConnected) {
      // Don't disconnect the last server
      if (connectedCount <= 1) {
        toast.error('At least one server must be connected');
        return;
      }
      disconnectServer(serverId);
      toast.success(`Disconnected from "${server.name}"`);
    } else {
      connectServer(serverId);

      // If no primary is connected, make this the primary via atomic switch
      if (!registry.connectedServerIds.includes(registry.activeServerId)) {
        try {
          await switchServer(serverId);
        } catch {
          disconnectServer(serverId);
          toast.error(`Failed to connect to "${server.name}"`);
          return;
        }
      }
      toast.success(`Connected to "${server.name}"`);
    }
  };

  const handleSetPrimary = async (serverId: string) => {
    if (serverId === registry.activeServerId) return;
    try {
      await switchServer(serverId);
    } catch {
      const server = registry.servers.find((s) => s.id === serverId);
      toast.error(`Failed to connect to "${server?.name || 'server'}"`);
    }
  };

  const handleAdd = async () => {
    if (!formState.baseUrl.trim()) {
      toast.error('Server URL is required');
      return;
    }

    try {
      const name =
        formState.name.trim() ||
        (() => {
          try {
            return new URL(formState.baseUrl).hostname;
          } catch {
            return 'Server';
          }
        })();

      const server = addServer({
        name,
        baseUrl: formState.baseUrl.trim(),
        authToken: formState.useAuthToken ? formState.authToken : null,
        useAuthToken: formState.useAuthToken,
      });

      setAddDialogOpen(false);
      setFormState({ name: '', baseUrl: '', authToken: '', useAuthToken: false });

      // Connect and make primary (switchServer handles connectServer internally)
      await switchServer(server.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to add server');
    }
  };

  // Always show — the dropdown is useful for managing connections
  const label =
    connectedCount > 1
      ? `${connectedCount} servers`
      : registry.servers.find((s) => s.id === registry.activeServerId)?.name || 'Servers';

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="xs" className="text-muted-foreground">
            <Server className="mr-1.5 h-3 w-3" />
            <span className="max-w-[120px] truncate text-xs">{label}</span>
            <ChevronDown className="ml-1 h-3 w-3" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuLabel>Servers</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {registry.servers.map((server) => {
            const isConnected = registry.connectedServerIds.includes(server.id);
            const isPrimary = server.id === registry.activeServerId;

            return (
              <DropdownMenuItem
                key={server.id}
                className="flex items-center justify-between"
                onSelect={(e) => e.preventDefault()}
              >
                <button
                  className="flex flex-1 cursor-pointer flex-col text-left"
                  onClick={() => handleSetPrimary(server.id)}
                >
                  <span className="flex items-center gap-1.5 text-sm">
                    {server.name}
                    {isPrimary && (
                      <span className="rounded bg-primary/10 px-1 text-[10px] text-primary">
                        primary
                      </span>
                    )}
                  </span>
                  <span className="text-xs text-muted-foreground">{server.baseUrl}</span>
                </button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="ml-2 h-7 w-7 shrink-0"
                  onClick={() => handleToggleConnection(server.id)}
                >
                  {isConnected ? (
                    <Unplug className="h-3.5 w-3.5 text-green-600" />
                  ) : (
                    <Plug className="h-3.5 w-3.5 text-muted-foreground" />
                  )}
                </Button>
              </DropdownMenuItem>
            );
          })}
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setAddDialogOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Add Server
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add Server</DialogTitle>
            <DialogDescription>Add a new gptme server connection.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="server-name">Name</Label>
              <Input
                id="server-name"
                value={formState.name}
                onChange={(e) => setFormState((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="e.g. bob-vm, staging"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="server-url">Server URL</Label>
              <Input
                id="server-url"
                value={formState.baseUrl}
                onChange={(e) => setFormState((prev) => ({ ...prev, baseUrl: e.target.value }))}
                placeholder="http://127.0.0.1:5700"
              />
            </div>
            <div className="flex items-center space-x-2">
              <Checkbox
                id="server-use-auth"
                checked={formState.useAuthToken}
                onCheckedChange={(checked) =>
                  setFormState((prev) => ({ ...prev, useAuthToken: checked === true }))
                }
              />
              <Label htmlFor="server-use-auth" className="cursor-pointer text-sm">
                Add Authorization header
              </Label>
            </div>
            {formState.useAuthToken && (
              <div className="space-y-2">
                <Label htmlFor="server-auth-token">User Token</Label>
                <Input
                  id="server-auth-token"
                  value={formState.authToken}
                  onChange={(e) => setFormState((prev) => ({ ...prev, authToken: e.target.value }))}
                  placeholder="Your authentication token"
                />
              </div>
            )}
            <Button onClick={handleAdd} className="w-full">
              Add & Connect
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
};
