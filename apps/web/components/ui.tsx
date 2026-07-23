import type { ReactNode } from "react";

/** Page header: title left, the ONE primary action right. If a screen needs two
 *  primary actions, the screen is doing two things and should be split. */
export function Header({
  title,
  action,
  meta,
}: {
  title: string;
  action?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-10 border-b border-[var(--color-line)] bg-[var(--color-bg)]/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1200px] items-center gap-4 px-8">
        <h1 className="text-[20px] font-semibold">{title}</h1>
        {meta && <div className="text-[13px] text-[var(--color-faint)]">{meta}</div>}
        <div className="ml-auto">{action}</div>
      </div>
    </header>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-[1200px] px-8 py-8">{children}</div>;
}

export function Button({
  children,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
}) {
  const styles =
    variant === "primary"
      ? "bg-[var(--color-accent)] text-white hover:brightness-110"
      : "border border-[var(--color-line)] text-[var(--color-muted)] hover:border-[var(--color-line-hover)] hover:text-[var(--color-ink)]";
  return (
    <button
      {...props}
      className={`rounded-[var(--radius-btn)] px-3.5 py-2 text-[13px] font-semibold transition-all duration-150 disabled:opacity-40 ${styles} ${props.className ?? ""}`}
    >
      {children}
    </button>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  // No drop shadow in dark mode — the raised surface plus a 1px line does the work.
  return (
    <div
      className={`rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-surface)] ${className}`}
    >
      {children}
    </div>
  );
}

/** Delta chip. Carries an arrow as well as colour — every state has to be readable
 *  without relying on hue. */
export function Delta({ value, suffix = "%" }: { value: number; suffix?: string }) {
  const up = value >= 0;
  return (
    <span
      className="mono inline-flex items-center gap-1 text-[12px]"
      style={{ color: up ? "var(--color-ok)" : "var(--color-bad)" }}
    >
      <span aria-hidden>{up ? "↑" : "↓"}</span>
      <span className="sr-only">{up ? "up" : "down"}</span>
      {Math.abs(value).toFixed(1)}
      {suffix}
    </span>
  );
}

export function Empty({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-[var(--radius-card)] border border-dashed border-[var(--color-line)] py-20 text-center">
      <p className="text-[15px] font-semibold">{title}</p>
      <p className="mt-1.5 max-w-sm text-[13px] text-[var(--color-faint)]">{hint}</p>
    </div>
  );
}
