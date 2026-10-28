import contextlib
import io
import os
import tempfile
import unittest

from audit import main


def write_report(directory, name, conclusion="通过"):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"## 结论\n{conclusion}\n")


class AuditTest(unittest.TestCase):
    def run_audit(self, root):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["audit.py", root])
        return code, output.getvalue().splitlines()

    def test_group_and_round_numbers_must_be_continuous(self):
        with tempfile.TemporaryDirectory() as root:
            object_path = os.path.join(root, "spec", "object-a")
            write_report(os.path.join(object_path, "group1", "round1"), "review-main.md")
            write_report(os.path.join(object_path, "group3", "round1"), "review-razor.md")
            write_report(os.path.join(object_path, "group3", "round3"), "review-main.md")

            code, problems = self.run_audit(root)

        self.assertEqual(code, 1)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("缺少 group2" in problem for problem in problems), problems)
        self.assertTrue(any("group3" in problem and "缺少 round2" in problem for problem in problems), problems)

    def test_round4_or_later_is_a_problem(self):
        with tempfile.TemporaryDirectory() as root:
            round_path = os.path.join(root, "group1", "round4")
            write_report(round_path, "review-main.md")

            code, problems = self.run_audit(root)

        self.assertEqual(code, 1)
        self.assertTrue(any("round4" in problem and "最多 3 轮" in problem for problem in problems), problems)

    def test_diagnosis_is_required_only_for_last_round_failure_at_limit(self):
        with tempfile.TemporaryDirectory() as root:
            object_path = os.path.join(root, "grouped-review")
            for number in (1, 2, 3):
                round_path = os.path.join(object_path, "group1", f"round{number}")
                write_report(round_path, "review-main.md", "不通过" if number == 1 else "通过")

                round_path = os.path.join(object_path, "group2", f"round{number}")
                write_report(round_path, "review-razor.md", "不通过" if number == 2 else "通过")

                round_path = os.path.join(object_path, "group3", f"round{number}")
                write_report(round_path, "review-main.md")
            write_report(os.path.join(object_path, "group3", "round3"), "review-razor.md")
            write_report(os.path.join(object_path, "group3", "round3"), "decision.md", "不通过")

            for number in (1, 2, 3):
                round_path = os.path.join(object_path, "group4", f"round{number}")
                write_report(round_path, "review-main.md", "不通过" if number == 3 else "通过")
            with open(os.path.join(object_path, "group4", "diagnosis.md"), "w", encoding="utf-8") as f:
                f.write("判因记录\n")

            for number in (1, 2, 3, 4):
                round_path = os.path.join(object_path, "group5", f"round{number}")
                write_report(round_path, "review-razor.md", "不通过" if number == 4 else "通过")

            code, problems = self.run_audit(root)

        self.assertEqual(code, 1)
        self.assertEqual(len(problems), 3, problems)
        self.assertTrue(any("group3" in problem and "diagnosis.md" in problem for problem in problems), problems)
        self.assertTrue(any("group5" in problem and "round4" in problem and "最多 3 轮" in problem
                            for problem in problems), problems)
        self.assertTrue(any("group5" in problem and "diagnosis.md" in problem for problem in problems), problems)

    def test_informal_loop_uses_maximum_round_number(self):
        with tempfile.TemporaryDirectory() as root:
            first_loop = os.path.join(root, "impl")
            write_report(first_loop, "report-round1.md")
            write_report(first_loop, "report-round3.md")

            second_loop = os.path.join(root, "followup-keep-list", "impl")
            write_report(second_loop, "report-round7.md")

            short_loop = os.path.join(root, "short-loop")
            write_report(short_loop, "report-round2.md")

            completed_loop = os.path.join(root, "completed-loop")
            write_report(completed_loop, "report-round4.md")
            with open(os.path.join(completed_loop, "diagnosis.md"), "w", encoding="utf-8") as f:
                f.write("判因记录\n")

            code, problems = self.run_audit(root)

        self.assertEqual(code, 1)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("impl" in problem and "report-round3" in problem for problem in problems), problems)
        self.assertTrue(any("followup-keep-list/impl" in problem and "report-round7" in problem
                            for problem in problems), problems)

    def test_clean_directory_and_usage_errors(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.run_audit(root), (0, []))

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                missing_path_code = main(["audit.py"])
            self.assertEqual(missing_path_code, 2)
            self.assertIn("用法：audit.py <目录路径>", output.getvalue())

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                invalid_path_code = main(["audit.py", os.path.join(root, "missing")])
            self.assertEqual(invalid_path_code, 2)
            self.assertIn("用法：audit.py <目录路径>", output.getvalue())


if __name__ == "__main__":
    unittest.main()
