/**
 * Charting primitives (dependency-free SVG).
 *
 * The PRD asks for anomaly-activity and SoH-trend charts driven by real
 * persisted data (PRD §4.5, §4.6, §8). Grafana owns the high-rate time series;
 * these small SVG charts cover the aggregated views that belong in the React
 * app, so the bundle stays free of a heavyweight charting library.
 *
 * Accessibility (PRD §7): every chart has a title, labelled axes/units and a
 * legend, and severity is never conveyed by colour alone — the legend and the
 * accessible table alternative name each series.
 */
import type { ReactNode } from "react";

export interface BarSeries {
  key: string;
  label: string;
  color: string;
  values: number[];
}

export interface StackedBarChartProps {
  title: string;
  /** X-axis tick labels, aligned with each series' ``values``. */
  labels: string[];
  series: BarSeries[];
  /** Unit shown on the value axis (e.g. ``events``). */
  unit?: string;
  height?: number;
  emptyLabel?: string;
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / magnitude) * magnitude;
}

/**
 * The accessible data-table alternative the header comment
 * promises: the same numbers as the SVG, in a real table that
 * assistive technology (and anyone who cannot use hover) can
 * read. Visually hidden; the chart stays the visual surface.
 */
function ChartDataTable({
  title,
  columns,
  rows,
}: {
  title: string;
  columns: string[];
  rows: (string | number)[][];
}) {
  return (
    <div className="sr-only">
      <table>
        <caption>{title}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((cell, cellIndex) =>
                cellIndex === 0 ? (
                  <th key={cellIndex} scope="row">
                    {cell}
                  </th>
                ) : (
                  <td key={cellIndex}>{cell}</td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * A stacked bar chart for per-bucket counts.
 *
 * It draws one bar per x label, stacking each series bottom-up. Hovering a bar
 * exposes its exact breakdown via ``<title>`` so values are never colour-only.
 */
export function StackedBarChart({
  title,
  labels,
  series,
  unit = "",
  height = 220,
  emptyLabel = "No data for this period",
}: StackedBarChartProps) {
  const totals = labels.map((_, index) =>
    series.reduce((sum, s) => sum + (s.values[index] ?? 0), 0),
  );
  const max = niceMax(Math.max(0, ...totals));
  const hasData = totals.some((value) => value > 0);

  if (labels.length === 0 || !hasData) {
    return (
      <div className="chart chart--empty" style={{ minHeight: height }}>
        <span className="muted">{emptyLabel}</span>
      </div>
    );
  }

  const padding = { top: 12, right: 12, bottom: 40, left: 44 };
  const width = 720;
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const slot = plotW / labels.length;
  const barW = Math.max(2, Math.min(slot * 0.7, 40));

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    f,
    y: padding.top + plotH - f * plotH,
    value: Math.round(f * max),
  }));

  return (
    <figure className="chart">
      <figcaption className="chart__title">{title}</figcaption>
      <svg
        className="chart__svg"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${title}. ${series
          .map((s) => s.label)
          .join(", ")}. Peak ${max} ${unit}.`}
      >
        {unit && (
          <text
            x={padding.left - 34}
            y={padding.top + 4}
            textAnchor="start"
            className="chart__axis chart__axis--unit"
          >
            {unit}
          </text>
        )}
        {gridLines.map((line) => (
          <g key={line.f}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={line.y}
              y2={line.y}
              stroke="var(--border)"
              strokeWidth={1}
            />
            <text
              x={padding.left - 6}
              y={line.y + 3}
              textAnchor="end"
              className="chart__axis"
            >
              {line.value}
            </text>
          </g>
        ))}

        {labels.map((label, index) => {
          const x = padding.left + index * slot + (slot - barW) / 2;
          let cursorY = padding.top + plotH;
          const total = totals[index];
          const parts = series.map((s) => ({
            key: s.key,
            label: s.label,
            color: s.color,
            value: s.values[index] ?? 0,
          }));
          return (
            <g key={`${index}-${label}`}>
              {parts.map((part) => {
                const h = (part.value / max) * plotH;
                cursorY -= h;
                if (part.value <= 0) return null;
                return (
                  <rect
                    key={part.key}
                    x={x}
                    y={cursorY}
                    width={barW}
                    height={h}
                    fill={part.color}
                  >
                    <title>{`${label} · ${part.label}: ${part.value} ${unit}`}</title>
                  </rect>
                );
              })}
              <rect x={x} y={padding.top} width={barW} height={plotH} fill="transparent">
                <title>
                  {`${label} — total ${total} ${unit}\n` +
                    parts
                      .map((p) => `${p.label}: ${p.value}`)
                      .join("\n")}
                </title>
              </rect>
              {index % Math.ceil(labels.length / 12) === 0 && (
                <text
                  x={x + barW / 2}
                  y={height - 20}
                  textAnchor="middle"
                  className="chart__axis"
                >
                  {label}
                </text>
              )}
            </g>
          );
        })}
        <text
          x={padding.left + plotW / 2}
          y={height - 4}
          textAnchor="middle"
          className="chart__axis chart__axis--unit"
        >
          Time
        </text>
      </svg>
      <Legend items={series.map((s) => ({ label: s.label, color: s.color }))} />
      <ChartDataTable
        title={title}
        columns={["Bucket", ...series.map((s) => s.label)]}
        rows={labels.map((label, index) => [
          label,
          ...series.map((s) => s.values[index] ?? 0),
        ])}
      />
    </figure>
  );
}

export function Legend({
  items,
}: {
  items: { label: string; color: string; glyph?: ReactNode }[];
}) {
  return (
    <ul className="chart__legend">
      {items.map((item) => (
        <li key={item.label} className="chart__legend-item">
          <span
            className="chart__swatch"
            style={{ background: item.color }}
            aria-hidden="true"
          />
          {item.glyph && <span aria-hidden="true">{item.glyph}</span>}
          {item.label}
        </li>
      ))}
    </ul>
  );
}

export interface LinePoint {
  x: number;
  y: number;
}

export interface LineSeries {
  key: string;
  label: string;
  color: string;
  points: LinePoint[];
  dashed?: boolean;
}

export interface LineChartProps {
  title: string;
  series: LineSeries[];
  unit?: string;
  height?: number;
  /** Value axis bounds; when omitted they are derived from the points. */
  yMin?: number;
  yMax?: number;
  /** Format a y value for the axis labels. */
  formatY?: (value: number) => string;
  /** Format an x value (e.g. a timestamp) for the axis ticks. */
  formatX?: (value: number) => string;
  /** Caption under the x-axis. */
  xLabel?: string;
  emptyLabel?: string;
}

/** A multi-series line chart for trends (SoH history, degradation). */
export function LineChart({
  title,
  series,
  unit = "",
  height = 240,
  yMin,
  yMax,
  formatY = (v) => String(Math.round(v)),
  formatX,
  xLabel = "Time",
  emptyLabel = "No data for this period",
}: LineChartProps) {
  const all = series.flatMap((s) => s.points);
  if (all.length === 0) {
    return (
      <div className="chart chart--empty" style={{ minHeight: height }}>
        <span className="muted">{emptyLabel}</span>
      </div>
    );
  }

  const xs = all.map((p) => p.x);
  const ys = all.map((p) => p.y);
  const xLo = Math.min(...xs);
  const xHi = Math.max(...xs);
  const lo = yMin ?? Math.min(...ys);
  const hi = yMax ?? Math.max(...ys);
  const ySpan = hi - lo || 1;

  const padding = { top: 12, right: 16, bottom: 40, left: 52 };
  const width = 720;
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const xSpan = xHi - xLo || 1;

  const sx = (x: number) => padding.left + ((x - xLo) / xSpan) * plotW;
  const sy = (y: number) => padding.top + plotH - ((y - lo) / ySpan) * plotH;

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    f,
    y: padding.top + plotH - f * plotH,
    value: lo + f * ySpan,
  }));

  const xTicks = formatX
    ? Array.from({ length: 5 }, (_, i) => xLo + (i / 4) * xSpan)
    : [];
  const formatTick = formatX;

  return (
    <figure className="chart">
      <figcaption className="chart__title">{title}</figcaption>
      <svg
        className="chart__svg"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${title}. ${series.map((s) => s.label).join(", ")}.${
          unit ? ` Value axis in ${unit}.` : ""
        }`}
      >
        {unit && (
          <text
            x={padding.left - 40}
            y={padding.top + 4}
            textAnchor="start"
            className="chart__axis chart__axis--unit"
          >
            {unit}
          </text>
        )}
        {gridLines.map((line) => (
          <g key={line.f}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={line.y}
              y2={line.y}
              stroke="var(--border)"
              strokeWidth={1}
              strokeDasharray="2 3"
              opacity={0.6}
            />
            <text
              x={padding.left - 6}
              y={line.y + 3}
              textAnchor="end"
              className="chart__axis"
            >
              {formatY(line.value)}
            </text>
          </g>
        ))}
        {series.map((s) => {
          if (s.points.length === 0) return null;
          const d = s.points
            .map((p, i) => `${i === 0 ? "M" : "L"} ${sx(p.x)} ${sy(p.y)}`)
            .join(" ");
          return (
            <path
              key={s.key}
              d={d}
              fill="none"
              stroke={s.color}
              strokeWidth={1.5}
              strokeDasharray={s.dashed ? "4 3" : undefined}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <title>{s.label}</title>
            </path>
          );
        })}
        {series.map((s) => {
          // If a series has many points (e.g. > 35), drawing overlapping solid circles
          // creates an excessively bold, blotchy line. The smooth stroke path
          // renders the curve cleanly. For sparser series, render subtle dots.
          if (s.points.length > 35) return null;
          return s.points.map((p, i) => (
            <circle
              key={`${s.key}-${i}`}
              cx={sx(p.x)}
              cy={sy(p.y)}
              r={1.75}
              fill={s.color}
            >
              <title>{`${s.label}: ${formatY(p.y)} ${unit}`}</title>
            </circle>
          ));
        })}
        {formatTick &&
          xTicks.map((tick, i) => (
            <text
              key={i}
              x={sx(tick)}
              y={height - 20}
              textAnchor="middle"
              className="chart__axis"
            >
              {formatTick(tick)}
            </text>
          ))}
        <text
          x={padding.left + plotW / 2}
          y={height - 4}
          textAnchor="middle"
          className="chart__axis chart__axis--unit"
        >
          {xLabel}
        </text>
      </svg>
      <Legend items={series.map((s) => ({ label: s.label, color: s.color }))} />
      <ChartDataTable
        title={title}
        columns={["Series", xLabel, `Value${unit ? ` (${unit})` : ""}`]}
        rows={series.flatMap((s) =>
          s.points.map((p) => [
            s.label,
            formatX ? formatX(p.x) : p.x,
            formatY(p.y),
          ]),
        )}
      />
    </figure>
  );
}
