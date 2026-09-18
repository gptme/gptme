import { forwardRef } from 'react';
import { Textarea } from '@/components/ui/textarea';

export interface PromptTextareaProps {
  value: string;
  /** `cursorPosition` is the caret offset after the change (0 if unavailable). */
  onValueChange: (value: string, cursorPosition: number) => void;
  /** Called on a submit keystroke (Enter without Shift, outside IME composition). */
  onSubmit: () => void;
  /** Called on Escape, after any consumer-specific interception below has run. */
  onEscape?: () => void;
  /**
   * Runs before the default Enter/Escape handling. Return `true` to indicate the
   * keystroke was fully handled (e.g. an autocomplete popup consumed it) — the
   * shell then skips its own submit/escape logic for that keystroke.
   */
  onKeyDownCapture?: (e: React.KeyboardEvent<HTMLTextAreaElement>) => boolean;
  onPaste?: (e: React.ClipboardEvent<HTMLTextAreaElement>) => void;
  placeholder?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  className?: string;
  'data-testid'?: string;
  'aria-label': string;
  'aria-describedby'?: string;
}

/**
 * Presentational textarea shell shared between the connected chat surface
 * (`ChatInput`) and the anonymous landing-page prompt front door. No
 * `useApi`/store dependencies — props in, callback out.
 *
 * Behavior: auto-resize up to 400px, Enter submits (Shift+Enter newlines),
 * IME composition is respected (a composing Enter never submits), Escape is
 * exposed via `onEscape` for the caller to interpret.
 */
export const PromptTextarea = forwardRef<HTMLTextAreaElement, PromptTextareaProps>(
  (
    {
      value,
      onValueChange,
      onSubmit,
      onEscape,
      onKeyDownCapture,
      onPaste,
      placeholder,
      disabled,
      autoFocus,
      className,
      ...rest
    },
    ref
  ) => {
    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      onValueChange(e.target.value, e.target.selectionStart || 0);
      // Auto-adjust height
      e.target.style.height = 'auto';
      e.target.style.height = `${Math.min(e.target.scrollHeight, 400)}px`;
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (onKeyDownCapture?.(e)) return;

      // Skip submit during IME composition (Japanese/Chinese/Korean input,
      // mobile keyboards): the Enter that confirms a candidate must not submit.
      if (e.nativeEvent.isComposing) return;

      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        onSubmit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onEscape?.();
      }
    };

    return (
      <Textarea
        ref={ref}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onPaste={onPaste}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus={autoFocus}
        className={className}
        {...rest}
      />
    );
  }
);
PromptTextarea.displayName = 'PromptTextarea';
