"""结构化 + 质控流水线，输出 JSON 与可读 Markdown。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .extract import Record, build_record
from .qc import Finding, run_qc, summarize

SOAP_MAP = {
    "S": [("主诉", "主诉"), ("现病史", "现病史"), ("既往史", "既往史"), ("过敏史", "过敏史"), ("个人史", "个人史"), ("家族史", "家族史")],
    "O": [("查体", "体格检查"), ("辅助检查", "辅助检查")],
    "A": [("诊断", "诊断")],
    "P": [("处理", "处理")],
}

DISCLAIMER = (
    "本工具为文书结构化与规则质控辅助，不是医疗器械，不提供诊断、处方或治疗建议；"
    "所有输出必须由具备资质的医生逐条核对原文后签字使用。"
)


def build_soap(record: Record) -> dict:
    soap: dict[str, list[dict]] = {"S": [], "O": [], "A": [], "P": []}
    for key, entries in SOAP_MAP.items():
        for section_name, label in entries:
            sec = record.section(section_name)
            if not sec or not sec.text.strip():
                continue
            soap[key].append(
                {
                    "label": label,
                    "section": section_name,
                    "text": sec.text.strip(),
                    "span": [sec.start, sec.end],
                    "quote": sec.text.strip()[:80],
                }
            )
    return soap


def _facts(record: Record) -> dict:
    return {
        "vitals": [
            {
                "name": v.name,
                "value": v.value,
                "secondary": v.secondary,
                "unit": v.unit,
                "raw": v.raw,
                "span": list(v.span),
            }
            for v in record.vitals
        ],
        "medications": [
            {
                "name": m.name,
                "dose": m.dose,
                "unit": m.unit,
                "frequency": m.frequency,
                "duration": m.duration,
                "raw": m.raw,
                "source": "plan",
                "span": list(m.span),
            }
            for m in record.medications
        ]
        + [
            {
                "name": m.name,
                "dose": m.dose,
                "unit": m.unit,
                "frequency": m.frequency,
                "duration": m.duration,
                "raw": m.raw,
                "source": "past",
                "span": list(m.span),
            }
            for m in record.past_medications
        ],
        "diagnoses": [
            {"index": d.index, "text": d.text, "uncertain": d.uncertain, "span": list(d.span)}
            for d in record.diagnoses
        ],
    }


def _checklist(findings: list[Finding]) -> list[str]:
    items = []
    for f in findings:
        items.append(f"[{f.severity}] {f.title} —— {f.suggestion}")
    return items


def build_report(text: str, source: str = "") -> dict:
    record = build_record(text, source=source)
    findings = run_qc(record)
    summary = summarize(findings)
    soap = build_soap(record)
    filled = sum(1 for group in soap.values() for item in group)
    return {
        "source": source,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "engine": "clinote-rule-v1",
        "chars": len(text),
        "summary": summary,
        "coverage": {"structured_fields": filled, "with_span": filled},
        "soap": soap,
        "facts": _facts(record),
        "findings": [f.to_dict() for f in findings],
        "review_checklist": _checklist(findings),
        "disclaimer": DISCLAIMER,
    }


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines: list[str] = []
    lines.append(f"# 病历结构化与质控报告：`{report['source']}`")
    lines.append("")
    lines.append(
        f"> 引擎 {report['engine']} ｜ {report['chars']} 字 ｜ "
        f"阻断 {s['blocker']} / 重要 {s['major']} / 提示 {s['minor']}"
    )
    lines.append("")
    lines.append(f"**结论：{s['verdict']}**")
    lines.append("")

    lines.append("## 1. 质控清单")
    lines.append("")
    if not report["findings"]:
        lines.append("规则检查未发现问题。仍需医生核对原文并签字。")
        lines.append("")
    else:
        lines.append("| 级别 | 规则 | 问题 | 建议 | 原文定位 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for f in report["findings"]:
            span = f["span"]
            loc = f"字符 {span[0]}–{span[1]}" if span else "—"
            lines.append(
                f"| {f['severity_label']} | {f['rule_id']} | {_md_escape(f['title'])} | "
                f"{_md_escape(f['suggestion'])} | {loc} |"
            )
        lines.append("")
        lines.append("### 问题详情")
        lines.append("")
        for f in report["findings"]:
            lines.append(f"- **{f['rule_id']} · {f['title']}**（{f['severity_label']}）")
            lines.append(f"  - 问题：{f['message']}")
            lines.append(f"  - 建议：{f['suggestion']}")
            if f["evidence"]:
                lines.append(f"  - 依据原文：“{f['evidence']}”")
        lines.append("")

    lines.append("## 2. SOAP 结构化")
    lines.append("")
    headers = {"S": "S · 主观资料", "O": "O · 客观资料", "A": "A · 评估", "P": "P · 计划"}
    for key in ("S", "O", "A", "P"):
        lines.append(f"### {headers[key]}")
        lines.append("")
        if not report["soap"][key]:
            lines.append("_未记录_")
            lines.append("")
            continue
        for item in report["soap"][key]:
            span = item["span"]
            lines.append(f"- **{item['label']}**（原文 {span[0]}–{span[1]}）：{item['text']}")
        lines.append("")

    lines.append("## 3. 抽取事实")
    lines.append("")
    lines.append("### 生命体征")
    lines.append("")
    if report["facts"]["vitals"]:
        lines.append("| 项目 | 数值 | 单位 | 原文 |")
        lines.append("| --- | --- | --- | --- |")
        for v in report["facts"]["vitals"]:
            val = f"{v['value']:g}" if v["value"] is not None else "—"
            if v["secondary"] is not None:
                val += f"/{v['secondary']:g}"
            lines.append(f"| {v['name']} | {val} | {v['unit'] or '—'} | {_md_escape(v['raw'])} |")
    else:
        lines.append("未识别到生命体征。")
    lines.append("")

    lines.append("### 用药")
    lines.append("")
    if report["facts"]["medications"]:
        lines.append("| 药名 | 剂量 | 频次 | 疗程 | 来源 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for m in report["facts"]["medications"]:
            dose = f"{m['dose']}{m['unit'] or ''}" if m["dose"] else "—"
            source = "本次处理" if m["source"] == "plan" else "既往"
            lines.append(
                f"| {m['name']} | {dose} | {m['frequency'] or '—'} | {m['duration'] or '—'} | {source} |"
            )
    else:
        lines.append("未识别到处方用药。")
    lines.append("")

    lines.append("### 诊断")
    lines.append("")
    if report["facts"]["diagnoses"]:
        for d in report["facts"]["diagnoses"]:
            flag = "（待确认）" if d["uncertain"] else ""
            lines.append(f"{d['index']}. {d['text']}{flag}")
    else:
        lines.append("未识别到诊断。")
    lines.append("")

    lines.append("## 4. 人工复核清单")
    lines.append("")
    if report["review_checklist"]:
        for item in report["review_checklist"]:
            lines.append(f"- [ ] {item}")
    else:
        lines.append("- [ ] 核对 SOAP 与原文一致")
        lines.append("- [ ] 确认诊断、用药、随访计划")
    lines.append("")
    lines.append("## 免责声明")
    lines.append("")
    lines.append(report["disclaimer"])
    lines.append("")
    return "\n".join(lines)


def run(text: str, source: str, out_dir: Path) -> tuple[Path, Path]:
    report = build_report(text, source=source)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(source).stem
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
