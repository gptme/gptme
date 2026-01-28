import { RefreshCw, Smartphone, Monitor, Terminal, Globe } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useState, useEffect, useRef } from 'react';
import { consoleProxyScript } from '@/utils/consoleProxy';
import { ScrollArea } from '@/components/ui/scroll-area';
import type { FC } from 'react';

interface ConsoleMessage {
  level: 'log' | 'info' | 'warn' | 'error' | 'debug';
  args: unknown[];
  timestamp: number;
}

interface Props {
  defaultUrl?: string;
}

export const BrowserPreview: FC<Props> = ({ defaultUrl = 'http://localhost:8080' }) => {
  const [inputValue, setInputValue] = useState(defaultUrl);
  const [currentUrl, setCurrentUrl] = useState(defaultUrl);
  const [isMobile, setIsMobile] = useState(false);
  const [showConsole, setShowConsole] = useState(true);
  const [logs, setLogs] = useState<ConsoleMessage[]>([]);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Clear logs when URL changes
  useEffect(() => {
    setLogs([]);
  }, [currentUrl]);

  const handleRefresh = () => {
    // Force refresh by appending a dummy parameter if URL is unchanged
    setCurrentUrl(
      inputValue === currentUrl
        ? `${inputValue}${inputValue.includes('?') ? '&' : '?'}_refresh=${Date.now()}`
        : inputValue
    );
    setLogs([]); // Clear logs on refresh
  };

  const toggleMode = () => {
    setIsMobile((prev) => !prev);
  };

  const toggleConsole = () => {
    setShowConsole((prev) => !prev);
  };

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.data?.type === 'console') {
        setLogs((prev) => [
          ...prev,
          {
            level: event.data.level,
            args: event.data.args,
            timestamp: Date.now(),
          },
        ]);
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  // Inject console proxy script when iframe loads
  // NOTE: only works with same-origin URLs (we need a workaround to capture logs from cross-origin iframes)
  const handleIframeLoad = () => {
    const iframe = iframeRef.current;
    if (iframe?.contentWindow) {
      // Use Function constructor instead of eval for better type safety
      const script = new Function(consoleProxyScript);
      iframe.contentWindow.document.head.appendChild(
        Object.assign(iframe.contentWindow.document.createElement('script'), {
          textContent: `(${script.toString()})();`,
        })
      );
    }
  };

  const clearLogs = () => {
    setLogs([]);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b p-2">
        <div className="relative flex-1">
          <Globe className="absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleRefresh();
              }
            }}
            className="flex-1 pl-8"
          />
        </div>
        <Button variant="ghost" size="icon" onClick={handleRefresh} title="Refresh">
          <RefreshCw className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleMode}
          title={isMobile ? 'Switch to desktop' : 'Switch to mobile'}
        >
          {isMobile ? <Smartphone className="h-4 w-4" /> : <Monitor className="h-4 w-4" />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleConsole}
          title={showConsole ? 'Hide Console' : 'Show Console'}
        >
          <Terminal className="h-4 w-4" />
        </Button>
      </div>
      <div className={`relative flex-1 ${showConsole ? 'h-[60%]' : 'h-full'}`}>
        <div className="h-full border border-foreground/10 bg-muted/30 p-1">
          <iframe
            ref={iframeRef}
            src={currentUrl}
            onLoad={handleIframeLoad}
            className={`h-full w-full rounded-sm bg-background shadow-md ${
              isMobile ? 'mx-auto w-[375px]' : 'w-full'
            }`}
            title="Browser Preview"
            // sandbox="allow-scripts allow-same-origin"
          />
        </div>
      </div>
      {showConsole && (
        <div className="h-[40%] border-t">
          <div className="flex items-center justify-between border-b px-2 py-1">
            <span className="text-sm font-medium">Console</span>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={clearLogs}>
                Clear
              </Button>
            </div>
          </div>
          <ScrollArea className="h-[calc(100%-2rem)]">
            <div className="space-y-1 p-2">
              {logs.map((log, i) => (
                <div
                  key={i}
                  className={`font-mono text-sm ${
                    log.level === 'error'
                      ? 'text-red-500'
                      : log.level === 'warn'
                        ? 'text-yellow-500'
                        : 'text-foreground'
                  }`}
                >
                  {log.args.map((arg, j) => (
                    <span key={j}>
                      {typeof arg === 'object' ? JSON.stringify(arg, null, 2) : String(arg)}{' '}
                    </span>
                  ))}
                </div>
              ))}
            </div>
          </ScrollArea>
        </div>
      )}
    </div>
  );
};
