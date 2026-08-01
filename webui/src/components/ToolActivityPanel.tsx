import { type FC, useMemo } from 'react';
import { use$ } from '@legendapp/state/react';
import { Terminal, FileText, Code, Globe, Monitor, Wrench, ChevronDown } from 'lucide-react';
import { conversations$ } from '@/stores/conversations';
import { buildToolActivity, type ToolActivityEntry } from '@/utils/toolActivity';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';

interface Props {
  conversationId: string;
}

function toolIcon(tool: string) {
  switch (tool) {
    case 'bash':
    case 'shell':
    case 'tmux':
      return <Terminal className="h-4 w-4 shrink-0" />;
    case 'python':
    case 'ipython':
      return <Code className="h-4 w-4 shrink-0" />;
    case 'save':
    case 'append':
    case 'read':
    case 'patch':
      return <FileText className="h-4 w-4 shrink-0" />;
    case 'browser':
      return <Globe className="h-4 w-4 shrink-0" />;
    case 'computer':
    case 'screenshot':
      return <Monitor className="h-4 w-4 shrink-0" />;
    default:
      return <Wrench className="h-4 w-4 shrink-0" />;
  }
}

function formatTimestamp(ts?: string): string {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function truncate(s: string, maxLen: number): string {
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen) + '…';
}

const ToolEntryRow: FC<{ entry: ToolActivityEntry }> = ({ entry }) => {
  const previewLine = entry.lastCall.content.split('\n')[0].trim();
  const args = entry.lastCall.args.join(' ');
  const subtitle = args || previewLine;

  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted/50">
        {toolIcon(entry.tool)}
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-1">
            <span className="font-mono text-sm font-medium">{entry.tool}</span>
            <div className="flex shrink-0 items-center gap-1">
              <Badge variant="secondary" className="h-4 px-1 text-xs">
                {entry.callCount}
              </Badge>
              <ChevronDown className="h-3 w-3 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
            </div>
          </div>
          {subtitle && (
            <p className="truncate text-xs text-muted-foreground">{truncate(subtitle, 48)}</p>
          )}
        </div>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mx-2 mb-1 rounded-md bg-muted px-2 py-1.5 text-xs">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-muted-foreground">Last call</span>
            <span className="text-muted-foreground">
              {formatTimestamp(entry.lastCall.timestamp)}
            </span>
          </div>
          {entry.lastCall.args.length > 0 && (
            <div className="mb-1">
              <span className="text-muted-foreground">Args: </span>
              <code className="text-foreground">{entry.lastCall.args.join(' ')}</code>
            </div>
          )}
          {entry.lastCall.content && (
            <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-foreground">
              {truncate(entry.lastCall.content.trim(), 200)}
            </pre>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
};

export const ToolActivityPanel: FC<Props> = ({ conversationId }) => {
  const conversation = conversations$.get(conversationId);
  const log = use$(conversation?.data.log);
  const hasMoreBefore = use$(conversation?.hasMoreBefore) ?? false;
  const activity = useMemo(() => buildToolActivity(log ?? []), [log]);

  if (!log || log.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-muted-foreground">
        <Wrench className="h-8 w-8 opacity-40" />
        <p className="text-sm">No messages yet</p>
      </div>
    );
  }

  if (activity.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-muted-foreground">
        <Wrench className="h-8 w-8 opacity-40" />
        <p className="text-sm">
          {hasMoreBefore ? 'No tool calls in loaded messages' : 'No tool calls in this session'}
        </p>
        {hasMoreBefore && (
          <p className="text-xs">Load older messages in the conversation to include them.</p>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b px-3 py-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">Tool Activity</h3>
          <Badge variant="outline" className="text-xs">
            {activity.reduce((s, e) => s + e.callCount, 0)} calls
          </Badge>
        </div>
        {hasMoreBefore && (
          <p className="mt-1 text-xs text-muted-foreground">Loaded messages only</p>
        )}
      </div>
      <ScrollArea className="flex-1">
        <div className="p-1">
          {activity.map((entry) => (
            <ToolEntryRow key={entry.tool} entry={entry} />
          ))}
        </div>
      </ScrollArea>
    </div>
  );
};
