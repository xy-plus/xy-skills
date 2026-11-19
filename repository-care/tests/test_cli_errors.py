from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest

REPOSITORY_CARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_CARE))
from cli_errors import report_cli_argument_error, report_cli_input_error  # noqa: E402


class CliErrorsTest(unittest.TestCase):
    def test_argument_error_keeps_stderr_and_usage_exit_status(self):
        output = StringIO()
        errors = StringIO()

        with redirect_stdout(output), redirect_stderr(errors):
            status = report_cli_argument_error(ValueError("参数错误"))

        self.assertEqual(status, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "参数错误\n")

    def test_input_error_keeps_stdout_and_input_exit_status(self):
        output = StringIO()
        errors = StringIO()

        with redirect_stdout(output), redirect_stderr(errors):
            status = report_cli_input_error(OSError("文件错误"))

        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), "文件错误\n")
        self.assertEqual(errors.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
