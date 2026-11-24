"""clinote: 中文门诊病历结构化与可溯源质控（规则基线）。

非医疗器械，输出仅供文书辅助，必须由医生核对原文后使用。
"""

__version__ = "0.1.0"

from .extract import build_record, parse_sections
from .qc import run_qc, summarize
from .pipeline import build_report, build_soap, render_markdown

__all__ = [
    "build_record",
    "parse_sections",
    "run_qc",
    "summarize",
    "build_report",
    "build_soap",
    "render_markdown",
]
