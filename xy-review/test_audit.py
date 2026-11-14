import contextlib
import io
import os
import tempfile
import unittest

from audit import main


def write_file(directory, name, content):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
        f.write(content)


class AuditTest(unittest.TestCase):
    def run_audit(self, root):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["audit.py", root])
        return code, output.getvalue().splitlines()

    def test_informal_loop_uses_maximum_round_number(self):
        with tempfile.TemporaryDirectory() as root:
            first_loop = os.path.join(root, "impl")
            write_file(first_loop, "report-round1.md", "")
            write_file(first_loop, "report-round3.md", "")

            second_loop = os.path.join(root, "followup-keep-list", "impl")
            write_file(second_loop, "report-round7.md", "")

            short_loop = os.path.join(root, "short-loop")
            write_file(short_loop, "report-round2.md", "")

            completed_loop = os.path.join(root, "completed-loop")
            write_file(completed_loop, "report-round4.md", "")
            write_file(completed_loop, "diagnosis.json", '{"diagnosis":"已复核","actions":["重想"]}')

            invalid_loop = os.path.join(root, "invalid-diagnosis")
            write_file(invalid_loop, "report-round3.md", "")
            write_file(invalid_loop, "diagnosis.json", '{"diagnosis":"已复核","actions":["无效动作"]}')

            code, problems = self.run_audit(root)

        self.assertEqual(code, 1)
        self.assertEqual(len(problems), 3, problems)
        self.assertTrue(any("impl" in problem and "report-round3" in problem for problem in problems), problems)
        self.assertTrue(any("followup-keep-list/impl" in problem and "report-round7" in problem
                            for problem in problems), problems)

    def test_usage_errors(self):
        with tempfile.TemporaryDirectory() as root:
            cases = ([], [os.path.join(root, "missing")])
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        code = main(["audit.py", *arguments])
                    self.assertEqual(code, 2)
                    self.assertIn("用法：audit.py <目录路径>", output.getvalue())
