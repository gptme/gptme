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
