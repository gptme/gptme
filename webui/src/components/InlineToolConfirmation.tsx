import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import type { PendingTool } from '@/stores/conversations';
import { Loader2, Play, Edit, SkipForward, Settings, ChevronDown } from 'lucide-react';
import { type Observable, observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';
import { CodeDisplay } from '@/components/CodeDisplay';
import { MessageAvatar } from './MessageAvatar';
import { detectToolLanguage } from '@/utils/highlightUtils';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';

interface InlineToolConfirmationProps {
  pendingTool$: Observable<PendingTool | null>;
  onConfirm: () => Promise<void>;
  onEdit: (content: string) => Promise<void>;
  onSkip: () => Promise<void>;
  onAuto: (count: number) => Promise<void>;
}

export function InlineToolConfirmation({
  pendingTool$,
  onConfirm,
  onEdit,
  onSkip,
  onAuto,
}: InlineToolConfirmationProps) {
  const [editedContent, setEditedContent] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [customCount, setCustomCount] = useState(10);
  const [showCustomInput, setShowCustomInput] = useState(false);
  const pendingTool = use$(pendingTool$);

  // Reset state when the pending tool changes
  React.useEffect(() => {
    if (pendingTool) {
      const content = pendingTool.tooluse.content;
      setEditedContent(content);
      setIsEditing(false);
      setConfirmLoading(false);
      setShowCustomInput(false);
    }
  }, [pendingTool]);

  const handleConfirm = React.useCallback(async () => {
    setConfirmLoading(true);
    try {
      await onConfirm();
    } catch (error) {
      console.error('Error confirming tool:', error);
    } finally {
      setConfirmLoading(false);
    }
  }, [onConfirm]);

  // Add keyboard handler for Enter key
  React.useEffect(() => {
    const handleKeyPress = async (e: KeyboardEvent) => {
      if (
        pendingTool &&
        !isEditing &&
        e.key === 'Enter' &&
        !e.shiftKey &&
        !e.ctrlKey &&
        !e.altKey
      ) {
        e.preventDefault();
        await handleConfirm();
      }
    };

    window.addEventListener('keypress', handleKeyPress);
    return () => window.removeEventListener('keypress', handleKeyPress);
  }, [pendingTool, isEditing, handleConfirm]);

  const handleEdit = async () => {
    setConfirmLoading(true);
    try {
      await onEdit(editedContent);
    } catch (error) {
      console.error('Error confirming edited tool:', error);
    } finally {
      setConfirmLoading(false);
    }
  };

  const handleSkip = async () => {
    try {
      await onSkip();
    } catch (error) {
      console.error('Error skipping tool:', error);
    }
  };

  // Format args for display
  const formatArgs = (args: string[]) => {
    if (!args || args.length === 0) return 'No arguments';
    if (args.length === 1) return args[0];
    return args.map((arg, i) => `${i + 1}. ${arg}`).join('\n');
  };

  if (!pendingTool) return null;

  return (
    <div className="role-system mb-4 mt-4">
      <div className="mx-auto max-w-3xl px-4">
        <div className="relative">
          <MessageAvatar
            role$={observable('system' as const)}
            isError$={observable(false)}
            isSuccess$={observable(false)}
            chainType$={observable('standalone' as const)}
          />
          <div className="md:px-12">
            <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20">
              <div className="border-b border-amber-200 px-4 py-3 dark:border-amber-800">
                <div className="flex items-center gap-2">
                  <Settings className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                  <h3 className="font-medium text-amber-800 dark:text-amber-200">
                    Tool Execution Confirmation
                  </h3>
                </div>

                <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
                  The assistant wants to use
                  <code className="rounded bg-muted px-2 py-1 font-mono text-sm">
                    {pendingTool.tooluse.tool}
                  </code>
                </p>
              </div>

              <div className="space-y-4 p-4">
                {/* Arguments */}
                {pendingTool.tooluse.args.length > 0 && (
                  <div className="space-y-2">
                    <span className="text-sm font-medium text-muted-foreground">Arguments:</span>
                    <CodeDisplay
                      code={formatArgs(pendingTool.tooluse.args)}
                      maxHeight="120px"
                      showLineNumbers={false}
                    />
                  </div>
                )}

                {/* Code */}
                <div className="space-y-2">
                  {isEditing ? (
                    <Textarea
                      value={editedContent}
                      onChange={(e) => setEditedContent(e.target.value)}
                      rows={Math.min(12, editedContent.split('\n').length + 2)}
                      className="resize-none font-mono text-sm"
                      placeholder="Edit the code to be executed..."
                    />
                  ) : (
                    <CodeDisplay
                      code={pendingTool.tooluse.content}
                      maxHeight="240px"
                      showLineNumbers={true}
                      language={detectToolLanguage(
                        pendingTool.tooluse.tool,
                        pendingTool.tooluse.args,
                        pendingTool.tooluse.content
                      )}
                    />
                  )}
                </div>

                {/* Action buttons */}
                <div className="flex items-center justify-between border-t border-amber-200 pt-3 dark:border-amber-800">
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setIsEditing(!isEditing)}
                      disabled={confirmLoading}
                    >
                      <Edit className="mr-1 h-4 w-4" />
                      {isEditing ? 'Cancel' : 'Edit'}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleSkip}
                      disabled={confirmLoading}
                    >
                      <SkipForward className="mr-1 h-4 w-4" />
                      Skip
                    </Button>
                  </div>

                  <div className="flex items-center">
                    <Button
                      onClick={isEditing ? handleEdit : handleConfirm}
                      disabled={confirmLoading}
                      size="sm"
                      className="rounded-r-none border-r-0 bg-amber-600 text-white hover:bg-amber-700"
                    >
                      {confirmLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                      <Play className="mr-1 h-4 w-4" />
                      {isEditing ? 'Save & Execute' : 'Execute'}
                    </Button>

                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          disabled={confirmLoading}
                          size="sm"
                          className="rounded-l-none border-l border-amber-500 bg-amber-600 px-2 text-white hover:bg-amber-700"
                        >
                          <ChevronDown className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-48">
                        <DropdownMenuItem onClick={() => onAuto(999999)}>
                          Auto-accept all
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem onClick={() => onAuto(5)}>
                          Auto-confirm 5x
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => onAuto(10)}>
                          Auto-confirm 10x
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          onClick={() => setShowCustomInput(!showCustomInput)}
                          className="flex items-center justify-between"
                        >
                          Custom count
                          {showCustomInput && (
                            <div
                              className="ml-2 flex items-center gap-2"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <Input
                                type="number"
                                min="1"
                                max="50"
                                value={customCount}
                                onChange={(e) => setCustomCount(parseInt(e.target.value, 10) || 1)}
                                className="h-6 w-16 px-1 text-xs"
                                autoFocus
                              />
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-6 px-2 text-xs"
                                onClick={() => {
                                  onAuto(customCount);
                                  setShowCustomInput(false);
                                }}
                              >
                                Apply
                              </Button>
                            </div>
                          )}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
