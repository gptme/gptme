import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import type { PendingTool } from '@/stores/conversations';
import {
  Loader2,
  Play,
  Edit,
  SkipForward,
  Settings,
  ChevronDown,
  ChevronsRight,
} from 'lucide-react';
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

  // Use a ref to track pending requests synchronously (prevents render-time race conditions)
  const isConfirmingRef = React.useRef(false);

  // Reset state when the pending tool changes
  React.useEffect(() => {
    if (pendingTool) {
      const content = pendingTool.tooluse.content;
      setEditedContent(content);
      setIsEditing(false);
      setConfirmLoading(false);
      setShowCustomInput(false);
      isConfirmingRef.current = false;
    }
  }, [pendingTool]);

  const handleConfirm = React.useCallback(async () => {
    // Check synchronously before any async work (prevents render-time race)
    if (isConfirmingRef.current) return;
    isConfirmingRef.current = true;
    setConfirmLoading(true);
    try {
      await onConfirm();
    } catch (error) {
      console.error('Error confirming tool:', error);
    } finally {
      setConfirmLoading(false);
      isConfirmingRef.current = false;
    }
  }, [onConfirm]);

  // Add keyboard handler for Enter key
  React.useEffect(() => {
    const handleKeyPress = async (e: KeyboardEvent) => {
      if (
        pendingTool &&
        !isEditing &&
        !confirmLoading &&
        !isConfirmingRef.current &&
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
  }, [pendingTool, isEditing, confirmLoading, handleConfirm]);

  const handleEdit = async () => {
    // Check synchronously before any async work (prevents render-time race)
    if (isConfirmingRef.current) return;
    isConfirmingRef.current = true;
    setConfirmLoading(true);
    try {
      await onEdit(editedContent);
    } catch (error) {
      console.error('Error confirming edited tool:', error);
    } finally {
      setConfirmLoading(false);
      isConfirmingRef.current = false;
    }
  };

  const handleSkip = async () => {
    // Check synchronously before any async work (prevents render-time race)
    if (isConfirmingRef.current) return;
    isConfirmingRef.current = true;
    setConfirmLoading(true);
    try {
      await onSkip();
    } catch (error) {
      console.error('Error skipping tool:', error);
    } finally {
      setConfirmLoading(false);
      isConfirmingRef.current = false;
    }
  };

  const handleAcceptAll = async () => {
    // Check synchronously before any async work (prevents render-time race)
    if (isConfirmingRef.current) return;
    isConfirmingRef.current = true;
    setConfirmLoading(true);
    try {
      await onAuto(999999);
    } catch (error) {
      console.error('Error accepting all tools:', error);
    } finally {
      setConfirmLoading(false);
      isConfirmingRef.current = false;
    }
  };

  const handleAuto = React.useCallback(
    async (count: number) => {
      // Check synchronously before any async work (prevents render-time race)
      if (isConfirmingRef.current) return;
      isConfirmingRef.current = true;
      setConfirmLoading(true);
      try {
        await onAuto(count);
      } catch (error) {
        console.error('Error auto-confirming tools:', error);
      } finally {
        setConfirmLoading(false);
        isConfirmingRef.current = false;
      }
    },
    [onAuto]
  );

  // Format args for display
  const formatArgs = (args: string[]) => {
    if (!args || args.length === 0) return 'No arguments';
    if (args.length === 1) return args[0];
    return args.map((arg, i) => `${i + 1}. ${arg}`).join('\n');
  };

  if (!pendingTool) return null;

  return (
    <div className="role-system mb-2 mt-2">
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
              {/* Compact header */}
              <div className="flex items-center gap-2 border-b border-amber-200 px-3 py-2 dark:border-amber-800">
                <Settings className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
                <span className="text-sm font-medium text-amber-800 dark:text-amber-200">
                  Run{' '}
                  <code className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-xs dark:bg-amber-900/40">
                    {pendingTool.tooluse.tool}
                  </code>
                  ?
                </span>
                <span className="ml-auto text-xs text-amber-600 dark:text-amber-400">
                  Press Enter to execute
                </span>
              </div>

              <div className="space-y-3 p-3">
                {/* Arguments */}
                {pendingTool.tooluse.args.length > 0 && (
                  <div className="space-y-1">
                    <span className="text-xs font-medium text-muted-foreground">Arguments:</span>
                    <CodeDisplay
                      code={formatArgs(pendingTool.tooluse.args)}
                      maxHeight="80px"
                      showLineNumbers={false}
                    />
                  </div>
                )}

                {/* Code */}
                <div className="space-y-1">
                  {isEditing ? (
                    <Textarea
                      value={editedContent}
                      onChange={(e) => setEditedContent(e.target.value)}
                      rows={Math.min(10, editedContent.split('\n').length + 2)}
                      className="resize-none font-mono text-sm"
                      placeholder="Edit the code to be executed..."
                    />
                  ) : (
                    <CodeDisplay
                      code={pendingTool.tooluse.content}
                      maxHeight="200px"
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
                <div className="flex flex-wrap items-center justify-between gap-y-2 border-t border-amber-200 pt-2 dark:border-amber-800">
                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setIsEditing(!isEditing)}
                      disabled={confirmLoading}
                      className="h-7 px-2 text-xs"
                    >
                      <Edit className="mr-1 h-3.5 w-3.5" />
                      {isEditing ? 'Cancel' : 'Edit'}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleSkip}
                      disabled={confirmLoading}
                      className="h-7 px-2 text-xs"
                    >
                      <SkipForward className="mr-1 h-3.5 w-3.5" />
                      Skip
                    </Button>
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    {/* Accept All — direct one-click action */}
                    {!isEditing && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleAcceptAll}
                        disabled={confirmLoading}
                        className="h-7 px-2 text-xs text-amber-700 hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-900/30"
                        title="Auto-accept all remaining tool confirmations"
                      >
                        <ChevronsRight className="mr-1 h-3.5 w-3.5" />
                        Accept All
                      </Button>
                    )}

                    {/* Execute (primary) + dropdown for 5x/10x/custom */}
                    <div className="flex items-center">
                      <Button
                        onClick={isEditing ? handleEdit : handleConfirm}
                        disabled={confirmLoading}
                        size="sm"
                        className="h-7 rounded-r-none border-r-0 bg-amber-600 px-2 text-xs text-white hover:bg-amber-700"
                      >
                        {confirmLoading && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                        <Play className="mr-1 h-3.5 w-3.5" />
                        {isEditing ? 'Save & Execute' : 'Execute'}
                      </Button>

                      {!isEditing && (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              disabled={confirmLoading}
                              size="sm"
                              className="h-7 rounded-l-none border-l border-amber-500 bg-amber-600 px-1.5 text-white hover:bg-amber-700"
                              title="Auto-confirm multiple tools"
                            >
                              <ChevronDown className="h-3.5 w-3.5" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-44">
                            <DropdownMenuItem onClick={() => handleAuto(5)}>
                              Auto-confirm 5×
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleAuto(10)}>
                              Auto-confirm 10×
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              onClick={() => setShowCustomInput(!showCustomInput)}
                              className="flex items-center justify-between"
                            >
                              Custom count
                              {showCustomInput && (
                                <div
                                  className="ml-2 flex items-center gap-1.5"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <Input
                                    type="number"
                                    min="1"
                                    max="50"
                                    value={customCount}
                                    onChange={(e) =>
                                      setCustomCount(parseInt(e.target.value, 10) || 1)
                                    }
                                    className="h-6 w-14 px-1 text-xs"
                                    autoFocus
                                  />
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    className="h-6 px-2 text-xs"
                                    onClick={() => {
                                      handleAuto(customCount);
                                      setShowCustomInput(false);
                                    }}
                                  >
                                    Go
                                  </Button>
                                </div>
                              )}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                    </div>
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
