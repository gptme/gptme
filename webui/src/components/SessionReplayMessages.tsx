import { type FC, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Terminal, User, Bot } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import type { NormalizedMessage } from '@/types/api';

const MAX_TOOL_RESULT_CHARS = 500;

function formatBytes(chars: number): string {
  return chars < 1024 ? `${chars}B` : `${(chars / 1024).toFixed(1)}KB`;
}

const ToolCallRow: FC<{ message: NormalizedMessage }> = ({ message }) => {
  const [open, setOpen] = useState(false);
  const hasInput = !!message.tool_input && Object.keys(message.tool_input).length > 0;

  return (
    <div className="mt-1 rounded-md border bg-muted/30 px-2 py-1 text-xs">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <button
            className="flex w-full items-center gap-1.5 text-left text-muted-foreground hover:text-foreground"
            disabled={!hasInput}
          >
            <Terminal className="h-3 w-3 flex-shrink-0" />
            <span className="font-mono">{message.tool_name}</span>
            {hasInput && (
              <span className="ml-auto">
                {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              </span>
            )}
          </button>
        </CollapsibleTrigger>
        {hasInput && (
          <CollapsibleContent>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono text-[11px]">
              {JSON.stringify(message.tool_input, null, 2)}
            </pre>
          </CollapsibleContent>
        )}
      </Collapsible>
    </div>
  );
};

const ToolResultRow: FC<{ message: NormalizedMessage }> = ({ message }) => {
  const text = message.tool_result ?? message.content ?? '';
  const isLong = text.length > MAX_TOOL_RESULT_CHARS;
  const [expanded, setExpanded] = useState(false);
  const shown = expanded || !isLong ? text : `${text.slice(0, MAX_TOOL_RESULT_CHARS)}…`;

  return (
    <div
      className={`mt-1 rounded-md border px-2 py-1 text-xs ${
        message.is_error ? 'border-destructive/50 bg-destructive/5' : 'bg-muted/20'
      }`}
    >
      <div className="mb-1 flex items-center gap-1.5 text-muted-foreground">
        <span className="font-mono">tool result</span>
        {message.is_error && (
          <Badge variant="destructive" className="h-4 gap-0.5 px-1 text-[10px]">
            <AlertTriangle className="h-2.5 w-2.5" />
            error
          </Badge>
        )}
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[11px]">{shown}</pre>
      {isLong && (
        <button
          className="mt-1 text-[11px] text-primary hover:underline"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? 'Show less' : `Show more (${formatBytes(text.length)})`}
        </button>
      )}
    </div>
  );
};

const MessageRow: FC<{ message: NormalizedMessage }> = ({ message }) => {
  if (message.role === 'tool_result') {
    return <ToolResultRow message={message} />;
  }

  const isUser = message.role === 'user';
  return (
    <div
      className={`rounded-md border-l-2 px-3 py-2 ${
        isUser ? 'border-l-blue-500 bg-blue-500/5' : 'border-l-muted-foreground/30'
      }`}
    >
      <div className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
        {isUser ? <User className="h-3 w-3" /> : <Bot className="h-3 w-3" />}
        <span className="font-medium">{isUser ? 'User' : 'Assistant'}</span>
        {message.timestamp && (
          <span className="text-[10px] opacity-70">
            {new Date(message.timestamp).toLocaleTimeString()}
          </span>
        )}
      </div>
      {message.content && <p className="whitespace-pre-wrap text-sm">{message.content}</p>}
      {message.tool_name && <ToolCallRow message={message} />}
    </div>
  );
};

const SystemPrelude: FC<{ messages: NormalizedMessage[] }> = ({ messages }) => {
  const [open, setOpen] = useState(false);
  const totalChars = messages.reduce((sum, m) => sum + (m.content?.length ?? 0), 0);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 gap-1.5 text-xs">
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          {open ? 'Hide' : 'Show'} {messages.length} system message
          {messages.length !== 1 ? 's' : ''} ({formatBytes(totalChars)})
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2">
        {messages.map((m, i) => (
          <MessageRow key={i} message={m} />
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
};

/** Renders a normalized session transcript: leading system messages collapsed
 * into a toggle, remaining messages rendered with legible tool call/result rows. */
export const SessionReplayMessages: FC<{ messages: NormalizedMessage[] }> = ({ messages }) => {
  let prefixEnd = 0;
  while (prefixEnd < messages.length && messages[prefixEnd].role === 'system') {
    prefixEnd++;
  }
  const prelude = messages.slice(0, prefixEnd);
  const rest = messages.slice(prefixEnd);

  return (
    <div className="space-y-2">
      {prelude.length > 0 && <SystemPrelude messages={prelude} />}
      {rest.map((m, i) => (
        <MessageRow key={prefixEnd + i} message={m} />
      ))}
    </div>
  );
};
