import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { MessageSquareQuote } from 'lucide-react';
import { type Observable, observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';
import type { ElicitFormField, PendingElicitation } from '@/stores/conversations';
import type { ElicitationResponse } from '@/utils/api';
import { MessageAvatar } from './MessageAvatar';

type FormValues = Record<string, string | boolean>;

/** Initial form state: field defaults, with booleans as real booleans. */
export function initialFormValues(fields: ElicitFormField[]): FormValues {
  const values: FormValues = {};
  for (const f of fields) {
    if (f.type === 'boolean') {
      values[f.name] = f.default === true || f.default === 'yes' || f.default === 'true';
    } else {
      values[f.name] = f.default == null ? '' : String(f.default);
    }
  }
  return values;
}

/**
 * Serialize a filled form to the JSON text the agent receives, matching the
 * CLI form handler: booleans stay booleans, numbers are parsed (an omitted
 * optional number is null), and secrets never appear in plaintext.
 *
 * Returns an error message instead when validation fails.
 */
export function buildFormAnswer(
  fields: ElicitFormField[],
  values: FormValues
): { value: string } | { error: string } {
  const result: Record<string, unknown> = {};
  for (const f of fields) {
    const raw = values[f.name];
    if (f.type === 'boolean') {
      result[f.name] = raw === true;
      continue;
    }
    const text = typeof raw === 'string' ? raw : '';
    if (f.required && !text.trim()) {
      return { error: `"${f.prompt || f.name}" is required` };
    }
    if (f.type === 'number') {
      if (!text.trim()) {
        result[f.name] = null;
        continue;
      }
      const n = Number(text);
      if (!Number.isFinite(n)) {
        return { error: `"${f.prompt || f.name}" must be a finite number` };
      }
      result[f.name] = n;
    } else if (f.type === 'secret') {
      result[f.name] = '<secret provided>';
    } else {
      result[f.name] = text;
    }
  }
  return { value: JSON.stringify(result) };
}

interface InlineElicitationProps {
  pendingElicitation$: Observable<PendingElicitation | null>;
  onRespond: (response: ElicitationResponse) => Promise<void>;
}

/**
 * Renders a pending `elicit_pending` request (text, choice, multi_choice,
 * secret, confirmation or form) and sends the answer back to the server.
 */
export function InlineElicitation({ pendingElicitation$, onRespond }: InlineElicitationProps) {
  const pending = use$(pendingElicitation$);
  const [text, setText] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [formValues, setFormValues] = useState<FormValues>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Reset local state whenever a new request arrives.
  const pendingId = pending?.id;
  useEffect(() => {
    if (!pending) return;
    setText(pending.default == null ? '' : String(pending.default));
    setSelected([]);
    setFormValues(initialFormValues(pending.fields ?? []));
    setError(null);
    setSubmitting(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingId]);

  // Number keys pick a choice, unless the user is typing somewhere.
  const choiceOptions = pending?.type === 'choice' ? (pending.options ?? []) : [];
  const choiceKey = choiceOptions.join('\u0000');
  useEffect(() => {
    if (choiceOptions.length === 0 || submitting) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
      const index = Number(e.key) - 1;
      if (!Number.isInteger(index) || index < 0 || index >= choiceOptions.length) return;
      e.preventDefault();
      void send({ action: 'accept', value: choiceOptions[index] });
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingId, choiceKey, submitting]);

  if (!pending) return null;

  const send = async (response: ElicitationResponse) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onRespond(response);
    } catch (e) {
      console.error('Error answering elicitation:', e);
      setError('Could not send the answer. Try again.');
      setSubmitting(false);
    }
  };

  const submit = () => {
    switch (pending.type) {
      case 'choice':
        return; // choices submit on click
      case 'multi_choice':
        return send({ action: 'accept', values: selected });
      case 'form': {
        const built = buildFormAnswer(pending.fields ?? [], formValues);
        if ('error' in built) {
          setError(built.error);
          return;
        }
        return send({ action: 'accept', value: built.value });
      }
      default:
        return send({ action: 'accept', value: text });
    }
  };

  const options = pending.options ?? [];

  return (
    <div className="role-system mb-2 mt-2" data-testid="inline-elicitation">
      <div className="mx-auto max-w-3xl px-4">
        <div className="relative">
          <MessageAvatar
            role$={observable('system' as const)}
            isError$={observable(false)}
            isSuccess$={observable(false)}
            chainType$={observable('standalone' as const)}
          />
          <div className="md:px-12">
            <div className="rounded-lg border border-sky-200 bg-sky-50 dark:border-sky-800 dark:bg-sky-950/20">
              <div className="flex items-center gap-2 border-b border-sky-200 px-3 py-2 dark:border-sky-800">
                <MessageSquareQuote className="h-3.5 w-3.5 shrink-0 text-sky-600 dark:text-sky-400" />
                <h3 className="text-sm font-medium text-sky-800 dark:text-sky-200">
                  {pending.prompt}
                </h3>
              </div>

              <div className="space-y-3 p-3">
                {pending.description && (
                  <p className="text-xs text-muted-foreground">{pending.description}</p>
                )}

                {pending.type === 'choice' && (
                  <div className="flex flex-col gap-1.5">
                    {options.map((option, i) => (
                      <Button
                        key={option}
                        variant="outline"
                        size="sm"
                        className="h-8 justify-start px-2 text-left text-xs"
                        disabled={submitting}
                        onClick={() => send({ action: 'accept', value: option })}
                      >
                        <span className="mr-2 text-muted-foreground">{i + 1}.</span>
                        {option}
                      </Button>
                    ))}
                  </div>
                )}

                {pending.type === 'multi_choice' && (
                  <div className="flex flex-col gap-1.5">
                    {options.map((option) => (
                      <label key={option} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={selected.includes(option)}
                          onChange={(e) =>
                            setSelected((prev) =>
                              e.target.checked
                                ? [...prev, option]
                                : prev.filter((o) => o !== option)
                            )
                          }
                        />
                        {option}
                      </label>
                    ))}
                  </div>
                )}

                {(pending.type === 'text' || pending.type === 'secret') && (
                  <Input
                    type={pending.type === 'secret' ? 'password' : 'text'}
                    value={text}
                    autoFocus
                    autoComplete="off"
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void submit();
                    }}
                  />
                )}

                {pending.type === 'form' && (
                  <div className="space-y-2">
                    {(pending.fields ?? []).map((f) => (
                      <div key={f.name} className="space-y-1">
                        <label className="text-xs font-medium" htmlFor={`elicit-${f.name}`}>
                          {f.prompt || f.name}
                          {f.required && <span className="text-destructive"> *</span>}
                        </label>
                        {f.type === 'boolean' ? (
                          <input
                            id={`elicit-${f.name}`}
                            type="checkbox"
                            checked={formValues[f.name] === true}
                            onChange={(e) =>
                              setFormValues((prev) => ({ ...prev, [f.name]: e.target.checked }))
                            }
                          />
                        ) : f.type === 'choice' && f.options?.length ? (
                          <select
                            id={`elicit-${f.name}`}
                            className="block w-full rounded-md border bg-background px-2 py-1 text-sm"
                            value={String(formValues[f.name] ?? '')}
                            onChange={(e) =>
                              setFormValues((prev) => ({ ...prev, [f.name]: e.target.value }))
                            }
                          >
                            <option value="">—</option>
                            {f.options.map((o) => (
                              <option key={o} value={o}>
                                {o}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <Input
                            id={`elicit-${f.name}`}
                            type={
                              f.type === 'secret'
                                ? 'password'
                                : f.type === 'number'
                                  ? 'number'
                                  : 'text'
                            }
                            autoComplete="off"
                            value={String(formValues[f.name] ?? '')}
                            onChange={(e) =>
                              setFormValues((prev) => ({ ...prev, [f.name]: e.target.value }))
                            }
                          />
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {pending.type === 'confirmation' && (
                  <div className="flex gap-1.5">
                    <Button
                      size="sm"
                      className="h-7 px-3 text-xs"
                      disabled={submitting}
                      onClick={() => send({ action: 'accept', value: 'yes' })}
                    >
                      Yes
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-3 text-xs"
                      disabled={submitting}
                      onClick={() => send({ action: 'accept', value: 'no' })}
                    >
                      No
                    </Button>
                  </div>
                )}

                {error && (
                  <p role="alert" className="text-xs text-destructive">
                    {error}
                  </p>
                )}

                <div className="flex items-center gap-1.5">
                  {pending.type !== 'choice' && pending.type !== 'confirmation' && (
                    <Button
                      size="sm"
                      className="h-7 px-3 text-xs"
                      disabled={submitting}
                      onClick={() => void submit()}
                    >
                      Submit
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-3 text-xs"
                    disabled={submitting}
                    onClick={() => send({ action: 'cancel' })}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
