import { useState } from 'react';
import type { FC } from 'react';
import { Server, ChevronDown, Check, Plus } from 'lucide-react';
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
import { serverRegistry$, setActiveServer, addServer } from '@/stores/servers';
import { toast } from 'sonner';

export const ServerSelector: FC = () => {
  const { connect } = useApi();
  const registry = use$(serverRegistry$);
  const activeServer = registry.servers.find((s) => s.id === registry.activeServerId);

  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [formState, setFormState] = useState({
    name: '',
    baseUrl: '',
    authToken: '',
    useAuthToken: false,
  });
  const [isSwitching, setIsSwitching] = useState(false);

  const handleSwitch = async (serverId: string) => {
    if (serverId === registry.activeServerId) return;

    const server = registry.servers.find((s) => s.id === serverId);
    if (!server) return;

    const previousId = registry.activeServerId;
    setIsSwitching(true);

    try {
      setActiveServer(serverId);
      await connect({
        baseUrl: server.baseUrl,
        authToken: server.authToken,
        useAuthToken: server.useAuthToken,
      });
    } catch {
      // Rollback on failed connection
      setActiveServer(previousId);
      toast.error(`Failed to connect to "${server.name}"`);
    } finally {
      setIsSwitching(false);
    }
  };

  const handleAdd = async () => {
    if (!formState.baseUrl.trim()) {
      toast.error('Server URL is required');
      return;
    }

    try {
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

      setAddDialogOpen(false);
      setFormState({ name: '', baseUrl: '', authToken: '', useAuthToken: false });

      // Switch to the new server
      await handleSwitch(server.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to add server');
    }
  };

  // Only show the selector if there are multiple servers
  if (registry.servers.length <= 1) {
    return null;
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="xs"
            className="text-muted-foreground"
            disabled={isSwitching}
          >
            <Server className="mr-1.5 h-3 w-3" />
            <span className="max-w-[120px] truncate text-xs">
              {activeServer?.name || 'Select server'}
            </span>
            <ChevronDown className="ml-1 h-3 w-3" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuLabel>Servers</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {registry.servers.map((server) => (
            <DropdownMenuItem
              key={server.id}
              onClick={() => handleSwitch(server.id)}
              className="flex items-center justify-between"
            >
              <div className="flex flex-col">
                <span className="text-sm">{server.name}</span>
                <span className="text-xs text-muted-foreground">{server.baseUrl}</span>
              </div>
              {server.id === registry.activeServerId && <Check className="ml-2 h-4 w-4 shrink-0" />}
            </DropdownMenuItem>
          ))}
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
                placeholder="e.g. Production, Staging"
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
