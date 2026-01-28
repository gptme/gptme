import { type FC, useEffect, useRef } from 'react';
import { File, Folder } from 'lucide-react';
import type { FileType } from '@/types/workspace';
import { cn } from '@/lib/utils';

interface FileAutocompleteProps {
  files: FileType[];
  selectedIndex: number;
  onSelect: (file: FileType) => void;
  onHover: (index: number) => void;
  isOpen: boolean;
  query: string;
}

export const FileAutocomplete: FC<FileAutocompleteProps> = ({
  files,
  selectedIndex,
  onSelect,
  onHover,
  isOpen,
  query,
}) => {
  const listRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef<HTMLDivElement>(null);

  // Scroll selected item into view
  useEffect(() => {
    if (selectedRef.current && listRef.current) {
      selectedRef.current.scrollIntoView({
        block: 'nearest',
        behavior: 'smooth',
      });
    }
  }, [selectedIndex]);

  if (!isOpen || files.length === 0) return null;

  return (
    <div
      ref={listRef}
      className="absolute bottom-full left-0 z-50 mb-1 max-h-60 w-full overflow-y-auto rounded-lg border bg-popover shadow-lg"
    >
      <div className="p-1">
        {query && (
          <div className="mb-1 border-b px-2 py-1 text-xs text-muted-foreground">
            Files matching &quot;{query}&quot;
          </div>
        )}
        {files.map((file, index) => (
          <div
            key={file.path}
            ref={index === selectedIndex ? selectedRef : null}
            className={cn(
              'flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm',
              index === selectedIndex ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'
            )}
            onClick={() => onSelect(file)}
            onMouseEnter={() => onHover(index)}
          >
            {file.type === 'directory' ? (
              <Folder className="h-4 w-4 flex-shrink-0 text-blue-500" />
            ) : (
              <File className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
            )}
            <span className="flex-1 truncate">{file.path}</span>
            {file.type === 'directory' && (
              <span className="text-xs text-muted-foreground">dir</span>
            )}
          </div>
        ))}
        {files.length === 10 && (
          <div className="mt-1 border-t px-2 py-1 text-xs text-muted-foreground">
            Type more to narrow results...
          </div>
        )}
      </div>
    </div>
  );
};
