import { useState } from 'react';
import { Bot, Terminal } from 'lucide-react';
import type { MessageRole } from '@/types/conversation';
import { type Observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

interface MessageAvatarProps {
  role$: Observable<MessageRole>;
  isError$?: Observable<boolean>;
  isSuccess$?: Observable<boolean>;
  chainType$: Observable<'start' | 'middle' | 'end' | 'standalone'>;
  agentAvatarUrl?: string;
  agentName?: string;
  userAvatarUrl?: string;
  userName?: string;
  userColor?: string;
}

function getInitials(name?: string): string {
  const parts = name?.trim().split(/\s+/).filter(Boolean).slice(0, 2);

  if (!parts?.length) {
    return 'U';
  }

  return parts.map((part) => part[0]?.toUpperCase()).join('');
}

function contrastColor(background: string): string | undefined {
  const match = /^#([0-9a-f]{6})$/i.exec(background);
  if (!match) return undefined;

  const channels = [0, 2, 4].map(
    (offset) => parseInt(match[1].slice(offset, offset + 2), 16) / 255
  );
  const [red, green, blue] = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  );
  const luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  return luminance > 0.179 ? '#000000' : '#ffffff';
}

export function MessageAvatar({
  role$,
  isError$,
  isSuccess$,
  chainType$,
  agentAvatarUrl,
  agentName,
  userAvatarUrl,
  userName,
  userColor,
}: MessageAvatarProps) {
  const role = use$(role$);
  const isError = use$(isError$);
  const isSuccess = use$(isSuccess$);
  const chainType = use$(chainType$);
  const [imageError, setImageError] = useState(false);
  // Only show avatar for standalone messages or the start of a chain
  if (chainType !== 'start' && chainType !== 'standalone') {
    return null;
  }

  const isUser = role === 'user';
  const isAssistant = role === 'assistant';
  const showCustomAvatar =
    (isAssistant && agentAvatarUrl && !imageError) || (isUser && userAvatarUrl && !imageError);
  const avatarUrl = isUser ? userAvatarUrl : agentAvatarUrl;
  const sideClass = isUser ? 'right-0' : 'left-0';
  const fallbackStyle =
    isUser && !showCustomAvatar && userColor
      ? { backgroundColor: userColor, color: contrastColor(userColor) }
      : undefined;

  const avatarClasses = `absolute top-1.5 flex h-8 w-8 flex-shrink-0 select-none items-center justify-center rounded-full border-2 border-border text-xs font-semibold md:h-10 md:w-10 md:text-sm ${sideClass} ${
    isUser
      ? showCustomAvatar
        ? 'overflow-hidden'
        : 'bg-blue-600 text-white'
      : isAssistant
        ? showCustomAvatar
          ? 'overflow-hidden'
          : 'bg-muted text-muted-foreground'
        : isError
          ? 'bg-red-800 text-red-100'
          : isSuccess
            ? 'bg-green-800 text-green-100'
            : 'bg-slate-500 text-white'
  }`;

  // Determine tooltip text
  const tooltipText = isUser
    ? userName || 'User'
    : isAssistant
      ? agentName || 'Assistant'
      : role === 'system'
        ? 'System'
        : role;

  const avatarElement = showCustomAvatar ? (
    <div className={avatarClasses}>
      <img
        src={avatarUrl}
        alt={`${tooltipText} avatar`}
        className="h-full w-full rounded-full object-cover"
        onError={() => setImageError(true)}
      />
    </div>
  ) : (
    <div className={avatarClasses} style={fallbackStyle} aria-label={`${tooltipText} avatar`}>
      {isAssistant ? (
        <Bot className="h-4 w-4 md:h-5 md:w-5" />
      ) : role === 'system' ? (
        <Terminal className="h-4 w-4 md:h-5 md:w-5" />
      ) : (
        <span aria-hidden="true">{getInitials(userName)}</span>
      )}
    </div>
  );

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{avatarElement}</TooltipTrigger>
        <TooltipContent side="bottom">{tooltipText}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
