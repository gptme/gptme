import { SiOpenai, SiAnthropic } from '@icons-pack/react-simple-icons';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { FC } from 'react';

interface ProviderIconProps {
  provider: string;
  size?: number;
}

const PROVIDER_CONFIG = {
  openai: {
    type: 'component' as const,
    icon: SiOpenai,
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
    type: 'svg' as const,
    icon: '/icon-openrouter.svg',
    color: '#6467F2',
    name: 'OpenRouter',
  },
} as const;

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
              <img
                src={config.icon}
                alt={config.name}
                width={size}
                height={size}
                style={{
                  color: config.color,
                }}
                className="inline-block"
              />
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
