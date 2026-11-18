import { z } from 'zod';

const embeddedLinkMenuItemSchema = z.object({
  kind: z.literal('link'),
  id: z.string().min(1),
  label: z.string().min(1),
  href: z
    .string()
    .min(1)
    .refine(
      (val) => {
        // Allow relative paths (safe by definition)
        if (
          val.startsWith('/') ||
          val.startsWith('./') ||
          val.startsWith('../') ||
          val.startsWith('#')
        ) {
          return true;
        }
        // For absolute URLs only allow http/https — blocks javascript: and other unsafe protocols
        try {
          const url = new URL(val);
          return url.protocol === 'https:' || url.protocol === 'http:';
        } catch {
          return false;
        }
      },
      { message: 'href must use http/https protocol or be a relative path' }
    ),
  section: z.string().min(1).optional(),
});

const embeddedActionMenuItemSchema = z.object({
  kind: z.literal('action'),
  id: z.string().min(1),
  label: z.string().min(1),
  action: z.string().min(1),
  section: z.string().min(1).optional(),
  destructive: z.boolean().optional(),
});

export const embeddedMenuItemSchema = z.discriminatedUnion('kind', [
  embeddedLinkMenuItemSchema,
  embeddedActionMenuItemSchema,
]);

const embeddedContextMessageSchema = z.object({
  type: z.literal('gptme-host:embedded-context'),
  payload: z.object({
    menuItems: z.array(embeddedMenuItemSchema),
  }),
});

const seedPromptMessageSchema = z.object({
  type: z.literal('gptme-host:seed-prompt'),
  payload: z.object({
    prompt: z.string().min(1),
    // Opt-in auto-send. Hosts that only want to pre-fill the composer (the
    // default) omit it; a host forwarding a prompt the user already submitted
    // (e.g. gptme.ai's landing hero) sets it so the seed is sent instead of
    // waiting for a second Enter press.
    autoSend: z.boolean().optional(),
  }),
});

export interface SeedPrompt {
  prompt: string;
  /** True when the host asked for the prompt to be sent, not just pre-filled. */
  autoSend: boolean;
}

export type EmbeddedMenuItem = z.infer<typeof embeddedMenuItemSchema>;

export function getEmbeddedParentOrigin(referrer: string): string | null {
  if (!referrer) {
    return null;
  }

  try {
    return new URL(referrer).origin;
  } catch {
    return null;
  }
}

export function parseEmbeddedContextMessage(data: unknown): EmbeddedMenuItem[] | null {
  const parsed = embeddedContextMessageSchema.safeParse(data);
  return parsed.success ? parsed.data.payload.menuItems : null;
}

export function parseSeedPromptMessage(data: unknown): SeedPrompt | null {
  const parsed = seedPromptMessageSchema.safeParse(data);
  if (!parsed.success) {
    return null;
  }
  return {
    prompt: parsed.data.payload.prompt,
    autoSend: parsed.data.payload.autoSend === true,
  };
}

export function isEmbeddedContextEventAllowed(
  eventOrigin: string,
  parentOrigin: string | null,
  ownOrigin: string,
  options: { allowUnknownParentOrigin?: boolean } = {}
): boolean {
  if (parentOrigin) {
    return eventOrigin === parentOrigin;
  }

  if (options.allowUnknownParentOrigin) {
    return true;
  }

  return eventOrigin === ownOrigin;
}
