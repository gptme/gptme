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
  // Only show avatar for standalone messages or the start of a chain
  if (chainType !== 'start' && chainType !== 'standalone') {
    return null;
  }

  const avatarClasses = `hidden md:flex mt-0.5 flex-shrink-0 w-8 h-8 rounded-full items-center justify-center absolute ${
    role === 'user'
      ? 'bg-blue-600 text-white right-0'
      : role === 'assistant'
        ? avatarUrl
          ? 'left-0 overflow-hidden'
          : 'bg-gptme-600 text-white left-0'
        : isError
          ? 'bg-red-800 text-red-100'
          : isSuccess
            ? 'bg-green-800 text-green-100'
            : 'bg-slate-500 text-white left-0'
  }`;

  // Render custom avatar image for assistant if available
  if (role === 'assistant' && avatarUrl) {
    return (
      <div className={avatarClasses}>
        <img
          src={avatarUrl}
          alt="Agent avatar"
          className="w-full h-full object-cover rounded-full"
          onError={(e) => {
            // Fall back to Bot icon if image fails to load
            const target = e.currentTarget;
            target.style.display = 'none';
            target.parentElement!.classList.add('bg-gptme-600', 'text-white');
            // Create and append Bot icon fallback
            const fallback = document.createElement('span');
            fallback.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></svg>`;
            target.parentElement!.appendChild(fallback);
          }}
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
