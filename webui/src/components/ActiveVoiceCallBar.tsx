import { useLocation, useNavigate } from 'react-router-dom';
import { AudioLines, PhoneOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useOptionalVoiceCall } from '@/contexts/VoiceCallContext';
import { chatRoute } from '@/utils/routes';

/**
 * Shows a live voice call everywhere except its own chat (where the composer's
 * voice button controls it), so a call that kept running while the user
 * navigated away is never left streaming the microphone without a stop button.
 */
export function ActiveVoiceCallBar() {
  const call = useOptionalVoiceCall();
  const location = useLocation();
  const navigate = useNavigate();

  if (!call || (call.state !== 'recording' && call.state !== 'connecting')) return null;
  const { conversationId, serverId } = call;
  const onCallChat =
    !!conversationId && location.pathname === `/chat/${encodeURIComponent(conversationId)}`;
  if (onCallChat) return null;

  return (
    <div
      role="status"
      aria-label="Voice call active"
      className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-full border bg-background/95 px-3 py-1.5 text-sm shadow-lg backdrop-blur"
    >
      <AudioLines className="h-4 w-4 text-red-500" />
      <span>{call.state === 'connecting' ? 'Connecting voice call…' : 'Voice call active'}</span>
      {conversationId && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2"
          onClick={() =>
            navigate(
              chatRoute(
                conversationId,
                serverId ? new URLSearchParams({ server: serverId }).toString() : undefined
              )
            )
          }
        >
          Open chat
        </Button>
      )}
      <Button
        type="button"
        variant="destructive"
        size="sm"
        className="h-7 gap-1 px-2"
        onClick={call.stop}
        aria-label="End voice call"
      >
        <PhoneOff className="h-3.5 w-3.5" />
        End
      </Button>
    </div>
  );
}
