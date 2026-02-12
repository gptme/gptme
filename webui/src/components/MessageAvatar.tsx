import { useState } from 'react';
import { Bot, User, Terminal } from 'lucide-react';
import type { MessageRole } from '@/types/conversation';
import { type Observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';

interface MessageAvatarProps {
  role$: Observable<MessageRole>;
  isError$?: Observable<boolean>;
  isSuccess$?: Observable<boolean>;
  chainType$: Observable<'start' | 'middle' | 'end' | 'standalone'>;
  avatarUrl?: string;
}

export function MessageAvatar({
  role$,
  isError$,
  isSuccess$,
  chainType$,
  avatarUrl,
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

  // Determine if we should show the custom avatar image
  const showCustomAvatar = role === 'assistant' && avatarUrl && !imageError;

  const avatarClasses = `hidden md:flex mt-0.5 flex-shrink-0 w-8 h-8 rounded-full items-center justify-center absolute ${
    role === 'user'
      ? 'bg-blue-600 text-white right-0'
      : role === 'assistant'
        ? showCustomAvatar
          ? 'left-0 overflow-hidden'
          : 'bg-gptme-600 text-white left-0'
        : isError
          ? 'bg-red-800 text-red-100'
          : isSuccess
            ? 'bg-green-800 text-green-100'
            : 'bg-slate-500 text-white left-0'
  }`;

  // Render custom avatar image for assistant if available and not errored
  if (showCustomAvatar) {
    return (
      <div className={avatarClasses}>
        <img
          src={avatarUrl}
          alt="Agent avatar"
          className="w-full h-full object-cover rounded-full"
          onError={() => setImageError(true)}
        />
      </div>
    );
  }

  return (
    <div className={avatarClasses}>
      {role === 'assistant' ? (
        <Bot className="h-5 w-5" />
      ) : role === 'system' ? (
        <Terminal className="h-5 w-5" />
      ) : (
        <User className="h-5 w-5" />
      )}
    </div>
  );
}
