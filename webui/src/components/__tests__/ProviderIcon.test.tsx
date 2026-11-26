import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { ProviderIcon } from '../ProviderIcon';

function renderIcon(provider: string) {
  return render(
    <TooltipProvider>
      <ProviderIcon provider={provider} size={12} />
    </TooltipProvider>
  );
}

describe('ProviderIcon', () => {
  it.each([
    ['openai', 'OpenAI'],
    ['openrouter', 'OpenRouter'],
  ])('draws %s inline with currentColor, not as an <img>', (provider, name) => {
    const { container } = renderIcon(provider);
    // An <img src="*.svg"> cannot inherit currentColor, so it stays black under
    // recoloring extensions (Dark Reader) and needed a dark:invert workaround.
    expect(container.querySelector('img')).toBeNull();
    const icon = screen.getByRole('img', { name });
    const svg = icon.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute('fill', 'currentColor');
    expect(svg).toHaveAttribute('width', '12');
    expect(icon).toHaveClass('text-foreground');
  });

  it('falls back to the provider prefix for unknown providers', () => {
    renderIcon('deepseek');
    expect(screen.getByText('deepseek/')).toBeInTheDocument();
  });
});
