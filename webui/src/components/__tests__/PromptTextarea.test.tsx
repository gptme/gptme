import { render, screen, fireEvent } from '@testing-library/react';
import { PromptTextarea } from '../PromptTextarea';

describe('PromptTextarea', () => {
  it('submits on Enter without Shift', () => {
    const onSubmit = jest.fn();
    render(
      <PromptTextarea
        value="hi"
        onValueChange={jest.fn()}
        onSubmit={onSubmit}
        aria-label="Prompt"
      />
    );
    fireEvent.keyDown(screen.getByLabelText('Prompt'), { key: 'Enter' });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('does not submit on Shift+Enter', () => {
    const onSubmit = jest.fn();
    render(
      <PromptTextarea
        value="hi"
        onValueChange={jest.fn()}
        onSubmit={onSubmit}
        aria-label="Prompt"
      />
    );
    fireEvent.keyDown(screen.getByLabelText('Prompt'), { key: 'Enter', shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('does not submit on Enter while composing (IME)', () => {
    const onSubmit = jest.fn();
    render(
      <PromptTextarea
        value="hi"
        onValueChange={jest.fn()}
        onSubmit={onSubmit}
        aria-label="Prompt"
      />
    );
    fireEvent.keyDown(screen.getByLabelText('Prompt'), { key: 'Enter', isComposing: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('calls onEscape on Escape', () => {
    const onEscape = jest.fn();
    render(
      <PromptTextarea
        value="hi"
        onValueChange={jest.fn()}
        onSubmit={jest.fn()}
        onEscape={onEscape}
        aria-label="Prompt"
      />
    );
    fireEvent.keyDown(screen.getByLabelText('Prompt'), { key: 'Escape' });
    expect(onEscape).toHaveBeenCalledTimes(1);
  });

  it('lets onKeyDownCapture intercept and skip default handling', () => {
    const onSubmit = jest.fn();
    const onKeyDownCapture = jest.fn(() => true);
    render(
      <PromptTextarea
        value="hi"
        onValueChange={jest.fn()}
        onSubmit={onSubmit}
        onKeyDownCapture={onKeyDownCapture}
        aria-label="Prompt"
      />
    );
    fireEvent.keyDown(screen.getByLabelText('Prompt'), { key: 'Enter' });
    expect(onKeyDownCapture).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('reports value and cursor position on change', () => {
    const onValueChange = jest.fn();
    render(
      <PromptTextarea
        value=""
        onValueChange={onValueChange}
        onSubmit={jest.fn()}
        aria-label="Prompt"
      />
    );
    const textarea = screen.getByLabelText('Prompt') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'hello' } });
    expect(onValueChange).toHaveBeenCalledWith('hello', expect.any(Number));
  });
});
