"""Test that publication accepts render outputs with both required files."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from map_fixture import COMMAND


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "rendered"
        self.output.mkdir()

    def run_publish(self):
        return subprocess.run(
            ["python3", "-B", str(COMMAND), "publish", "--out", str(self.output),
             "--name", "xy-skills"],
            capture_output=True, text=True, check=False,
        )

    def test_publish_requires_a_render_directory(self):
        self.output.rmdir()
        result = self.run_publish()
        self.assertEqual(result.returncode, 2)

    def test_publish_requires_index_and_map_data(self):
        (self.output / "index.html").write_text("page", encoding="utf-8")
        result = self.run_publish()
        self.assertEqual(result.returncode, 2)
        self.assertIn("map-data.json", result.stdout + result.stderr)
        (self.output / "map-data.json").write_text("{}", encoding="utf-8")
        (self.output / "index.html").unlink()
        result = self.run_publish()
        self.assertEqual(result.returncode, 2)
        self.assertIn("index.html", result.stdout + result.stderr)

    def test_publish_calls_the_platform_for_a_complete_directory(self):
        (self.output / "index.html").write_text("page", encoding="utf-8")
        (self.output / "map-data.json").write_text("{}", encoding="utf-8")
        script = r'''
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from repo_map_lib.publish_command import PLATFORM_PYTHON, PORTAL_SCRIPT, publish_repository

calls = []
def run(*arguments, **options):
    calls.append((arguments, options))
    return type("Result", (), {"stdout": "published\n", "stderr": "", "returncode": 7})()

patcher = patch("repo_map_lib.publish_command.subprocess.run", run)
patcher.start()
output = io.StringIO()
with redirect_stdout(output):
    code = publish_repository(sys.argv[1], sys.argv[2])
expected = [PLATFORM_PYTHON, str(PORTAL_SCRIPT), "publish", sys.argv[2], str(Path(sys.argv[1]).resolve())]
assert code == 7
assert output.getvalue() == "published\n", repr(output.getvalue())
assert calls == [((expected,), {"capture_output": True, "text": True, "check": False})], calls
print("platform arguments and output verified")
'''
        result = subprocess.run(
            ["python3", "-B", "-c", script, str(self.output), "xy-skills"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "platform arguments and output verified\n")


if __name__ == "__main__":
    unittest.main()
