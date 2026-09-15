/** Join class names, skipping falsy values. */
export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

/** Page container: max width plus the responsive side gutter from the design. */
export const wrap = "mx-auto w-full max-w-page px-18 max-lg:px-10 max-md:px-8 max-sm:px-5";

/** Small uppercase mono label above headings. */
export const eyebrow =
  "m-0 font-mono text-[13px] font-medium uppercase tracking-eyebrow text-accent-2-text max-sm:text-xs";
