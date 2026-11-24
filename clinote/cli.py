"""clinote CLI：python -m clinote.cli --input samples/outpatient_zh_01.txt --out-dir demo_out"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import build_report, render_markdown, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="病历结构化 + 规则质控（非医疗器械）")
    parser.add_argument("--input", "-i", help="病历文本文件（UTF-8）")
    parser.add_argument("--text", help="直接传入病历文本（与 --input 二选一）")
    parser.add_argument("--out-dir", default="demo_out", help="输出目录")
    parser.add_argument("--stdout", action="store_true", help="同时打印 Markdown")
    args = parser.parse_args(argv)

    if bool(args.input) == bool(args.text):
        parser.error("请提供 --input 或 --text 之一")
    if args.input:
        path = Path(args.input)
        text = path.read_text(encoding="utf-8")
        source = path.name
    else:
        text = args.text
        source = "inline.txt"

    json_path, md_path = run(text, source, Path(args.out_dir))
    report = json.loads(json_path.read_text(encoding="utf-8"))
    s = report["summary"]
    print(f"[clinote] {source}: 阻断 {s['blocker']} / 重要 {s['major']} / 提示 {s['minor']}")
    print(f"[clinote] 结论: {s['verdict']}")
    print(f"[clinote] 输出: {json_path} , {md_path}")
    if args.stdout:
        print("\n" + render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
