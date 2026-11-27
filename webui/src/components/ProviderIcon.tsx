import { SiAnthropic } from '@icons-pack/react-simple-icons';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { FC, SVGProps } from 'react';

interface ProviderIconProps {
  provider: string;
  size?: number;
}

// Inline monochrome SVGs drawn with currentColor, so they follow the text color
// in every theme, including recoloring extensions such as Dark Reader. An
// <img src="*.svg"> cannot inherit currentColor and always renders black.
const OpenAIIcon: FC<SVGProps<SVGSVGElement>> = (props) => (
  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" {...props}>
    <path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0L4.069 14.3A4.501 4.501 0 0 1 2.34 7.895zm16.597 3.855l-5.843-3.369 2.02-1.168a.076.076 0 0 1 .071 0l4.749 2.742a4.505 4.505 0 0 1-.676 8.124v-5.677a.79.79 0 0 0-.321-.652zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.749-2.737a4.5 4.5 0 0 1 6.679 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z" />
  </svg>
);

const OpenRouterIcon: FC<SVGProps<SVGSVGElement>> = (props) => (
  <svg
    viewBox="0 0 512 512"
    fill="currentColor"
    stroke="currentColor"
    aria-hidden="true"
    {...props}
  >
    <path
      d="M3 248.945C18 248.945 76 236 106 219C136 202 136 202 198 158C276.497 102.293 332 120.945 423 120.945"
      strokeWidth="90"
    />
    <path d="M511 121.5L357.25 210.268L357.25 32.7324L511 121.5Z" />
    <path
      d="M0 249C15 249 73 261.945 103 278.945C133 295.945 133 295.945 195 339.945C273.497 395.652 329 377 420 377"
      strokeWidth="90"
    />
    <path d="M508 376.445L354.25 287.678L354.25 465.213L508 376.445Z" />
  </svg>
);

const PROVIDER_CONFIG = {
  openai: {
    type: 'inline' as const,
    icon: OpenAIIcon,
    color: '#10A37F',
    name: 'OpenAI',
  },
  anthropic: {
    type: 'component' as const,
    icon: SiAnthropic,
    color: '#CC785C',
    name: 'Anthropic',
  },
  openrouter: {
    type: 'inline' as const,
    icon: OpenRouterIcon,
    color: '#6467F2',
    name: 'OpenRouter',
  },
} as const;

export function hasProviderIcon(provider: string): boolean {
  return provider in PROVIDER_CONFIG;
}

export const ProviderIcon: FC<ProviderIconProps> = ({ provider, size = 14 }) => {
  const config = PROVIDER_CONFIG[provider as keyof typeof PROVIDER_CONFIG];

  return (
    <div className="flex items-center">
      {config ? (
        <Tooltip>
          <TooltipTrigger asChild>
            {config.type === 'component' ? (
              <config.icon size={size} color={config.color} />
            ) : (
              <span role="img" aria-label={config.name} className="inline-flex text-foreground">
                <config.icon width={size} height={size} className="shrink-0" />
              </span>
            )}
          </TooltipTrigger>
          <TooltipContent>
            <p>{config?.name}</p>
          </TooltipContent>
        </Tooltip>
      ) : (
        <span className="font-medium text-muted-foreground" style={{ marginRight: '-0.5em' }}>
          {provider}/
        </span>
      )}
    </div>
  );
};
