import { Bot, User, Terminal } from 'lucide-react';
import type { MessageRole } from '@/types/conversation';
import { type Observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';

interface MessageAvatarProps {
  role$: Observable<MessageRole>;
  isError$?: Observable<boolean>;
  isSuccess$?: Observable<boolean>;
  chainType$: Observable<'start' | 'middle' | 'end' | 'standalone'>;
}

export function MessageAvatar({ role$, isError$, isSuccess$, chainType$ }: MessageAvatarProps) {
  const role = use$(role$);
  const isError = use$(isError$);
  const isSuccess = use$(isSuccess$);
  const chainType = use$(chainType$);
  // Only show avatar for standalone messages or the start of a chain
  if (chainType !== 'start' && chainType !== 'standalone') {
    return null;
  }

  const avatarClasses = `hidden md:flex mt-0.5 flex-shrink-0 w-8 h-8 rounded-full items-center justify-center absolute ${
    role === 'user'
      ? 'bg-blue-600 text-white right-0'
      : role === 'assistant'
        ? 'bg-gptme-600 text-white left-0'
        : isError
          ? 'bg-red-800 text-red-100'
          : isSuccess
            ? 'bg-green-800 text-green-100'
            : 'bg-slate-500 text-white left-0'
  }`;

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
