import { useState } from 'react';
import type { FC } from 'react';
import { ChevronDown, Plus, Unplug, Copy, Square, Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
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
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import { serverRegistry$, addServer, connectServer, disconnectServer } from '@/stores/servers';
import { getClientForServer } from '@/stores/serverClients';
import { settingsModal$ } from './SettingsModal';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';

/** Check actual connectivity for a server via its client in the pool. */
function useServerConnected(serverId: string): boolean {
  const client = getClientForServer(serverId);
  const connected = use$(client?.isConnected$ ?? null);
  return connected ?? false;
}

/** Tiny status dot component with per-server live connectivity. */
const ServerDot: FC<{ serverId: string; className?: string }> = ({ serverId, className }) => {
  const reachable = useServerConnected(serverId);
  return (
    <span
      className={cn(
        'inline-block h-2 w-2 shrink-0 rounded-full',
        reachable ? 'bg-green-500' : 'bg-gray-400',
        className
      )}
    />
  );
};

export const ServerSelector: FC = () => {
  const { switchServer, isConnected$, isConnecting$, isAutoConnecting$, stopAutoConnect } =
    useApi();
  const registry = use$(serverRegistry$);
  const isConnected = use$(isConnected$);
  const isConnecting = use$(isConnecting$);
  const isAutoConnecting = use$(isAutoConnecting$);

  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [formState, setFormState] = useState({
    name: '',
    baseUrl: '',
    authToken: '',
    useAuthToken: false,
  });

  const serverCommand = `gptme-server --cors-origin='${window.location.origin}'`;

  const copyCommand = () => {
    navigator.clipboard.writeText(serverCommand);
    toast.success('Command copied to clipboard');
  };

  /** Disconnect a server and clear its client's isConnected$ so the dot turns gray. */
  const handleDisconnect = (serverId: string) => {
    const server = registry.servers.find((s) => s.id === serverId);
    if (registry.connectedServerIds.length <= 1) {
      toast.error('At least one server must be connected');
      return;
    }
    // Clear the client's live connectivity status
    const client = getClientForServer(serverId);
    if (client) {
      client.setConnected(false);
    }
    disconnectServer(serverId);
    toast.success(`Disconnected from "${server?.name}"`);
  };

  /** Connect a server (verify reachability). Does NOT set it as primary. */
  const handleConnect = async (serverId: string) => {
    const server = registry.servers.find((s) => s.id === serverId);
    if (!server) return;

    connectServer(serverId);

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
    toast.success(`Connected to "${server.name}"`);
  };

  /** Row click: if not connected → connect. If connected but not primary → set primary. */
  const handleServerClick = async (serverId: string) => {
    const isInList = registry.connectedServerIds.includes(serverId);
    const isPrimary = serverId === registry.activeServerId;

    if (!isInList) {
      // First click: connect
      await handleConnect(serverId);
    } else if (!isPrimary) {
      // Second click: set as primary
      try {
        await switchServer(serverId);
      } catch {
        const server = registry.servers.find((s) => s.id === serverId);
        toast.error(`Failed to switch to "${server?.name || 'server'}"`);
      }
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

      await switchServer(server.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to add server');
    }
  };

  const activeServer = registry.servers.find((s) => s.id === registry.activeServerId);
  const label = activeServer?.name || 'Servers';

  // Trigger dot color: based on primary server's actual connectivity
  const dotColor = isConnected
    ? 'bg-green-500'
    : isConnecting || isAutoConnecting
      ? 'bg-yellow-500 animate-pulse'
      : 'bg-gray-400';

  const statusText = isConnected
    ? 'Connected'
    : isConnecting
      ? 'Connecting...'
      : isAutoConnecting
        ? 'Auto-connecting...'
        : 'Disconnected';

  // Show command help when primary is disconnected
  const showCommandHelp = !isConnected && !isConnecting && !isAutoConnecting;

  return (
    <>
      <DropdownMenu>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="xs" className="text-muted-foreground">
                <span className={cn('mr-1.5 inline-block h-2 w-2 rounded-full', dotColor)} />
                <span className="max-w-[120px] truncate text-xs">{label}</span>
                <ChevronDown className="ml-1 h-3 w-3" />
              </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom">{statusText}</TooltipContent>
        </Tooltip>
        <DropdownMenuContent align="end" className="w-72">
          <div className="flex items-center justify-between px-2 py-1.5">
            <span className="text-sm font-semibold">Servers</span>
            <div className="flex items-center gap-0.5">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6"
                    onClick={() => setAddDialogOpen(true)}
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Add server</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6"
                    onClick={() => settingsModal$.set({ open: true, category: 'servers' })}
                  >
                    <Settings className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Server settings</TooltipContent>
              </Tooltip>
            </div>
          </div>
          <DropdownMenuSeparator />

          {registry.servers.map((server) => {
            const isInList = registry.connectedServerIds.includes(server.id);
            const isPrimary = server.id === registry.activeServerId;

            // Tooltip text depends on state
            const rowTooltip = !isInList
              ? 'Connect'
              : isPrimary
                ? 'Primary server'
                : 'Set as primary';

            return (
              <DropdownMenuItem
                key={server.id}
                className="flex items-center justify-between"
                onSelect={(e) => e.preventDefault()}
              >
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      className="flex flex-1 cursor-pointer items-center gap-2 text-left"
                      onClick={() => handleServerClick(server.id)}
                    >
                      <ServerDot serverId={server.id} />
                      <div className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5 text-sm">
                          {server.name}
                          {isPrimary && (
                            <span className="rounded bg-primary/10 px-1 text-[10px] text-primary">
                              primary
                            </span>
                          )}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {server.baseUrl}
                        </span>
                      </div>
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="left">{rowTooltip}</TooltipContent>
                </Tooltip>
                {isInList && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="ml-2 h-7 w-7 shrink-0"
                        onClick={() => handleDisconnect(server.id)}
                      >
                        <Unplug className="h-3.5 w-3.5 text-muted-foreground" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="right">Disconnect</TooltipContent>
                  </Tooltip>
                )}
              </DropdownMenuItem>
            );
          })}

          {isAutoConnecting && (
            <>
              <DropdownMenuSeparator />
              <div className="flex items-center justify-between px-2 py-1.5">
                <span className="flex items-center gap-1.5 text-xs text-yellow-600">
                  <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-yellow-500" />
                  Auto-connecting...
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-yellow-700 hover:text-yellow-900 dark:text-yellow-300"
                  onClick={stopAutoConnect}
                >
                  <Square className="mr-1 h-3 w-3" />
                  Stop
                </Button>
              </div>
            </>
          )}

          {showCommandHelp && (
            <>
              <DropdownMenuSeparator />
              <div className="px-2 py-1.5">
                <p className="mb-1 text-xs text-muted-foreground">Start the server with:</p>
                <div className="flex items-center gap-1 rounded bg-muted px-2 py-1">
                  <code className="flex-1 truncate text-[11px]">{serverCommand}</code>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 shrink-0"
                        onClick={copyCommand}
                      >
                        <Copy className="h-3 w-3" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Copy command</TooltipContent>
                  </Tooltip>
                </div>
              </div>
            </>
          )}
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
