/**
 * Client-side CSV export.
 *
 * The anomaly table and reports allow a CSV download (PRD §4.3, §4.7). Values
 * are escaped per RFC 4180 so a note containing a comma or quote cannot corrupt
 * the file, and the browser is handed a Blob rather than a data URL.
 */

function escapeCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  let text = String(value);
  // Spreadsheet formula injection: a *string* cell beginning with =, +, -
  // or @ is executed as a formula when the export is opened in Excel /
  // Sheets. Device ids, anomaly types and notes are operator/backend
  // controlled, so neutralise the leading character with a quote prefix.
  // Genuine numbers (typed, or plain numeric strings like "-3.2") are
  // values, not formulas, and are left untouched.
  if (
    typeof value === "string" &&
    /^\s*[=+@\t-]/.test(text) &&
    !/^\s*-?\d+(\.\d+)?$/.test(text)
  ) {
    text = `'${text}`;
  }
  if (/[",\n\r]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

export function toCsv(
  columns: { key: string; header: string }[],
  rows: Record<string, unknown>[],
): string {
  const head = columns.map((c) => escapeCell(c.header)).join(",");
  const body = rows
    .map((row) => columns.map((c) => escapeCell(row[c.key])).join(","))
    .join("\r\n");
  return `${head}\r\n${body}`;
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  // Revoking synchronously can cancel the download in some browsers for
  // larger blobs; defer it until the click has been processed.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
