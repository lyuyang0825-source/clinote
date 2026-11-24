import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clinote import build_record, build_report, run_qc  # noqa: E402
from clinote.pipeline import render_markdown  # noqa: E402

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "outpatient_zh_01.txt"

CLEAN = """【主诉】反复胸闷3天。
【现病史】患者3天前劳累后出现胸闷，位于胸骨后，呈压榨性，持续约5分钟，休息后缓解，无放射痛，无大汗，无晕厥。
【既往史】否认高血压、糖尿病、冠心病。
【过敏史】否认药物过敏。
【查体】T 36.5℃，P 78次/分，R 18次/分，BP 132/82mmHg。神清，双肺呼吸音清。
【辅助检查】心电图：窦性心律，未见ST段改变。
【诊断】1. 胸闷待查：冠心病？
【处理】1. 收住心内科，完善冠脉CTA。2. 阿司匹林100mg qd 7天，阿托伐他汀20mg qn 7天。3. 2周后心内科复诊。
"""


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.text = SAMPLE.read_text(encoding="utf-8")
        self.record = build_record(self.text, "outpatient_zh_01.txt")

    def test_sections(self):
        names = [s.name for s in self.record.sections]
        self.assertEqual(
            names,
            ["主诉", "现病史", "既往史", "过敏史", "查体", "辅助检查", "诊断", "处理"],
        )

    def test_vitals(self):
        bp = [v for v in self.record.vitals if v.name == "血压"][0]
        self.assertEqual((bp.value, bp.secondary, bp.unit), (158.0, 96.0, "mmHg"))
        self.assertIn("BP 158/96mmHg", self.text[bp.span[0] : bp.span[1]])

    def test_medications(self):
        names = [m.name for m in self.record.medications]
        self.assertEqual(names, ["阿司匹林", "阿托伐他汀"])
        self.assertEqual(self.record.medications[0].frequency, "qd")
        self.assertEqual(self.record.past_medications[0].name, "氨氯地平")

    def test_diagnoses(self):
        self.assertEqual(len(self.record.diagnoses), 2)
        self.assertTrue(self.record.diagnoses[0].uncertain)
        self.assertFalse(self.record.diagnoses[1].uncertain)


class TestRules(unittest.TestCase):
    def test_major_findings_on_sample(self):
        text = SAMPLE.read_text(encoding="utf-8")
        record = build_record(text, "sample")
        rules = {f.rule_id for f in run_qc(record)}
        self.assertTrue({"R003", "R004", "R006", "R007", "R010"} <= rules)

    def test_span_is_inside_source(self):
        text = SAMPLE.read_text(encoding="utf-8")
        report = build_report(text, "sample")
        for f in report["findings"]:
            if f["span"]:
                start, end = f["span"]
                self.assertLessEqual(0, start)
                self.assertLessEqual(end, len(text))

    def test_clean_record_has_no_major(self):
        record = build_record(CLEAN, "clean")
        findings = run_qc(record)
        majors = [f for f in findings if f.severity in ("blocker", "major")]
        self.assertEqual(majors, [], [f.title for f in findings])
        for f in findings:
            self.assertIn(f.rule_id, {"R004"})

    def test_missing_section_blocker(self):
        record = build_record("【主诉】腹痛3天。\n【处理】观察。", "bad")
        findings = run_qc(record)
        titles = [f.title for f in findings]
        self.assertTrue(any("诊断" in t for t in titles))
        self.assertTrue(any(f.severity == "blocker" for f in findings))


class TestReport(unittest.TestCase):
    def test_report_shape(self):
        report = build_report(SAMPLE.read_text(encoding="utf-8"), "sample")
        self.assertIn("soap", report)
        self.assertEqual(len(report["soap"]["S"]), 4)
        self.assertIn("disclaimer", report)
        self.assertIn("verdict", report["summary"])

    def test_markdown(self):
        md = render_markdown(build_report(SAMPLE.read_text(encoding="utf-8"), "sample"))
        self.assertIn("## 2. SOAP 结构化", md)
        self.assertIn("免责声明", md)
        self.assertIn("R010", md)


class TestNegationAndEdgeSamples(unittest.TestCase):
    def test_negated_chest_pain_is_not_flagged(self):
        text = (
            "【主诉】反复头晕2周。\n"
            "【现病史】头晕，无胸痛，无胸闷。\n"
            "【既往史】高血压10年。\n【过敏史】否认药物过敏。\n"
            "【查体】BP 150/92mmHg，P 80次/分。\n【诊断】高血压2级\n"
            "【处理】氨氯地平5mg 每日一次。\n"
        )
        record = build_record(text, "negation")
        rules = {f.rule_id for f in run_qc(record)}
        self.assertNotIn("R002", rules)

    def test_followup_sample_only_bp_major(self):
        p = Path(__file__).resolve().parents[1] / "samples" / "outpatient_zh_03_followup.txt"
        report = build_report(p.read_text(encoding="utf-8"), p.name)
        majors = [f["rule_id"] for f in report["findings"] if f["severity"] in ("blocker", "major")]
        self.assertEqual(majors, ["R003"])


if __name__ == "__main__":
    unittest.main()
