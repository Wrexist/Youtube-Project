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
      {/* 16px of padding until there is room for 32. With a fixed 64px rail beside
          it, `px-8` left a 375px screen 247px for a title and a button and the
          header simply overflowed — the whole page scrolled sideways, on every
          screen, because the frame did. Both values are on the spacing scale. */}
      <div className="mx-auto flex h-16 max-w-[1200px] items-center gap-4 px-4 sm:px-8">
        {/* `min-w-0` is what lets the title give way. Without it a flex item
            refuses to shrink below its content and pushes the action off-screen
            instead of truncating. */}
        <h1 className="min-w-0 truncate text-[20px] font-semibold">{title}</h1>
        {meta && (
          <div className="hidden text-[13px] text-[var(--color-faint)] sm:block">{meta}</div>
        )}
        <div className="ml-auto shrink-0">{action}</div>
      </div>
    </header>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-[1200px] px-4 py-8 sm:px-8">{children}</div>;
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

/** An empty state, optionally offering the action that would fill it.
 *
 * `children` exists because a screen that tells you it has nothing and gives you
 * no way to get something is a dead end — which is exactly what Repurpose was:
 * "nothing has been swept in yet" above no button that sweeps. */
export function Empty({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-[var(--radius-card)] border border-dashed border-[var(--color-line)] py-20 text-center">
      <p className="text-[15px] font-semibold">{title}</p>
      <p className="mt-1.5 max-w-sm text-[13px] text-[var(--color-faint)]">{hint}</p>
      {children && <div className="mt-5">{children}</div>}
    </div>
  );
}
