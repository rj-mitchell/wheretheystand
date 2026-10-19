import { useId } from "react";

export interface Series {
  key: string;
  group: string;
  label: string;
  values: (number | null)[];
}

// drawn narrower on phones, so the labels stay readable when the chart is scaled to fit
const NARROW = typeof window !== "undefined" && window.matchMedia?.("(max-width: 40rem)").matches;
const W = NARROW ? 400 : 720;
const H = NARROW ? 280 : 320;
const PAD = { top: 16, right: 16, bottom: 36, left: 52 };
const LABEL_EVERY = NARROW ? 4 : 2;
const SHAPES = ["circle", "square", "triangle", "diamond", "cross", "star", "circle-open"] as const;

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function Marker({ shape, x, y }: { shape: (typeof SHAPES)[number]; x: number; y: number }) {
  const r = 4;
  switch (shape) {
    case "square":
      return <rect x={x - r} y={y - r} width={r * 2} height={r * 2} />;
    case "triangle":
      return <polygon points={`${x},${y - r - 1} ${x + r + 1},${y + r} ${x - r - 1},${y + r}`} />;
    case "diamond":
      return <polygon points={`${x},${y - r - 1} ${x + r + 1},${y} ${x},${y + r + 1} ${x - r - 1},${y}`} />;
    case "cross":
      return <path d={`M${x - r},${y - r}L${x + r},${y + r}M${x + r},${y - r}L${x - r},${y + r}`} className="stroke-only" />;
    case "star":
      return <path d={`M${x},${y - r - 1}V${y + r + 1}M${x - r - 1},${y}H${x + r + 1}`} className="stroke-only" />;
    case "circle-open":
      return <circle cx={x} cy={y} r={r} className="open" />;
    default:
      return <circle cx={x} cy={y} r={r} />;
  }
}

/** A simple, accessible line chart: one line per series, distinct marker shapes as well as colours. */
export function LineChart({
  years,
  series,
  partial,
  unit,
  title,
}: {
  years: number[];
  series: Series[];
  partial: number[];
  unit: string;
  title: string;
}) {
  const id = useId();
  const max = niceMax(Math.max(0, ...series.flatMap((s) => s.values.map((v) => v ?? 0))));
  const x = (i: number) => PAD.left + (i * (W - PAD.left - PAD.right)) / Math.max(1, years.length - 1);
  const y = (v: number) => PAD.top + (1 - v / max) * (H - PAD.top - PAD.bottom);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const fmt = (v: number) => (v >= 100 || Number.isInteger(v) ? Math.round(v).toLocaleString("en-GB") : v.toFixed(1));
  const peak = series
    .map((s) => {
      const i = s.values.reduce<number>((best, v, k) => ((v ?? -1) > (s.values[best] ?? -1) ? k : best), 0);
      return `${s.label} peaked in ${years[i]} (${fmt(s.values[i] ?? 0)} ${unit})`;
    })
    .join("; ");
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={`${id}-t ${id}-d`}>
        <title id={`${id}-t`}>{title}</title>
        <desc id={`${id}-d`}>
          {`Line chart, ${years[0]} to ${years[years.length - 1]}. ${peak}. The full figures are in the table below.`}
        </desc>
        {ticks.map((t) => (
          <g key={t} className="grid">
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} />
            <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end">
              {fmt(t)}
            </text>
          </g>
        ))}
        {years.map((yr, i) => (
          <text key={yr} className="axis" x={x(i)} y={H - PAD.bottom + 18}
            textAnchor={i === years.length - 1 ? "end" : i === 0 ? "start" : "middle"}>
            {years.length > 8 && i % LABEL_EVERY !== 0 && i !== years.length - 1
              ? ""
              : `${partial.includes(yr) ? "(" : ""}${yr}${partial.includes(yr) ? ")" : ""}`}
          </text>
        ))}
        {series.map((s, k) => (
          <g key={s.key} className={`series s${k} g-${s.group}`}>
            <polyline
              fill="none"
              points={s.values.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean).join(" ")}
            />
            {s.values.map((v, i) =>
              v == null ? null : <Marker key={i} shape={SHAPES[k % SHAPES.length]} x={x(i)} y={y(v)} />,
            )}
          </g>
        ))}
      </svg>
      <figcaption>
        <ul className="legend">
          {series.map((s, k) => (
            <li key={s.key} className={`s${k} g-${s.group}`}>
              <svg viewBox="-6 -6 12 12" aria-hidden="true">
                <Marker shape={SHAPES[k % SHAPES.length]} x={0} y={0} />
              </svg>
              {s.label}
            </li>
          ))}
        </ul>
        <p className="muted small">Years in brackets are part-years.</p>
      </figcaption>
    </figure>
  );
}
