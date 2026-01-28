import { useState } from 'react';
import type { FC } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogDescription,
} from '@/components/ui/dialog';
import { Network, Check, Copy } from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';
import { cn } from '@/lib/utils';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { toast } from 'sonner';
import { use$ } from '@legendapp/state/react';

export const ConnectionButton: FC = () => {
  const [open, setOpen] = useState(false);
  const {
    connectionConfig,
    connect,
    isConnected$,
    isConnecting$,
    isAutoConnecting$,
    stopAutoConnect,
  } = useApi();
  const [formState, setFormState] = useState({
    baseUrl: connectionConfig.baseUrl,
    authToken: connectionConfig.authToken || '',
    useAuthToken: connectionConfig.useAuthToken,
  });

  const isConnected = use$(isConnected$);
  const isConnecting = use$(isConnecting$);
  const isAutoConnecting = use$(isAutoConnecting$);
  const features = [
    'Create new conversations',
    'Access conversation history',
    'Generate AI responses',
  ];

  const serverCommand = `gptme-server --cors-origin='${window.location.origin}'`;

  const copyCommand = () => {
    navigator.clipboard.writeText(serverCommand);
    toast.success('Command copied to clipboard');
  };

  const onChangeAuthToken = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    if (value && value.startsWith('Bearer ')) {
      const token = value.replace('Bearer ', '');
      setFormState((prev) => ({ ...prev, authToken: token }));
      toast('Detected "Bearer" prefix, it is now automatically removed', {
        description: 'Token format updated',
      });
      return;
    }
    setFormState((prev) => ({ ...prev, authToken: value }));
  };

  const handleConnect = async () => {
    try {
      await connect({
        baseUrl: formState.baseUrl,
        authToken: formState.useAuthToken ? formState.authToken : null,
        useAuthToken: formState.useAuthToken,
      });
      setOpen(false);
    } catch (error) {
      // Error handling is now done in the ApiContext
      console.error('Connection error:', error);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="xs"
          className={cn(
            isConnected ? 'text-green-600' : 'text-muted-foreground',
            (isConnecting || isAutoConnecting) && 'text-yellow-600'
          )}
        >
          <Network className="mr-2 h-3 w-3" />
          {isConnected
            ? 'Connected'
            : isConnecting
              ? 'Connecting...'
              : isAutoConnecting
                ? 'Auto-connecting...'
                : 'Connect'}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Connect to gptme</DialogTitle>
          <DialogDescription>
            Connect to a gptme instance to enable advanced features and AI interactions. See the{' '}
            <a
              href="https://gptme.org/docs/server.html"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-500 hover:underline"
            >
              server documentation
            </a>{' '}
            for more details.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-6">
          <div className="space-y-2">
            <label htmlFor="url" className="text-sm font-medium">
              Server URL
            </label>
            <Input
              id="url"
              value={formState.baseUrl}
              onChange={(e) => setFormState((prev) => ({ ...prev, baseUrl: e.target.value }))}
              placeholder="http://127.0.0.1:5700"
            />
          </div>

          <div className="flex items-center space-x-2">
            <Checkbox
              id="use-auth"
              checked={formState.useAuthToken}
              onCheckedChange={(checked) =>
                setFormState((prev) => ({ ...prev, useAuthToken: checked === true }))
              }
            />
            <Label htmlFor="use-auth" className="cursor-pointer text-sm font-medium">
              Add Authorization header
            </Label>
          </div>

          {formState.useAuthToken && (
            <div className="space-y-2">
              <label htmlFor="auth-token" className="text-sm font-medium">
                User Token
              </label>
              <Input
                id="auth-token"
                value={formState.authToken}
                onChange={onChangeAuthToken}
                placeholder="Your authentication token"
              />
              <p className="text-xs text-muted-foreground">
                Will be sent as: Authorization: Bearer "[user token]"
              </p>
            </div>
          )}

          <div className="space-y-2">
            <label className="text-sm font-medium">Start the server with:</label>
            <div className="flex items-center gap-2 rounded-md bg-muted p-2">
              <code className="flex-1 text-sm">{serverCommand}</code>
              <Button variant="ghost" size="icon" onClick={copyCommand} className="h-8 w-8">
                <Copy className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {isAutoConnecting && (
            <div className="space-y-2 rounded-md bg-yellow-50 p-3 dark:bg-yellow-950/20">
              <div className="flex items-center justify-between">
                <div className="flex items-center">
                  <Network className="mr-2 h-4 w-4 animate-pulse text-yellow-600" />
                  <span className="text-sm font-medium text-yellow-800 dark:text-yellow-200">
                    Auto-connecting to server...
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={stopAutoConnect}
                  className="text-yellow-700 hover:text-yellow-900 dark:text-yellow-300 dark:hover:text-yellow-100"
                >
                  Stop
                </Button>
              </div>
              <p className="text-xs text-yellow-700 dark:text-yellow-300">
                The server is starting up. This may take a few seconds.
              </p>
            </div>
          )}

          <div className="space-y-3">
            <h4 className="text-sm font-semibold">Features enabled by connecting:</h4>
            <ul className="space-y-2">
              {features.map((feature, index) => (
                <li key={index} className="flex items-center text-sm">
                  <Check
                    className={cn('mr-2 h-4 w-4', isConnected ? 'text-green-500' : 'text-gray-300')}
                  />
                  <span className="text-foreground">{feature}</span>
                </li>
              ))}
            </ul>
          </div>

          <Button
            onClick={handleConnect}
            className={cn('w-full', isConnected && 'bg-green-600 hover:bg-green-700')}
            disabled={isConnecting || isAutoConnecting}
          >
            {isConnected
              ? 'Reconnect'
              : isConnecting
                ? 'Connecting...'
                : isAutoConnecting
                  ? 'Auto-connecting...'
                  : 'Connect'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
};
