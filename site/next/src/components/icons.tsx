type IconProps = { className?: string; size?: number };

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export function StarIcon({ className, size = 15 }: IconProps) {
  return (
    <svg {...base} width={size} height={size} strokeWidth={2} className={className}>
      <path d="M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z" />
    </svg>
  );
}

export function MenuIcon({ className, size = 20 }: IconProps) {
  return (
    <svg {...base} width={size} height={size} strokeWidth={2} className={className}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

export function CopyIcon({ className, size = 17 }: IconProps) {
  return (
    <svg {...base} width={size} height={size} strokeWidth={2} className={className}>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  );
}

export function CheckIcon({ className, size = 17 }: IconProps) {
  return (
    <svg {...base} width={size} height={size} strokeWidth={2.4} className={className}>
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}
