import { useCallback, useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { shortcutsDialogOpen$ } from '@/stores/shortcutsDialog';

interface Shortcut {
  keys: string[];
  description: string;
}

interface ShortcutGroup {
  title: string;
  shortcuts: Shortcut[];
}

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform ?? '');
const MOD = isMac ? '⌘' : 'Ctrl';
const ALT = isMac ? '⌥' : 'Alt';
const CONVERSATION_SEARCH_MOD = isMac ? '⌘' : 'Alt';

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: 'General',
    shortcuts: [
      { keys: [MOD, 'K'], description: 'Open command palette' },
      { keys: [ALT, 'N'], description: 'New conversation' },
      { keys: [MOD, 'F'], description: 'Search messages in conversation' },
      { keys: [MOD, 'Shift', '\\'], description: 'Toggle split view' },
      { keys: [CONVERSATION_SEARCH_MOD, 'F'], description: 'Search conversations' },
      { keys: ['?'], description: 'Show this shortcuts reference' },
      { keys: ['i'], description: 'Focus the message input' },
    ],
  },
  {
    title: 'Command palette',
    shortcuts: [
      { keys: ['↑', '↓'], description: 'Navigate results' },
      { keys: ['Enter'], description: 'Select highlighted item' },
      { keys: ['Esc'], description: 'Close palette' },
    ],
  },
  {
    title: 'Message input',
    shortcuts: [
      { keys: ['Enter'], description: 'Send message' },
      { keys: ['Shift', 'Enter'], description: 'Insert a new line' },
      { keys: ['Esc'], description: 'Cancel edit / blur input' },
    ],
  },
  {
    title: 'Tool confirmation',
    shortcuts: [{ keys: ['Enter'], description: 'Confirm the pending tool call' }],
  },
];

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="pointer-events-none inline-flex h-6 min-w-[1.5rem] items-center justify-center rounded border bg-muted px-1.5 font-mono text-[11px] font-medium text-muted-foreground">
      {children}
    </kbd>
  );
}

export function ShortcutsDialog() {
  const [open, setOpen] = useState(false);

  const setOpenState = useCallback((value: boolean) => {
    setOpen(value);
    shortcutsDialogOpen$.set(value);
  }, []);

  // Sync with external opens (e.g. MenuBar help button).
  useEffect(() => {
    return shortcutsDialogOpen$.onChange(({ value }) => {
      setOpen(value);
    });
  }, []);

  // Toggle with `?` when not typing in an input/textarea/contentEditable.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key !== '?') return;
      const target = e.target as HTMLElement | null;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target?.isContentEditable
      ) {
        return;
      }
      e.preventDefault();
      setOpen((prev) => {
        const next = !prev;
        shortcutsDialogOpen$.set(next);
        return next;
      });
    };
    document.addEventListener('keydown', down);
    return () => document.removeEventListener('keydown', down);
  }, []);

  return (
    <Dialog open={open} onOpenChange={setOpenState}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            Press <Kbd>?</Kbd> anytime to open this reference.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {SHORTCUT_GROUPS.map((group) => (
            <div key={group.title}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {group.title}
              </h3>
              <ul className="space-y-1.5">
                {group.shortcuts.map((shortcut) => (
                  <li
                    key={shortcut.description}
                    className="flex items-center justify-between gap-4 text-sm"
                  >
                    <span>{shortcut.description}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      {shortcut.keys.map((key, i) => (
                        <Kbd key={i}>{key}</Kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
