"""病历文本解析：段落切分、句子切分、事实抽取。

设计原则：
1. 只抽取原文出现过的内容，每个字段保留字符级 span，方便医生回看原文；
2. 不生成原文没有的阴性/阳性描述，缺失即标记 not_mentioned；
3. 纯标准库实现，方便在没有 GPU、没有外网的医院内网环境跑规则基线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

SECTION_RE = re.compile(r"【([^】]{1,16})】")

# 段落别名归一化
SECTION_ALIASES = {
    "主诉": "主诉",
    "现病史": "现病史",
    "现病 史": "现病史",
    "既往史": "既往史",
    "既往病史": "既往史",
    "过敏史": "过敏史",
    "药物过敏史": "过敏史",
    "查体": "查体",
    "体格检查": "查体",
    "辅助检查": "辅助检查",
    "检查": "辅助检查",
    "诊断": "诊断",
    "初步诊断": "诊断",
    "处理": "处理",
    "处置": "处理",
    "治疗方案": "处理",
    "个人史": "个人史",
    "家族史": "家族史",
    "用药史": "用药史",
}

REQUIRED_SECTIONS = ("主诉", "现病史", "既往史", "查体", "诊断", "处理")

SENTENCE_DELIMITERS = "。；;!?！？\n"


@dataclass
class Section:
    name: str
    raw_name: str
    start: int  # 正文起点（不含【】标题）
    end: int
    text: str


@dataclass
class Sentence:
    text: str
    start: int
    end: int


@dataclass
class Vital:
    name: str
    raw: str
    value: float | None
    secondary: float | None
    unit: str
    span: tuple[int, int]


@dataclass
class Medication:
    name: str
    dose: str | None
    unit: str | None
    frequency: str | None
    duration: str | None
    raw: str
    span: tuple[int, int]


@dataclass
class Diagnosis:
    index: int
    text: str
    uncertain: bool
    span: tuple[int, int]


@dataclass
class Record:
    source: str
    text: str = ""
    sections: list[Section] = field(default_factory=list)
    vitals: list[Vital] = field(default_factory=list)
    medications: list[Medication] = field(default_factory=list)
    past_medications: list[Medication] = field(default_factory=list)
    diagnoses: list[Diagnosis] = field(default_factory=list)
    negations: list[str] = field(default_factory=list)

    def section(self, name: str) -> Section | None:
        for s in self.sections:
            if s.name == name:
                return s
        return None

    def text_of(self, *names: str) -> str:
        return "\n".join(s.text for s in self.sections if s.name in names)

    def has_section(self, name: str) -> bool:
        s = self.section(name)
        return bool(s and s.text.strip())

    @property
    def full_text(self) -> str:
        return self.text


def parse_sections(text: str) -> list[Section]:
    """按【段落名】切分。若文档没有【】标题，则整篇视为“现病史”。"""
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        return [Section("现病史", "现病史", 0, len(text), text)]

    sections: list[Section] = []
    for i, m in enumerate(matches):
        raw_name = m.group(1).strip()
        name = SECTION_ALIASES.get(raw_name, raw_name)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip("\n：: ")
        # 修正 body 真实起点（去掉前置冒号与空白）
        offset = start
        while offset < end and text[offset] in "：: \t\n":
            offset += 1
        sections.append(Section(name, raw_name, offset, end, text[offset:end].strip()))
    return sections


def split_sentences(section: Section) -> list[Sentence]:
    out: list[Sentence] = []
    start = section.start
    buf_start = 0
    body = section.text
    for i, ch in enumerate(body):
        if ch in SENTENCE_DELIMITERS:
            piece = body[buf_start : i + 1].strip()
            if piece:
                out.append(Sentence(piece, section.start + buf_start, section.start + i + 1))
            buf_start = i + 1
    tail = body[buf_start:].strip()
    if tail:
        out.append(Sentence(tail, section.start + buf_start, section.end))
    return out


VITAL_RE = re.compile(
    r"(BP|血压|T|体温|P|脉搏|HR|心率|R|呼吸|SpO2|血氧饱和度)\s*[：:]?\s*"
    r"([0-9]+(?:\.[0-9]+)?(?:\s*/\s*[0-9]+(?:\.[0-9]+)?)?)\s*"
    r"(℃|°C|次/分|bpm|mmHg|%)?"
)

MED_RE = re.compile(
    r"([A-Za-z\u4e00-\u9fa5][A-Za-z\u4e00-\u9fa5\-]{1,14})"
    r"\s*(\d+(?:\.\d+)?)\s*(mg|g|ml|mL|μg|ug|IU|单位|片|粒|袋|支)"
)
FREQ_RE = re.compile(
    r"(qd|qn|bid|tid|qid|q8h|q12h|每日一次|每日两次|每日三次|每日四次|每晚一次|每晚|必要时)"
)
DURATION_RE = re.compile(r"(\d+\s*(?:天|日|周|月|年))")

NEGATION_RE = re.compile(r"(?:否认|无明显|无)\s*([\u4e00-\u9fa5A-Za-z]{2,12})")

VITAL_NAMES = {
    "BP": "血压",
    "血压": "血压",
    "T": "体温",
    "体温": "体温",
    "P": "脉搏",
    "脉搏": "脉搏",
    "HR": "心率",
    "心率": "心率",
    "R": "呼吸",
    "呼吸": "呼吸",
    "SpO2": "血氧饱和度",
    "血氧饱和度": "血氧饱和度",
}


def _vital_value(raw: str) -> tuple[float | None, float | None]:
    raw = raw.replace(" ", "")
    if "/" in raw:
        a, b = raw.split("/", 1)
        try:
            return float(a), float(b)
        except ValueError:
            return None, None
    try:
        return float(raw), None
    except ValueError:
        return None, None


def extract_vitals(text: str, base_offset: int = 0) -> list[Vital]:
    out: list[Vital] = []
    for m in VITAL_RE.finditer(text):
        name = VITAL_NAMES.get(m.group(1), m.group(1))
        value, secondary = _vital_value(m.group(2))
        unit = m.group(3) or ""
        if value is None:
            continue
        # 体温缺单位时补默认，方便规则判断
        if name == "体温" and not unit:
            unit = "℃"
        out.append(
            Vital(
                name=name,
                raw=m.group(0),
                value=value,
                secondary=secondary,
                unit=unit,
                span=(base_offset + m.start(), base_offset + m.end()),
            )
        )
    return out


def extract_medications(text: str, base_offset: int = 0) -> list[Medication]:
    out: list[Medication] = []
    for m in MED_RE.finditer(text):
        tail = text[m.end() : m.end() + 24]
        freq_m = FREQ_RE.search(tail)
        dur_m = DURATION_RE.search(tail)
        name = re.sub(
            r"^(?:规律)?(?:服用|口服|使用|予|静滴|静脉注射|肌注|皮下注射|吸入)",
            "",
            m.group(1),
        )
        out.append(
            Medication(
                name=name,
                dose=m.group(2),
                unit=m.group(3),
                frequency=freq_m.group(1) if freq_m else None,
                duration=dur_m.group(1) if dur_m else None,
                raw=m.group(0),
                span=(base_offset + m.start(), base_offset + m.end()),
            )
        )
    return out


def extract_diagnoses(text: str, base_offset: int = 0) -> list[Diagnosis]:
    """支持“1. A；2. B”“A；B”“A 2. B”等写法，并保留字符偏移。"""
    sep_re = re.compile(r"[；;]|\s+\d{1,2}[\.、]\s*|^\s*\d{1,2}[\.、]\s*")
    seps = list(sep_re.finditer(text))
    parts: list[tuple[str, int]] = []
    start = 0
    for sep in seps:
        piece = text[start : sep.start()]
        if piece.strip():
            lead = len(piece) - len(piece.lstrip())
            parts.append((piece.strip(), start + lead))
        start = sep.end()
    tail = text[start:]
    if tail.strip():
        lead = len(tail) - len(tail.lstrip())
        parts.append((tail.strip(), start + lead))

    out: list[Diagnosis] = []
    for idx, (part, rel_start) in enumerate(parts, start=1):
        part = re.sub(r"^\s*[\d一二三四五六七八九十]+[\.、,，)]\s*", "", part).strip()
        if not part:
            continue
        uncertain = any(k in part for k in ("？", "?", "待查", "可能", "疑"))
        out.append(
            Diagnosis(
                index=idx,
                text=part,
                uncertain=uncertain,
                span=(base_offset + rel_start, base_offset + rel_start + len(part)),
            )
        )
    return out


def extract_negations(text: str) -> list[str]:
    return [m.group(1).strip() for m in NEGATION_RE.finditer(text)]


def build_record(text: str, source: str = "") -> Record:
    sections = parse_sections(text)
    record = Record(source=source or "inline", text=text, sections=sections)

    vitals_text = record.text_of("查体", "辅助检查", "现病史")
    for s in sections:
        if s.name in ("查体", "辅助检查", "现病史"):
            record.vitals.extend(extract_vitals(s.text, s.start))

    plan = record.section("处理")
    if plan:
        record.medications = extract_medications(plan.text, plan.start)
    # 既往史里正在服用的药单独保留，避免和本次处方混淆
    past = record.section("既往史")
    record.past_medications = extract_medications(past.text, past.start) if past else []

    diag = record.section("诊断")
    if diag:
        record.diagnoses = extract_diagnoses(diag.text, diag.start)

    record.negations = extract_negations(text)
    return record


def iter_sentences(record: Record) -> Iterable[Sentence]:
    for s in record.sections:
        yield from split_sentences(s)
