import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { FormProvider, useFieldArray, useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { ToolsConfiguration } from '../ToolsConfiguration';
import { formSchema, type FormSchema } from '@/schemas/conversationSettings';
import { ToolFormat, type ChatConfig } from '@/types/api';
function Harness({ toolFormat }: { toolFormat: ChatConfig['chat']['tool_format'] }) {
  const form = useForm<FormSchema>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      chat: {
        name: 'test',
        model: 'openrouter/test/model',
        tools: [{ name: 'shell' }],
        tool_format: toolFormat,
        stream: true,
        interactive: false,
        workspace: '/tmp/workspace',
        system_prompt: 'be helpful',
      },
      mcp: { enabled: false, auto_start: false, servers: [] },
    },
  });
  const toolFields = useFieldArray({ control: form.control, name: 'chat.tools' });

  return (
    <>
      <FormProvider {...form}>
        <ToolsConfiguration form={form} toolFields={toolFields} isSubmitting={false} />
      </FormProvider>
      <output data-testid="tool-format">{String(form.watch('chat.tool_format'))}</output>
    </>
  );
}

beforeEach(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  // Radix Select uses these DOM helpers, which jsdom does not implement.
  Element.prototype.hasPointerCapture = jest.fn(() => false);
  Element.prototype.setPointerCapture = jest.fn();
  Element.prototype.releasePointerCapture = jest.fn();
  Element.prototype.scrollIntoView = jest.fn();
});

it('shows the automatic tool format as a selectable option and maps it back to null', async () => {
  render(<Harness toolFormat={null} />);

  const trigger = screen.getByRole('combobox', { name: 'Tool Format' });
  expect(trigger).toHaveTextContent('Auto (model default)');

  fireEvent.click(trigger);
  const autoOption = await screen.findByRole('option', { name: 'Auto (model default)' });
  const markdownOption = await screen.findByRole('option', { name: 'markdown' });

  fireEvent.click(markdownOption);
  await waitFor(() => expect(screen.getByTestId('tool-format')).toHaveTextContent('markdown'));

  fireEvent.click(screen.getByRole('combobox', { name: 'Tool Format' }));
  fireEvent.click(await screen.findByRole('option', { name: 'Auto (model default)' }));
  await waitFor(() => expect(screen.getByTestId('tool-format')).toHaveTextContent('null'));

  // The sentinel is display-only and must never leak into the submitted config.
  expect(autoOption).toHaveTextContent('Auto (model default)');
});

it('shows an explicit tool format instead of the automatic option', () => {
  render(<Harness toolFormat={ToolFormat.MARKDOWN} />);
  expect(screen.getByRole('combobox', { name: 'Tool Format' })).toHaveTextContent('markdown');
});
