import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Clipboard, Check } from 'lucide-react';
import { highlightCode } from '@/utils/highlightUtils';
// We still need the CSS though
import 'highlight.js/styles/github-dark.css';

interface CodeDisplayProps {
  code: string;
  maxHeight?: string;
  showLineNumbers?: boolean;
  language?: string;
}

export function CodeDisplay({
  code,
  maxHeight = '300px',
  showLineNumbers = true,
  language,
}: CodeDisplayProps) {
  const [copied, setCopied] = useState(false);
  const [highlightedCode, setHighlightedCode] = useState('');

  useEffect(() => {
    if (!code) return;

    // Use our shared utility
    setHighlightedCode(highlightCode(code, language, true, 1000).code);
  }, [code, language]);

  if (!code) return null;

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const lines = code.split('\n');

  return (
    <div className="relative overflow-hidden rounded bg-muted">
      <div className="absolute right-1 z-10">
        <Button
          variant="ghost"
          size="sm"
          onClick={handleCopy}
          className="h-10 w-10 p-1 opacity-50 hover:opacity-100"
        >
          {copied ? <Check size={16} /> : <Clipboard className="drop-shadow-md" size={16} />}
        </Button>
      </div>

      {/* Single scrollable container for both line numbers and code */}
      <div className="overflow-auto text-xs" style={{ maxHeight }}>
        <div className="flex">
          {showLineNumbers && lines.length > 1 && (
            <div className="min-w-12 flex-none select-none border-r border-muted-foreground/20 bg-muted/50 pr-1 text-right font-mono text-muted-foreground">
              {lines.map((_, i) => (
                <div key={i} className="px-2 text-xs leading-6">
                  {i + 1}
                </div>
              ))}
            </div>
          )}

          <div className="flex-1">
            {highlightedCode ? (
              <pre className="whitespace-pre px-4 leading-6">
                <code dangerouslySetInnerHTML={{ __html: highlightedCode }} />
              </pre>
            ) : (
              <pre className="whitespace-pre px-4 leading-6">{code}</pre>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
