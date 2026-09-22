import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = process.argv[2];
const outputPath = process.argv[3];
if (!inputPath || !outputPath) {
  throw new Error("Kullanım: node build_job_workbook.mjs input.json output.xlsx");
}

const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const job = payload.job ?? {};
const rows = payload.rows ?? [];
const font = "Arial";
const workbook = Workbook.create();

const numeric = (value) => typeof value === "number" && Number.isFinite(value);
const median = (values) => {
  const sorted = values.filter(numeric).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

const groups = new Map();
for (const row of rows) {
  if (row.status !== "COMPLETE") continue;
  const key = `${row.combo_id}__${row.parameter_id}`;
  if (!groups.has(key)) groups.set(key, []);
  groups.get(key).push(row);
}
const summaryRows = [...groups.entries()].map(([key, items]) => ({
  combination: items[0]?.combo_id ?? key,
  parameter_id: items[0]?.parameter_id ?? "",
  parameter_overrides: items[0]?.parameter_overrides ?? "{}",
  tested_symbols: items.length,
  profitable_symbols: items.filter((item) => Number(item.total_pnl_usdt) > 0).length,
  total_pnl_usdt: items.reduce((sum, item) => sum + Number(item.total_pnl_usdt || 0), 0),
  median_pnl_usdt: median(items.map((item) => Number(item.total_pnl_usdt))),
  median_profit_factor: median(items.map((item) => Number(item.profit_factor))),
  median_drawdown_pct: median(items.map((item) => Number(item.max_drawdown_pct_initial))),
  worst_drawdown_pct: Math.max(...items.map((item) => Number(item.max_drawdown_pct_initial || 0))),
  total_trades: items.reduce((sum, item) => sum + Number(item.closed_trades || 0), 0),
})).sort((a, b) =>
  b.profitable_symbols - a.profitable_symbols ||
  b.median_profit_factor - a.median_profit_factor ||
  b.total_pnl_usdt - a.total_pnl_usdt
);

const summary = workbook.worksheets.add("Özet");
summary.getRange("A2:I2").merge();
summary.getRange("A2").values = [["Local Quant Lab Test Sonuçları"]];
summary.getRange("A2").format = { font: { name: font, size: 15, bold: true, color: "#111827" } };
summary.getRange("A3:I3").merge();
summary.getRange("A3").values = [["Bu sonuçlar RESEARCH_APPROXIMATION düzeyindedir ve production kararı değildir."]];
summary.getRange("A3").format = { font: { name: font, size: 10, italic: true, color: "#92400E" }, fill: "#FEF3C7" };

const info = [
  ["İş", job.id ?? ""],
  ["Dönem", `${job.start ?? ""} - ${job.end ?? ""}`],
  ["Coin sayısı", (job.symbols ?? []).length],
  ["Planlanan koşu", job.planned_runs ?? rows.length],
  ["Tamamlanan koşu", rows.filter((row) => row.status === "COMPLETE").length],
  ["Motor durumu", "RESEARCH_APPROXIMATION"],
];
summary.getRange(`A5:B${4 + info.length}`).values = info;
summary.getRange("A5:A10").format = { fill: "#E5E7EB", font: { name: font, bold: true, color: "#111827" } };
summary.getRange("B5:B10").format.font = { name: font, color: "#111827" };

const summaryHeaders = ["Sıra", "Kombinasyon", "Parametre", "Parametre değerleri", "Test edilen coin", "Pozitif coin", "Toplam PnL (USDT)", "Medyan PF", "En kötü DD (%)"];
const summaryValues = summaryRows.map((row, index) => [
  index + 1, row.combination, row.parameter_id, row.parameter_overrides,
  row.tested_symbols, row.profitable_symbols, row.total_pnl_usdt,
  row.median_profit_factor, row.worst_drawdown_pct,
]);
const summaryStart = 13;
summary.getRange(`A${summaryStart}:I${summaryStart}`).values = [summaryHeaders];
if (summaryValues.length) summary.getRange(`A${summaryStart + 1}:I${summaryStart + summaryValues.length}`).values = summaryValues;
summary.getRange(`A${summaryStart}:I${summaryStart}`).format = { fill: "#1F4E78", font: { name: font, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center" };
if (summaryValues.length) {
  summary.tables.add(`A${summaryStart}:I${summaryStart + summaryValues.length}`, true, "CombinationSummaryTable");
  summary.getRange(`G${summaryStart + 1}:H${summaryStart + summaryValues.length}`).format.numberFormat = "0.00";
  summary.getRange(`I${summaryStart + 1}:I${summaryStart + summaryValues.length}`).format.numberFormat = "0.00";
}
summary.getRange("A:I").format.font = { name: font, size: 10 };
summary.getRange("A:I").format.autofitColumns();
summary.getRange("B:B").format.columnWidth = 34;
summary.getRange("D:D").format.columnWidth = 34;
summary.freezePanes.freezeRows(summaryStart);

const detail = workbook.worksheets.add("Tüm Sonuçlar");
const preferred = [
  "symbol", "combo_id", "parameter_id", "parameter_overrides", "exit_family", "er",
  "winrate_enabled", "close_on_block", "closed_trades", "wins", "win_rate_pct",
  "total_pnl_usdt", "profit_factor", "commission_usdt", "max_drawdown_usdt",
  "max_drawdown_pct_initial", "shadow_trades", "status", "engine_status", "production_eligible",
];
const allKeys = [...new Set(rows.flatMap((row) => Object.keys(row)))];
const headers = [...preferred.filter((key) => allKeys.includes(key)), ...allKeys.filter((key) => !preferred.includes(key))];
detail.getRange(`A1:${columnName(headers.length)}1`).values = [headers];
if (rows.length) {
  detail.getRange(`A2:${columnName(headers.length)}${rows.length + 1}`).values = rows.map((row) => headers.map((key) => row[key] ?? null));
  detail.tables.add(`A1:${columnName(headers.length)}${rows.length + 1}`, true, "AllResultsTable");
}
detail.getRange(`A1:${columnName(headers.length)}1`).format = { fill: "#1F4E78", font: { name: font, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center" };
detail.getRange(`A:${columnName(headers.length)}`).format.font = { name: font, size: 9 };
detail.getRange(`A:${columnName(headers.length)}`).format.autofitColumns();
for (const key of ["combo_id", "parameter_overrides", "engine_status"]) {
  const index = headers.indexOf(key);
  if (index >= 0) detail.getRange(`${columnName(index + 1)}:${columnName(index + 1)}`).format.columnWidth = key === "parameter_overrides" ? 38 : 30;
}
detail.freezePanes.freezeRows(1);

const parameters = workbook.worksheets.add("Test Tanımı");
parameters.getRange("A2:D2").merge();
parameters.getRange("A2").values = [["Test Tanımı"]];
parameters.getRange("A2").format = { font: { name: font, size: 14, bold: true } };
const definition = [
  ["Alan", "Değer"],
  ["İş kimliği", job.id ?? ""],
  ["Açıklama", job.description ?? ""],
  ["Başlangıç", job.start ?? ""],
  ["Bitiş", job.end ?? ""],
  ["Coinler", (job.symbols ?? []).join(", ")],
  ["Çıkış aileleri", (job.categorical?.exit_family ?? []).join(", ")],
  ["ER seçenekleri", (job.categorical?.er ?? []).join(", ")],
  ["Winrate seçenekleri", (job.categorical?.winrate_state ?? []).join(", ")],
  ["Parametre ızgarası", JSON.stringify(job.parameter_grid ?? {})],
  ["Kanıt durumu", "RESEARCH_APPROXIMATION"],
  ["Production uygunluğu", false],
];
parameters.getRange(`A4:B${3 + definition.length}`).values = definition;
parameters.getRange("A4:B4").format = { fill: "#1F4E78", font: { name: font, bold: true, color: "#FFFFFF" } };
parameters.getRange(`A4:B${3 + definition.length}`).format.font = { name: font, size: 10 };
parameters.getRange("A:B").format.autofitColumns();
parameters.getRange("B:B").format.columnWidth = 70;

workbook.recalculate();
await fs.mkdir(path.dirname(outputPath), { recursive: true });
const preview = await workbook.render({ sheetName: "Özet", range: `A1:I${Math.min(summaryStart + summaryValues.length, 45)}`, scale: 1.2, format: "png" });
await fs.writeFile(path.join(path.dirname(outputPath), "sonuc_onizleme.png"), new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

function columnName(number) {
  let result = "";
  while (number > 0) {
    number -= 1;
    result = String.fromCharCode(65 + (number % 26)) + result;
    number = Math.floor(number / 26);
  }
  return result || "A";
}
