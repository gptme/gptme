/**
 * Tests for InlineElicitation — web UI for `elicit_pending` events (gptme/gptme#5056).
 */
import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { InlineElicitation, buildFormAnswer, initialFormValues } from '../InlineElicitation';
import type { ElicitFormField, PendingElicitation } from '@/stores/conversations';

function renderElicit(pending: PendingElicitation | null) {
  const pendingElicitation$ = observable<PendingElicitation | null>(pending);
  const onRespond = jest.fn().mockResolvedValue(undefined);
  render(<InlineElicitation pendingElicitation$={pendingElicitation$} onRespond={onRespond} />);
  return { onRespond };
}

describe('InlineElicitation', () => {
  it('renders nothing without a pending elicitation', () => {
    renderElicit(null);
    expect(screen.queryByTestId('inline-elicitation')).not.toBeInTheDocument();
  });

  it('answers a choice with the clicked option', () => {
    const { onRespond } = renderElicit({
      id: 'e1',
      type: 'choice',
      prompt: 'Pick one',
      options: ['alpha', 'beta'],
    });
    fireEvent.click(screen.getByText('beta'));
    expect(onRespond).toHaveBeenCalledWith({ action: 'accept', value: 'beta' });
  });

  it('picks a choice with its number key, but not while typing', () => {
    const { onRespond } = renderElicit({
      id: 'e1b',
      type: 'choice',
      prompt: 'Pick one',
      options: ['alpha', 'beta'],
    });
    const field = document.createElement('input');
    document.body.appendChild(field);
    fireEvent.keyDown(field, { key: '2' });
    expect(onRespond).not.toHaveBeenCalled();
    field.remove();
    fireEvent.keyDown(document.body, { key: '3' }); // out of range
    expect(onRespond).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: '2' });
    expect(onRespond).toHaveBeenCalledWith({ action: 'accept', value: 'beta' });
  });

  it('routes number keys only to the active pane with two pending questions', async () => {
    let active = 'left';
    const left = jest.fn().mockResolvedValue(undefined);
    const right = jest.fn().mockResolvedValue(undefined);
    const question = { id: 'split', type: 'choice', prompt: 'Pick', options: ['a', 'b'] };
    render(
      <>
        <InlineElicitation
          pendingElicitation$={observable<PendingElicitation | null>(question)}
          onRespond={left}
          isActivePane={() => active === 'left'}
        />
        <InlineElicitation
          pendingElicitation$={observable<PendingElicitation | null>(question)}
          onRespond={right}
          isActivePane={() => active === 'right'}
        />
      </>
    );
    fireEvent.keyDown(document.body, { key: '2' });
    await waitFor(() => expect(left).toHaveBeenCalledWith({ action: 'accept', value: 'b' }));
    expect(right).not.toHaveBeenCalled();
    active = 'right';
    fireEvent.keyDown(document.body, { key: '1' });
    await waitFor(() => expect(right).toHaveBeenCalledWith({ action: 'accept', value: 'a' }));
    expect(left).toHaveBeenCalledTimes(1);
  });

  it('answers multi_choice with all checked options', () => {
    const { onRespond } = renderElicit({
      id: 'e2',
      type: 'multi_choice',
      prompt: 'Pick some',
      options: ['a', 'b', 'c'],
    });
    fireEvent.click(screen.getByLabelText('a'));
    fireEvent.click(screen.getByLabelText('c'));
    fireEvent.click(screen.getByText('Submit'));
    expect(onRespond).toHaveBeenCalledWith({ action: 'accept', values: ['a', 'c'] });
  });

  it('answers a confirmation with yes/no', () => {
    const { onRespond } = renderElicit({ id: 'e3', type: 'confirmation', prompt: 'Sure?' });
    fireEvent.click(screen.getByText('No'));
    expect(onRespond).toHaveBeenCalledWith({ action: 'accept', value: 'no' });
  });

  it('sends typed text and masks secrets', () => {
    const { onRespond } = renderElicit({ id: 'e4', type: 'secret', prompt: 'Token' });
    const input = document.querySelector('input[type="password"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    fireEvent.change(input, { target: { value: 'hunter2' } });
    fireEvent.click(screen.getByText('Submit'));
    expect(onRespond).toHaveBeenCalledWith({ action: 'accept', value: 'hunter2' });
  });

  it('cancels', () => {
    const { onRespond } = renderElicit({ id: 'e5', type: 'text', prompt: 'Name?' });
    fireEvent.click(screen.getByText('Cancel'));
    expect(onRespond).toHaveBeenCalledWith({ action: 'cancel' });
  });

  it('blocks a form with a missing required field', () => {
    const { onRespond } = renderElicit({
      id: 'e6',
      type: 'form',
      prompt: 'Details',
      fields: [{ name: 'name', prompt: 'Name', type: 'text', required: true }],
    });
    fireEvent.click(screen.getByText('Submit'));
    expect(screen.getByRole('alert')).toHaveTextContent('"Name" is required');
    expect(onRespond).not.toHaveBeenCalled();
  });

  it('shows a retry message when sending fails', async () => {
    const pendingElicitation$ = observable<PendingElicitation | null>({
      id: 'e7',
      type: 'choice',
      prompt: 'Pick',
      options: ['x'],
    });
    const onRespond = jest.fn().mockRejectedValue(new Error('boom'));
    jest.spyOn(console, 'error').mockImplementation(() => {});
    render(<InlineElicitation pendingElicitation$={pendingElicitation$} onRespond={onRespond} />);
    fireEvent.click(screen.getByText('x'));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Try again'));
  });
});

describe('buildFormAnswer', () => {
  const fields: ElicitFormField[] = [
    { name: 'name', prompt: 'Name', type: 'text', required: true },
    { name: 'age', prompt: 'Age', type: 'number' },
    { name: 'ok', prompt: 'OK?', type: 'boolean' },
    { name: 'pw', prompt: 'Password', type: 'secret' },
  ];

  it('serializes types like the CLI form handler and hides secrets', () => {
    const values = { ...initialFormValues(fields), name: 'Bob', age: '3.5', ok: true, pw: 's3' };
    const built = buildFormAnswer(fields, values);
    expect('value' in built && JSON.parse(built.value)).toEqual({
      name: 'Bob',
      age: 3.5,
      ok: true,
      pw: '<secret provided>',
    });
  });

  it('turns an omitted optional number into null', () => {
    const built = buildFormAnswer(fields, { ...initialFormValues(fields), name: 'Bob' });
    expect('value' in built && JSON.parse(built.value).age).toBeNull();
  });

  it('rejects a non-finite number', () => {
    const built = buildFormAnswer(fields, {
      ...initialFormValues(fields),
      name: 'Bob',
      age: 'abc',
    });
    expect(built).toEqual({ error: '"Age" must be a finite number' });
  });

  it.each([true, 'yes', 'y', '1', 'TRUE', '  Yes  '])(
    'honors the affirmative boolean default %p',
    (value) => {
      const { onRespond } = renderElicit({
        id: 'defaults',
        type: 'form',
        prompt: 'Confirm',
        fields: [{ name: 'ok', prompt: 'OK?', type: 'boolean', default: value }],
      });
      expect(screen.getByRole('checkbox')).toBeChecked();
      fireEvent.click(screen.getByText('Submit'));
      expect(onRespond).toHaveBeenCalledWith({ action: 'accept', value: '{"ok":true}' });
    }
  );

  it.each([false, 'no', 'n', '0', 'FALSE', '  No  '])(
    'honors the negative boolean default %p',
    (value) => {
      expect(
        initialFormValues([{ name: 'ok', prompt: 'OK?', type: 'boolean', default: value }])
      ).toEqual({ ok: false });
    }
  );

  it('seeds booleans from string defaults', () => {
    const values = initialFormValues([
      { name: 'a', prompt: 'a', type: 'boolean', default: 'yes' },
      { name: 'b', prompt: 'b', type: 'boolean' },
    ]);
    expect(values).toEqual({ a: true, b: false });
  });
});
