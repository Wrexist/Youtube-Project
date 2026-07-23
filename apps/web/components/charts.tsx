/** Charts.
 *
 *  Hand-rolled SVG rather than a charting library: these are four specific shapes,
 *  each of which needs exact control over labelling and colour, and a library would
 *  cost more in fighting its defaults than it saves.
 *
 *  Conventions (per the dataviz skill): no gridline clutter, direct labels instead of
 *  legends, one accent per chart, and every value also available as text for screen
 *  readers.
 */

function path(values: number[], w: number, h: number, pad = 2) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = w / (values.length - 1);
  return values
    .map((v, i) => {
      const x = i * step;
      const y = pad + (h - pad * 2) * (1 - (v - min) / range);
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

export function Sparkline({
  values,
  width = 120,
  height = 32,
  color = "var(--color-muted)",
  fill = false,
}: {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
  fill?: boolean;
}) {
  const d = path(values, width, height);
  const id = `spark-${values.length}-${Math.round(values[0])}`;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="overflow-visible"
      aria-hidden
    >
      {fill && (
        <>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.22" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${d} L${width},${height} L0,${height} Z`} fill={`url(#${id})`} />
        </>
      )}
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

/** The one number that owns the Analytics screen. Nothing else competes with it. */
export function BigNumber({
  label,
  value,
  delta,
  series,
}: {
  label: string;
  value: string;
  delta: number;
  series: number[];
}) {
  const up = delta >= 0;
  return (
    <div className="flex flex-wrap items-end justify-between gap-8">
      <div>
        <p className="text-[13px] text-[var(--color-faint)]">{label}</p>
        <p className="mono mt-1 text-[52px] leading-none font-semibold tracking-tight">
          {value}
        </p>
        <p
          className="mono mt-2.5 text-[13px]"
          style={{ color: up ? "var(--color-ok)" : "var(--color-bad)" }}
        >
          {up ? "↑" : "↓"} {Math.abs(delta).toFixed(1)}%{" "}
          <span className="text-[var(--color-faint)]">vs previous 28 days</span>
        </p>
      </div>
      <Sparkline
        values={series}
        width={420}
        height={96}
        color="var(--color-accent)"
        fill
      />
    </div>
  );
}

export function StatTile({
  label,
  value,
  delta,
  series,
  highlight = false,
}: {
  label: string;
  value: string;
  delta: number;
  series: number[];
  highlight?: boolean;
}) {
  const up = delta >= 0;
  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5">
      <p className="text-[12px] text-[var(--color-faint)]">{label}</p>
      <div className="mt-2 flex items-end justify-between gap-4">
        <div>
          <p className="mono text-[28px] leading-none font-semibold">{value}</p>
          <p
            className="mono mt-2 text-[12px]"
            style={{ color: up ? "var(--color-ok)" : "var(--color-bad)" }}
          >
            {up ? "↑" : "↓"} {Math.abs(delta).toFixed(1)}%
          </p>
        </div>
        {/* Only the tile that moved most gets the accent. Everything else stays quiet. */}
        <Sparkline
          values={series}
          width={104}
          height={40}
          color={highlight ? "var(--color-accent)" : "var(--color-muted)"}
          fill={highlight}
        />
      </div>
    </div>
  );
}

/** Retention curve with script beats overlaid.
 *
 *  The point of the whole analytics loop: a drop-off is not a mystery, it points at
 *  the beat that caused it. Beats flagged `warn` sit where the curve falls fastest. */
export function RetentionMap({
  curve,
  beats,
}: {
  curve: number[];
  beats: { at: number; label: string; warn?: boolean }[];
}) {
  const W = 900;
  const H = 240;
  const d = path(curve, W, H, 6);

  return (
    <figure className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H + 46}`} className="w-full min-w-[640px]" role="img"
           aria-label="Audience retention across the video, annotated with script beats">
        <defs>
          <linearGradient id="ret" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.18" />
            <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Two reference lines only. More is clutter. */}
        {[50, 100].map((pct) => {
          const y = 6 + (H - 12) * (1 - (pct - Math.min(...curve)) /
            (Math.max(...curve) - Math.min(...curve)));
          return (
            <g key={pct}>
              <line x1="0" x2={W} y1={y} y2={y} stroke="var(--color-line)" strokeDasharray="3 5" />
              <text x={W - 4} y={y - 6} textAnchor="end" fontSize="10" fill="var(--color-faint)">
                {pct}%
              </text>
            </g>
          );
        })}

        <path d={`${d} L${W},${H} L0,${H} Z`} fill="url(#ret)" />
        <path d={d} fill="none" stroke="var(--color-accent)" strokeWidth="2" />

        {beats.map((beat) => {
          const x = (beat.at / 100) * W;
          return (
            <g key={beat.at}>
              <line
                x1={x} x2={x} y1="0" y2={H}
                stroke={beat.warn ? "var(--color-warn)" : "var(--color-line-hover)"}
                strokeWidth="1"
              />
              <text
                x={x + 5}
                y={H + 18}
                fontSize="11"
                fill={beat.warn ? "var(--color-warn)" : "var(--color-faint)"}
              >
                {beat.label}
              </text>
              {beat.warn && (
                <text x={x + 5} y={H + 33} fontSize="10" fill="var(--color-faint)">
                  steepest drop
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="sr-only">
        Retention starts at 100% and ends at {curve[curve.length - 1]}%. The steepest
        decline occurs at the “First data point” beat.
      </figcaption>
    </figure>
  );
}

/** Weekly quota consumption. The only place the API ceiling is made visible —
 *  present, but not alarming. */
export function QuotaBar({ used, total }: { used: number; total: number }) {
  const pct = Math.min(100, (used / total) * 100);
  const tight = pct > 75;
  return (
    <div className="flex items-center gap-3">
      <div className="h-1 w-28 overflow-hidden rounded-full bg-[var(--color-raised)]">
        <div
          className="h-full rounded-full transition-[width] duration-300"
          style={{
            width: `${pct}%`,
            background: tight ? "var(--color-warn)" : "var(--color-muted)",
          }}
        />
      </div>
      <span className="mono text-[11px] text-[var(--color-faint)]">
        {used.toLocaleString()} / {total.toLocaleString()} quota
      </span>
    </div>
  );
}
