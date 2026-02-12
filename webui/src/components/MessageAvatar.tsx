import { useState } from 'react';
import { Bot, User, Terminal } from 'lucide-react';
import type { MessageRole } from '@/types/conversation';
import { type Observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

interface MessageAvatarProps {
  role$: Observable<MessageRole>;
  isError$?: Observable<boolean>;
  isSuccess$?: Observable<boolean>;
  chainType$: Observable<'start' | 'middle' | 'end' | 'standalone'>;
  agentAvatarUrl?: string;
  agentName?: string;
  userAvatarUrl?: string;
  userName?: string;
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
    (isAssistant && agentAvatarUrl && !imageError) ||
    (isUser && userAvatarUrl && !imageError);
  const avatarUrl = isUser ? userAvatarUrl : agentAvatarUrl;

  const avatarClasses = `hidden md:flex mt-0.5 flex-shrink-0 w-8 h-8 rounded-full items-center justify-center absolute ${
    isUser
      ? showCustomAvatar
        ? 'right-0 overflow-hidden'
        : 'bg-blue-600 text-white right-0'
      : isAssistant
        ? showCustomAvatar
          ? 'left-0 overflow-hidden'
          : 'bg-gptme-600 text-white left-0'
        : isError
          ? 'bg-red-800 text-red-100'
          : isSuccess
            ? 'bg-green-800 text-green-100'
            : 'bg-slate-500 text-white left-0'
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
        className="w-full h-full object-cover rounded-full"
        onError={() => setImageError(true)}
      />
    </div>
  ) : (
    <div className={avatarClasses}>
      {isAssistant ? (
        <Bot className="h-5 w-5" />
      ) : role === 'system' ? (
        <Terminal className="h-5 w-5" />
      ) : (
        <User className="h-5 w-5" />
      )}
    </div>
  );

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{avatarElement}</TooltipTrigger>
        <TooltipContent side={isUser ? 'left' : 'right'}>{tooltipText}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
