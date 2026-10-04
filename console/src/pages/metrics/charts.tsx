import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Button } from "../../components/ui/Button";

export type ChartSeries = { key: string; label: string; color: string; values: number[] };

export const formatCount = (value: number) => Math.round(value).toLocaleString();

export function formatSeconds(value: number | null | undefined) {
  if (value == null) return "No data";
  if (value < 0.001) return "<1 ms";
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  return `${value.toFixed(value < 10 ? 2 : 1)} s`;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry!.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

/** 0 and 3 to 5 round steps (1, 2 or 5 times a power of ten) that cover max. */
function niceTicks(max: number, format: (value: number) => string = formatCount) {
  if (max <= 0) return { top: 1, ticks: [0, 1].map((value) => ({ value, label: format(value) })) };
  const rough = max / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((factor) => factor * power).find((candidate) => candidate >= rough)!;
  const top = Math.ceil(max / step) * step;
  const ticks = [];
  for (let value = 0; value <= top + step / 2; value += step) ticks.push({ value, label: value === 0 ? "0" : format(value) });
  return { top, ticks };
}

function xTicks(count: number, wanted: number) {
  if (count <= 1) return [0];
  const every = Math.max(1, Math.round((count - 1) / (wanted - 1)));
  const indexes = [];
  for (let index = 0; index < count; index += every) indexes.push(index);
  return indexes;
}

/** Edge labels grow inward, so they never spill past the chart. */
function tickAnchor(x: number, width: number) {
  return x < MARGIN.left + 30 ? "start" : x > width - MARGIN.right - 30 ? "end" : "middle";
}

/** A rect with 4px rounded top corners and a square base. */
function columnPath(x: number, y: number, width: number, height: number, rounded: boolean) {
  const r = rounded ? Math.min(4, width / 2, height) : 0;
  return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + height}Z`;
}

const MARGIN = { top: 10, right: 12, bottom: 24, left: 44 };

type Hover = { index: number } | null;

function useKeyboardIndex(count: number, hover: Hover, setHover: (hover: Hover) => void) {
  return {
    tabIndex: 0,
    onFocus: () => setHover({ index: count - 1 }),
    onBlur: () => setHover(null),
    onKeyDown: (event: KeyboardEvent) => {
      const current = hover?.index ?? count - 1;
      const next = event.key === "ArrowLeft" ? current - 1 : event.key === "ArrowRight" ? current + 1
        : event.key === "Home" ? 0 : event.key === "End" ? count - 1 : null;
      if (next === null) return;
      event.preventDefault();
      setHover({ index: Math.min(count - 1, Math.max(0, next)) });
    },
  };
}

function Tooltip({ x, width, title, rows, footer }: { x: number; width: number; title: string; rows: { key: string; color: string; label: string; value: string }[]; footer?: string }) {
  const flip = x > width - 190;
  return (
    <div className="chart-tooltip" role="status" style={flip ? { right: width - x + 12 } : { left: x + 12 }}>
      <p className="chart-tooltip-title">{title}</p>
      {rows.map((row) => (
        <p key={row.key} className="chart-tooltip-row">
          <span className="line-key" style={{ background: row.color }} />
          <strong>{row.value}</strong>
          <span>{row.label}</span>
        </p>
      ))}
      {footer && <p className="chart-tooltip-footer">{footer}</p>}
    </div>
  );
}

type TimeChartProps = { timestamps: number[]; formatTime: (seconds: number) => string; formatTick: (seconds: number) => string; label: string; height?: number };

export function StackedColumns({ timestamps, series, formatTime, formatTick, label, height = 220 }: TimeChartProps & { series: ChartSeries[] }) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<Hover>(null);
  const keyboard = useKeyboardIndex(timestamps.length, hover, setHover);
  const totals = timestamps.map((_, index) => series.reduce((sum, item) => sum + (item.values[index] ?? 0), 0));
  const { top, ticks } = niceTicks(Math.max(...totals, 0));
  const plotW = Math.max(width - MARGIN.left - MARGIN.right, 0);
  const plotH = height - MARGIN.top - MARGIN.bottom;
  const slot = plotW / Math.max(timestamps.length, 1);
  const barW = Math.min(24, Math.max(1.5, slot - 2));
  const y = (value: number) => MARGIN.top + plotH - (value / top) * plotH;
  const hovered = hover?.index;

  return (
    <div className="chart" ref={ref}>
      {width > 0 && (
        <svg width={width} height={height} role="group" aria-label={`${label}. Use the arrow keys to read each interval.`} {...keyboard}
          onPointerLeave={() => setHover(null)}>
          {ticks.map((tick) => (
            <g key={tick.value}>
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(tick.value)} y2={y(tick.value)} className={tick.value === 0 ? "chart-axis" : "chart-grid"} />
              <text x={MARGIN.left - 8} y={y(tick.value)} dy="0.32em" textAnchor="end" className="chart-tick">{tick.label}</text>
            </g>
          ))}
          {hovered !== undefined && <rect x={MARGIN.left + slot * hovered} y={MARGIN.top} width={slot} height={plotH} className="chart-hover-band" />}
          {timestamps.map((_, index) => {
            const x = MARGIN.left + slot * index + (slot - barW) / 2;
            let cursor = MARGIN.top + plotH;
            const visible = series.filter((item) => (item.values[index] ?? 0) > 0);
            return (
              <g key={index} opacity={hovered === undefined || hovered === index ? 1 : 0.55}>
                {visible.map((item, position) => {
                  const h = Math.max(((item.values[index] ?? 0) / top) * plotH, 1);
                  const last = position === visible.length - 1;
                  // The 2px surface gap sits above every segment but the top one.
                  const segment = columnPath(x, cursor - h, barW, Math.max(h - (last ? 0 : 2), 0.5), last);
                  cursor -= h;
                  return <path key={item.key} d={segment} fill={item.color} />;
                })}
              </g>
            );
          })}
          {xTicks(timestamps.length, Math.max(2, Math.floor(plotW / 90))).map((index) => (
            <text key={index} x={MARGIN.left + slot * (index + 0.5)} y={height - 6} textAnchor={tickAnchor(MARGIN.left + slot * (index + 0.5), width)} className="chart-tick">{formatTick(timestamps[index]!)}</text>
          ))}
          {timestamps.map((_, index) => (
            <rect key={index} x={MARGIN.left + slot * index} y={MARGIN.top} width={slot} height={plotH} fill="transparent"
              onPointerEnter={() => setHover({ index })} />
          ))}
        </svg>
      )}
      {hovered !== undefined && width > 0 && (
        <Tooltip
          x={MARGIN.left + slot * (hovered + 0.5)}
          width={width}
          title={formatTime(timestamps[hovered]!)}
          rows={series.map((item) => ({ key: item.key, color: item.color, label: item.label, value: formatCount(item.values[hovered] ?? 0) }))}
          footer={`${formatCount(totals[hovered]!)} in total`}
        />
      )}
    </div>
  );
}

export function LineChart({ timestamps, values, color, formatValue, formatTime, formatTick, label, valueLabel, height = 220 }: TimeChartProps & {
  values: (number | null)[]; color: string; formatValue: (value: number) => string; valueLabel: string;
}) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<Hover>(null);
  const keyboard = useKeyboardIndex(timestamps.length, hover, setHover);
  const present = values.filter((value): value is number => value !== null);
  const { top, ticks } = niceTicks(Math.max(...present, 0), formatValue);
  const plotW = Math.max(width - MARGIN.left - MARGIN.right, 0);
  const plotH = height - MARGIN.top - MARGIN.bottom;
  const x = (index: number) => MARGIN.left + (timestamps.length > 1 ? (index / (timestamps.length - 1)) * plotW : plotW / 2);
  const y = (value: number) => MARGIN.top + plotH - (value / top) * plotH;

  // Split at gaps, so missing data is never drawn as a line.
  const runs: number[][] = [];
  values.forEach((value, index) => {
    if (value === null) return;
    if (index > 0 && values[index - 1] !== null && runs.length) runs.at(-1)!.push(index);
    else runs.push([index]);
  });
  let lastIndex = values.length - 1;
  while (lastIndex >= 0 && values[lastIndex] === null) lastIndex -= 1;
  const hovered = hover?.index;

  return (
    <div className="chart" ref={ref}>
      {width > 0 && (
        <svg width={width} height={height} role="group" aria-label={`${label}. Use the arrow keys to read each interval.`} {...keyboard}
          onPointerLeave={() => setHover(null)}
          onPointerMove={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            const index = Math.round(((event.clientX - box.left - MARGIN.left) / Math.max(plotW, 1)) * (timestamps.length - 1));
            setHover({ index: Math.min(timestamps.length - 1, Math.max(0, index)) });
          }}>
          {ticks.map((tick) => (
            <g key={tick.value}>
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(tick.value)} y2={y(tick.value)} className={tick.value === 0 ? "chart-axis" : "chart-grid"} />
              <text x={MARGIN.left - 8} y={y(tick.value)} dy="0.32em" textAnchor="end" className="chart-tick">{tick.label}</text>
            </g>
          ))}
          {runs.map((run) => {
            const line = run.map((index, position) => `${position ? "L" : "M"}${x(index)},${y(values[index]!)}`).join("");
            const area = `${line}L${x(run.at(-1)!)},${y(0)}L${x(run[0]!)},${y(0)}Z`;
            return (
              <g key={run[0]}>
                <path d={area} fill={color} opacity={0.1} />
                <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              </g>
            );
          })}
          {runs.filter((run) => run.length === 1).map((run) => <circle key={`dot-${run[0]}`} cx={x(run[0]!)} cy={y(values[run[0]!]!)} r={3} fill={color} />)}
          {lastIndex >= 0 && hovered === undefined && (
            <>
              <circle cx={x(lastIndex)} cy={y(values[lastIndex]!)} r={4} fill={color} className="chart-ring" />
              <text x={x(lastIndex) - 8} y={y(values[lastIndex]!) - 10} textAnchor="end" className="chart-label">{formatValue(values[lastIndex]!)}</text>
            </>
          )}
          {hovered !== undefined && (
            <>
              <line x1={x(hovered)} x2={x(hovered)} y1={MARGIN.top} y2={MARGIN.top + plotH} className="chart-crosshair" />
              {values[hovered] != null && <circle cx={x(hovered)} cy={y(values[hovered]!)} r={4} fill={color} className="chart-ring" />}
            </>
          )}
          {xTicks(timestamps.length, Math.max(2, Math.floor(plotW / 90))).map((index) => (
            <text key={index} x={x(index)} y={height - 6} textAnchor={tickAnchor(x(index), width)} className="chart-tick">{formatTick(timestamps[index]!)}</text>
          ))}
        </svg>
      )}
      {hovered !== undefined && width > 0 && (
        <Tooltip
          x={x(hovered)}
          width={width}
          title={formatTime(timestamps[hovered]!)}
          rows={[{ key: "value", color, label: valueLabel, value: values[hovered] == null ? "No requests" : formatValue(values[hovered]!) }]}
        />
      )}
    </div>
  );
}

export type BarRow = { key: string; label: string; segments: { key: string; label: string; color: string; value: number }[] };

/** Horizontal bars, longest first; each row splits into segments with a 2px surface gap. */
export function BarList({ rows, label }: { rows: BarRow[]; label: string }) {
  const max = Math.max(...rows.map((row) => row.segments.reduce((sum, segment) => sum + segment.value, 0)), 1);
  return (
    <ul className="bar-list" aria-label={label}>
      {rows.map((row) => {
        const total = row.segments.reduce((sum, segment) => sum + segment.value, 0);
        const shown = row.segments.filter((segment) => segment.value > 0);
        const summary = shown.map((segment) => `${formatCount(segment.value)} ${segment.label.toLowerCase()}`).join(", ");
        return (
          <li key={row.key} className="bar-row" tabIndex={0} aria-label={`${row.label}: ${formatCount(total)} (${summary})`}>
            <span className="bar-label mono" title={row.label}>{row.label}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(total / max) * 100}%` }}>
                {shown.map((segment) => (
                  <span key={segment.key} className="bar-segment" style={{ flexGrow: segment.value, background: segment.color }} />
                ))}
              </span>
              <span className="bar-value">{formatCount(total)}</span>
            </span>
            <span className="bar-tooltip" aria-hidden="true">
              <strong>{row.label}</strong>
              {shown.map((segment) => (
                <span key={segment.key} className="chart-tooltip-row">
                  <span className="line-key" style={{ background: segment.color }} /><strong>{formatCount(segment.value)}</strong><span>{segment.label}</span>
                </span>
              ))}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function Legend({ items, shape = "box" }: { items: { key: string; label: string; color: string }[]; shape?: "box" | "line" }) {
  return (
    <ul className="legend" aria-label="Legend">
      {items.map((item) => (
        <li key={item.key}><span className={shape === "line" ? "line-key" : "swatch"} style={{ background: item.color }} />{item.label}</li>
      ))}
    </ul>
  );
}

type ChartCardProps = { id: string; title: string; subtitle?: string; legend?: ReactNode; table: ReactNode; children: ReactNode; className?: string };

/** A chart with a title, its legend, and a table view of the same numbers. */
export function ChartCard({ id, title, subtitle, legend, table, children, className }: ChartCardProps) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className={`panel chart-card${className ? ` ${className}` : ""}`} aria-labelledby={`${id}-title`}>
      <header className="chart-card-header">
        <div>
          <h2 id={`${id}-title`}>{title}</h2>
          {subtitle && <p className="field-hint">{subtitle}</p>}
        </div>
        <Button variant="ghost" size="sm" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>{asTable ? "Show chart" : "Show table"}</Button>
      </header>
      {!asTable && legend}
      <div className="chart-card-body">{asTable ? <div className="chart-table">{table}</div> : children}</div>
    </section>
  );
}
