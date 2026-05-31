import type { FC } from 'react';
import { useRef, useEffect } from 'react';
import { Search, X, ChevronUp, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface Props {
  query: string;
  matchCount: number;
  currentMatch: number; // 1-based, 0 when no matches/empty query
  onQueryChange: (q: string) => void;
  onNext: () => void;
  onPrev: () => void;
  onClose: () => void;
}

export const MessageSearchBar: FC<Props> = ({
  query,
  matchCount,
  currentMatch,
  onQueryChange,
  onNext,
  onPrev,
  onClose,
}) => {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      if (e.shiftKey) onPrev();
      else onNext();
    } else if (e.key === 'Escape') {
      onClose();
    }
  };

  return (
    <div className="flex items-center gap-1.5 border-b bg-background px-3 py-2">
      <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
      <Input
        ref={inputRef}
        className="h-7 flex-1 border-none bg-transparent p-0 text-sm shadow-none focus-visible:ring-0"
        placeholder="Search messages…"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        onKeyDown={handleKeyDown}
      />
      {query.length > 0 && (
        <span className="min-w-[3.5rem] text-center text-xs text-muted-foreground">
          {matchCount === 0 ? 'No matches' : `${currentMatch}/${matchCount}`}
        </span>
      )}
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        onClick={onPrev}
        disabled={matchCount === 0}
        title="Previous match (Shift+Enter)"
      >
        <ChevronUp className="h-4 w-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        onClick={onNext}
        disabled={matchCount === 0}
        title="Next match (Enter)"
      >
        <ChevronDown className="h-4 w-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        onClick={onClose}
        title="Close search (Escape)"
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
};
