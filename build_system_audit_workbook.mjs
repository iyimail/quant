import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("summary.json ve output.xlsx gerekli");
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const font = "Arial";
const col = n => { let r = ""; while (n) { n--; r = String.fromCharCode(65 + n % 26) + r; n = Math.floor(n / 26); } return r; };
const header = (sheet, range) => { sheet.getRange(range).format = { fill: "#1F4E78", font: { name: font, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center" }; };

const summary = workbook.worksheets.add("Denetim Özeti");
summary.showGridLines = false;
summary.getRange("A2:H2").merge();
summary.getRange("A2").values = [["Local Quant Lab Sistem Denetimi"]];
summary.getRange("A2").format = { font: { name: font, size: 15, bold: true, color: "#111827" } };
const agent = data.audit_agent ?? {};
const facts = [
  ["Denetim kimliği", `ID: ${data.audit_id ?? ""}`], ["Üretim zamanı UTC", `UTC: ${data.generated_at_utc ?? ""}`],
  ["Denetim Agentı", agent.name ?? ""], ["Agent kararı", agent.verdict ?? "WAITING"], ["Karar gerekçesi", agent.reason ?? ""],
  ["Kural", "Her alan/aralık için en fazla üç örnek değer; bir-faktör testleri"],
];
summary.getRange(`A4:B${3 + facts.length}`).values = facts;
summary.getRange("A4:A9").format = { fill: "#E5E7EB", font: { name: font, bold: true, color: "#111827" } };
summary.getRange("A:B").format.font = { name: font, size: 10 };
// Audit IDs and ISO timestamps are evidence labels, not spreadsheet dates.
// Preserve them literally so Excel does not show a serial date in an adjacent cell.
summary.getRange("B4:B9").format.numberFormat = "@";
summary.getRange("A:A").format.columnWidth = 25; summary.getRange("B:B").format.columnWidth = 95;

const jobs = data.jobs ?? [];
const metrics = [["Planlanan iş", jobs.length], ["Tamamlanan iş", jobs.filter(x => x.job_status === "COMPLETE").length], ["Başarısız iş", jobs.filter(x => x.job_status === "FAILED").length], ["Atlanan koşu", jobs.reduce((n, x) => n + Number(x.skipped_runs || 0), 0)]];
summary.getRange("D4:E7").values = metrics; header(summary, "D4:E4"); summary.getRange("D4:E7").format.font = { name: font, size: 10 }; summary.getRange("D:D").format.columnWidth = 24; summary.getRange("E:E").format.columnWidth = 12;

const detail = workbook.worksheets.add("Denetim İşleri"); detail.showGridLines = false;
const detailHeaders = ["İş", "Denetim ekseni", "Durum", "Planlanan", "Tamamlanan", "Atlanan", "Net P&L", "Ort. PF", "Maks. DD", "Örnek değerler"];
const detailRows = jobs.map(x => [x.label, x.axis, x.job_status, x.planned_runs, x.complete_runs, x.skipped_runs, x.net_pnl_usdt, x.profit_factor, x.max_drawdown_usdt, JSON.stringify(x.sampled_values ?? [])]);
detail.getRange(`A1:${col(detailHeaders.length)}1`).values = [detailHeaders]; header(detail, `A1:${col(detailHeaders.length)}1`);
if (detailRows.length) { detail.getRange(`A2:${col(detailHeaders.length)}${detailRows.length + 1}`).values = detailRows; detail.tables.add(`A1:${col(detailHeaders.length)}${detailRows.length + 1}`, true, "SystemAuditJobs"); }
detail.getRange(`A:${col(detailHeaders.length)}`).format.font = { name: font, size: 9 }; detail.getRange(`A:${col(detailHeaders.length)}`).format.autofitColumns(); detail.getRange("B:B").format.columnWidth = 30; detail.getRange("J:J").format.columnWidth = 32; detail.freezePanes.freezeRows(1);

const rec = workbook.worksheets.add("Alan Değer Adayları"); rec.showGridLines = false;
const recHeaders = ["Alan / test ekseni", "Örneklenen değerler", "Net P&L", "Ort. PF", "Maks. DD", "Kanıt"];
const recRows = (data.recommendations ?? []).map(x => [x.axis, JSON.stringify(x.sampled_values), x.net_pnl_usdt, x.profit_factor, x.max_drawdown_usdt, x.evidence]);
rec.getRange(`A1:${col(recHeaders.length)}1`).values = [recHeaders]; header(rec, `A1:${col(recHeaders.length)}1`);
if (recRows.length) { rec.getRange(`A2:${col(recHeaders.length)}${recRows.length + 1}`).values = recRows; rec.tables.add(`A1:${col(recHeaders.length)}${recRows.length + 1}`, true, "AuditCandidates"); }
rec.getRange(`A:${col(recHeaders.length)}`).format.font = { name: font, size: 10 }; rec.getRange(`A:${col(recHeaders.length)}`).format.autofitColumns(); rec.getRange("A:B").format.columnWidth = 36; rec.freezePanes.freezeRows(1);

workbook.recalculate();
await fs.mkdir(path.dirname(outputPath), { recursive: true });
const preview = await workbook.render({ sheetName: "Denetim Özeti", range: "A1:H12", scale: 1.2, format: "png" });
await fs.writeFile(path.join(path.dirname(outputPath), "sistem_denetim_onizleme.png"), new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
