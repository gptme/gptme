import { Copy, ExternalLink, FolderOpen } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { buildFileUri, isLocalApiBaseUrl } from '@/utils/openConversationPath';

interface OpenConversationPathButtonProps {
  logdir?: string;
  baseUrl: string;
}

export function OpenConversationPathButton({ logdir, baseUrl }: OpenConversationPathButtonProps) {
  if (!logdir || !isLocalApiBaseUrl(baseUrl)) {
    return null;
  }

  const copyPath = async () => {
    if (!navigator.clipboard?.writeText) {
      toast.error('Clipboard unavailable');
      return;
    }

    try {
      await navigator.clipboard.writeText(logdir);
      toast.success('Conversation path copied');
    } catch (error) {
      console.error('Failed to copy conversation path:', error);
      toast.error('Failed to copy conversation path');
    }
  };

  const openDirectory = () => {
    const fileUri = buildFileUri(logdir);
    if (!window.open(fileUri, '_blank', 'noopener,noreferrer')) {
      window.location.href = fileUri;
    }
  };

  return (
    <div className="sticky top-0 z-20 mx-auto flex max-w-3xl justify-end px-3 pt-3">
      <DropdownMenu>
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="h-8 w-8 bg-background/90 shadow-sm backdrop-blur"
                  aria-label="Open conversation directory"
                >
                  <FolderOpen className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent>Open conversation directory</TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuItem onClick={openDirectory}>
            <ExternalLink className="mr-2 h-4 w-4" />
            Open directory
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => void copyPath()}>
            <Copy className="mr-2 h-4 w-4" />
            Copy path
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
