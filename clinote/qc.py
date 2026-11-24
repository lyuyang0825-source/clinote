"""病历质控规则引擎。

规则只做“提示”，不做诊断；每条 finding 必须带原文位置，方便医生回看。
规则编号在 README 中公开，医院可按科室自定义阈值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from .extract import Record, REQUIRED_SECTIONS

SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2}
SEVERITY_LABEL = {"blocker": "阻断", "major": "重要", "minor": "提示"}


@dataclass
class Finding:
    rule_id: str
    severity: str
    title: str
    message: str
    suggestion: str
    evidence: str = ""
    span: tuple[int, int] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = list(self.span) if self.span else None
        d["severity_label"] = SEVERITY_LABEL[self.severity]
        return d


def _locate(record: Record, keyword: str) -> tuple[int, int] | None:
    idx = record.text.find(keyword)
    if idx < 0:
        return None
    return (idx, idx + len(keyword))


def _finding(
    record: Record,
    rule_id: str,
    severity: str,
    title: str,
    message: str,
    suggestion: str,
    evidence: str = "",
) -> Finding:
    span = _locate(record, evidence) if evidence else None
    return Finding(
        rule_id=rule_id,
        severity=severity,
        title=title,
        message=message,
        suggestion=suggestion,
        evidence=evidence,
        span=span,
    )


def rule_required_sections(record: Record) -> list[Finding]:
    out = []
    for name in REQUIRED_SECTIONS:
        if record.has_section(name):
            continue
        severity = "blocker" if name in ("主诉", "诊断", "处理") else "major"
        out.append(
            Finding(
                rule_id="R001",
                severity=severity,
                title=f"缺少必填段落：{name}",
                message=f"记录中未找到【{name}】段落或内容为空，病历完整性不达标。",
                suggestion=f"补充【{name}】；若确实不适用，请写明“无”或“未记录”，不要留空。",
            )
        )
    return out


CHEST_FACETS = {
    "部位": ("胸骨后", "心前区", "左侧", "右侧", "剑突下", "部位"),
    "性质": ("压榨", "闷", "刺痛", "绞痛", "烧灼", "性质"),
    "持续时间": ("分钟", "小时", "持续", "秒"),
    "诱因": ("诱因", "劳累", "活动", "情绪", "饱餐", "寒冷"),
    "缓解方式": ("缓解", "休息", "硝酸甘油"),
    "伴随症状": ("放射", "出汗", "大汗", "呼吸困难", "晕厥", "恶心"),
}


NEGATIONS = ("无", "未见", "否认", "不伴", "没有", "未诉")


def _positive_mention(text: str, keyword: str) -> bool:
    """关键词出现且不是“无胸痛/否认胸痛”这类否定表述。"""
    for m in re.finditer(keyword, text):
        prefix = text[max(0, m.start() - 6):m.start()]
        if any(prefix.endswith(neg) for neg in NEGATIONS):
            continue
        return True
    return False


def rule_chest_symptom(record: Record) -> list[Finding]:
    history = record.text_of("现病史", "主诉")
    keywords = ("胸痛", "胸闷", "胸部不适")
    if not any(_positive_mention(history, k) for k in keywords):
        return []
    missing = []
    for facet, keys in CHEST_FACETS.items():
        if not any(k in history for k in keys):
            missing.append(facet)
    if not missing:
        return []
    return [
        _finding(
            record,
            "R002",
            "major",
            "胸痛/胸闷描述要素不全",
            "胸痛/胸闷评估建议覆盖：部位、性质、持续时间、诱因、缓解方式、伴随症状。当前缺少：" + "、".join(missing),
            "补齐缺失要素；对急性胸痛建议同时记录生命体征与心电图时间点，避免只写“胸闷待查”。",
            evidence="胸闷" if _positive_mention(history, "胸闷") else "胸痛",
        )
    ]


def rule_vitals(record: Record) -> list[Finding]:
    out = []
    by_name: dict[str, list] = {}
    for v in record.vitals:
        by_name.setdefault(v.name, []).append(v)

    for v in by_name.get("血压", []):
        if v.secondary is None:
            out.append(
                _finding(
                    record, "R003", "major", "血压记录不完整",
                    f"血压“{v.raw}”缺少舒张压，无法判断分级。",
                    "按“收缩压/舒张压 mmHg”记录，例如 158/96mmHg。", v.raw,
                )
            )
            continue
        sys, dia = v.value or 0, v.secondary or 0
        if sys >= 180 or dia >= 110:
            out.append(
                _finding(
                    record, "R003", "blocker", "血压达到危急值阈值",
                    f"实测血压 {sys:.0f}/{dia:.0f}mmHg，达到 180/110mmHg 危急阈值，需立即复核并评估靶器官损害。",
                    "复测血压、记录复测时间；按院内危急值流程处理并记录处置。", v.raw,
                )
            )
        elif sys >= 140 or dia >= 90:
            out.append(
                _finding(
                    record, "R003", "major", "血压高于目标值",
                    f"实测血压 {sys:.0f}/{dia:.0f}mmHg，高于一般门诊控制目标（<140/90mmHg）。",
                    "结合既往史与用药依从性评估；如为首次发现，建议不同日复测并记录。", v.raw,
                )
            )
    for v in by_name.get("心率", []) + by_name.get("脉搏", []):
        if v.value and (v.value > 100 or v.value < 50):
            out.append(
                _finding(
                    record, "R003", "major", "心率异常",
                    f"心率/脉搏 {v.value:.0f} 次/分，超出 50–100 次/分范围。",
                    "确认是否为静息状态测量，必要时记录心律与症状。", v.raw,
                )
            )
    for v in by_name.get("体温", []):
        if v.value and v.value >= 38.0:
            out.append(
                _finding(
                    record, "R003", "major", "体温升高",
                    f"体温 {v.value:.1f}℃，达到发热标准。",
                    "记录发热持续时间、伴随症状与已用退热措施。", v.raw,
                )
            )
    for v in by_name.get("血氧饱和度", []):
        if v.value and v.value < 94:
            out.append(
                _finding(
                    record, "R003", "blocker", "血氧饱和度偏低",
                    f"SpO2 {v.value:.0f}%，低于 94%。",
                    "复测并记录吸氧状态、呼吸频率与肺部体征。", v.raw,
                )
            )
    if not by_name:
        out.append(
            Finding(
                rule_id="R011", severity="minor", title="查体缺少生命体征",
                message="【查体】中未识别到体温、脉搏、呼吸、血压等生命体征。",
                suggestion="至少记录 T/P/R/BP，并注明测量时间与体位。",
            )
        )
    return out


def rule_medications(record: Record) -> list[Finding]:
    out = []
    if not record.has_section("处理"):
        return out
    if not record.medications:
        out.append(
            Finding(
                rule_id="R004", severity="minor", title="处理段未识别到用药",
                message="【处理】中未识别到“药名+剂量+频次”结构的处方信息。",
                suggestion="如有处方，按“药名 剂量 频次 疗程”记录；如未开药，写明“暂不处方”。",
            )
        )
        return out
    for med in record.medications:
        if med.dose is None:
            out.append(
                _finding(
                    record, "R004", "blocker", f"用药缺少剂量：{med.name}",
                    f"“{med.raw}”未识别到剂量，存在给药差错风险。",
                    "补全单次剂量与单位（如 100mg），并核对最大日剂量。", med.raw,
                )
            )
        if med.frequency is None:
            out.append(
                _finding(
                    record, "R004", "major", f"用药缺少频次：{med.name}",
                    f"“{med.raw}”未识别到给药频次。",
                    "补全 qd/bid/tid 等频次，或写明“按需”。", med.raw,
                )
            )
        if med.duration is None:
            out.append(
                _finding(
                    record, "R004", "minor", f"用药缺少疗程：{med.name}",
                    f"“{med.raw}”未写明疗程或复诊时间点。",
                    "补全疗程（如 7 天）或“长期服用，X 周后复诊”。", med.raw,
                )
            )
    return out


def rule_uncertain_diagnosis(record: Record) -> list[Finding]:
    uncertain = [d for d in record.diagnoses if d.uncertain]
    if not uncertain:
        return []
    plan = record.text_of("处理", "辅助检查")
    workup_keys = ("CTA", "CT", "超声", "造影", "化验", "复查", "收住", "入院", "MRI", "磁共振", "心电图", "holter", "动态")
    if any(k.lower() in plan.lower() for k in workup_keys):
        return []
    return [
        _finding(
            record, "R005", "major", "待查诊断缺少进一步检查计划",
            f"诊断“{uncertain[0].text}”为待查/疑似，但处理段未见对应的检查或收住安排。",
            "补充确认性检查、观察计划或转诊安排，并写明时间窗。", uncertain[0].text,
        )
    ]


def rule_followup(record: Record) -> list[Finding]:
    plan = record.text_of("处理")
    if not any(k in plan for k in ("随诊", "复诊", "随访", "复查")):
        return []
    window = re.search(r"(\d+\s*(?:天|日|周|个月|月|年)后?)|(?:一周|两周|一月|1个月|三个月)", plan)
    if window:
        return []
    return [
        _finding(
            record, "R006", "minor", "随访未写明时间窗",
            "处理段要求随诊/复诊，但未写明确切时间（如“2 周后复诊”）。",
            "补全复诊时间、地点与需要复查的项目，方便患者执行。", "随诊",
        )
    ]


def rule_contradictions(record: Record) -> list[Finding]:
    out = []
    defined_dx = "；".join(d.text for d in record.diagnoses if not d.uncertain)
    for term in record.negations:
        if len(term) >= 2 and term in defined_dx:
            out.append(
                _finding(
                    record, "R007", "major", f"既往史与诊断矛盾：{term}",
                    f"记录中写“否认{term}”，但诊断包含“{term}”，两者矛盾。",
                    "核对病史与诊断；如为新发诊断，改写为“既往否认，本次发现”，并说明依据。", f"否认{term}",
                )
            )

    target = re.search(r"血压控制在\s*(\d{2,3})\s*/\s*(\d{2,3})", record.text_of("既往史", "现病史"))
    measured = next((v for v in record.vitals if v.name == "血压" and v.secondary), None)
    if target and measured:
        t_sys, t_dia = int(target.group(1)), int(target.group(2))
        if measured.value and (measured.value - t_sys >= 20 or measured.secondary - t_dia >= 15):
            out.append(
                _finding(
                    record, "R007", "major", "自述血压控制值与实测不符",
                    f"病史称血压控制在 {t_sys}/{t_dia}mmHg，本次实测 {measured.value:.0f}/{measured.secondary:.0f}mmHg，差值较大。",
                    "注明测量条件（诊室/家庭、时间、是否服药），并评估依从性与白大衣效应。", measured.raw,
                )
            )
    return out


def rule_allergy(record: Record) -> list[Finding]:
    if record.has_section("过敏史"):
        return []
    return [
        Finding(
            rule_id="R008", severity="minor", title="未记录过敏史",
            message="未找到【过敏史】段落；开具处方前应有过敏史记录。",
            suggestion="补充“否认药物/食物过敏”或具体过敏原与反应类型。",
        )
    ]


ANTIHYPERTENSIVES = (
    "氨氯地平", "硝苯地平", "非洛地平", "尼群地平", "左氨氯地平",
    "缬沙坦", "厄贝沙坦", "氯沙坦", "替米沙坦", "奥美沙坦", "阿利沙坦",
    "美托洛尔", "比索洛尔", "阿替洛尔",
    "氢氯噻嗪", "吲达帕胺", "呋塞米",
    "培哚普利", "依那普利", "贝那普利", "卡托普利", "雷米普利",
    "沙库巴曲缬沙坦", "可乐定", "哌唑嗪",
)


def rule_dx_med_match(record: Record) -> list[Finding]:
    dx_text = "；".join(d.text for d in record.diagnoses)
    if "高血压" not in dx_text:
        return []
    plan_has = any(any(a in m.name for a in ANTIHYPERTENSIVES) for m in record.medications)
    if plan_has:
        return []
    past = [m for m in record.past_medications if any(a in m.name for a in ANTIHYPERTENSIVES)]
    past_hint = f"；既往在用：{'、'.join(m.name for m in past)}" if past else ""
    return [
        _finding(
            record, "R010", "major", "诊断高血压但处理段未见降压方案",
            "诊断包含高血压，但本次【处理】未记录降压药调整或血压管理计划" + past_hint + "。",
            "明确本次是否继续原方案、调整剂量或转专科；若仅随访，写明“维持原方案，X 周复诊”。", "高血压",
        )
    ]


def rule_chief_complaint(record: Record) -> list[Finding]:
    cc = record.text_of("主诉")
    if not cc:
        return []
    if re.search(r"(\d+\s*(?:小时|天|日|周|个月|月|年))|(?:半天|数小时|数天|余天|余年)", cc):
        return []
    return [
        _finding(
            record, "R009", "major", "主诉缺少持续时间",
            f"主诉“{cc.strip()}”未写明症状持续时间。",
            "按“症状 + 持续时间”书写，例如“反复胸闷 3 天”。", cc.strip(),
        )
    ]


ALL_RULES = (
    rule_required_sections,
    rule_chief_complaint,
    rule_chest_symptom,
    rule_vitals,
    rule_medications,
    rule_uncertain_diagnosis,
    rule_followup,
    rule_dx_med_match,
    rule_contradictions,
    rule_allergy,
)


def run_qc(record: Record) -> list[Finding]:
    findings: list[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(record))
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.rule_id))
    return findings


def summarize(findings: list[Finding]) -> dict:
    counts = {"blocker": 0, "major": 0, "minor": 0}
    for f in findings:
        counts[f.severity] += 1
    if counts["blocker"]:
        verdict = "不可归档：需先处理阻断级问题"
    elif counts["major"]:
        verdict = "建议修改后归档"
    elif counts["minor"]:
        verdict = "可归档，建议补充提示项"
    else:
        verdict = "规则检查通过（仍需医生签字确认）"
    return {**counts, "total": len(findings), "verdict": verdict}
