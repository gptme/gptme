type StepErrorToast = (props: {
  variant: 'destructive';
  title: string;
  description: string;
}) => void;

export function getStepStartErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }

  if (typeof error === 'string' && error.trim()) {
    return error;
  }

  return 'Failed to start generation';
}

export function toastStepStartError(toast: StepErrorToast, error: unknown): void {
  toast({
    variant: 'destructive',
    title: 'Generation failed',
    description: getStepStartErrorMessage(error),
  });
}
