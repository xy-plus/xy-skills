"""Publication mode guards and platform-command forwarding."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map import main
from repo_map_lib import publish_command


class PublishCommandTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fake_portal = self.root / "portal_stub.py"
        self.fake_portal.write_text(
            "import json, sys\n"
            "print(json.dumps(sys.argv[1:], ensure_ascii=False))\n"
            "sys.stderr.write('平台诊断\\n')\n"
            "raise SystemExit(23)\n",
            encoding="utf-8")

    def site(self, mode, draft_marker):
        output = self.root / f"site-{len(list(self.root.glob('site-*')))}"
        output.mkdir()
        (output / "map-data.json").write_text(
            json.dumps({"mode": mode}, ensure_ascii=False), encoding="utf-8")
        if draft_marker:
            (output / "DRAFT").write_text("草稿·未经同意\n", encoding="utf-8")
        return output

    @staticmethod
    def invoke(output, name):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main(["repo_map.py", "publish", "--out", str(output), "--name", name])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_valid_modes_forward_platform_output_and_exit_code_unchanged(self):
        for mode, marker, name in (("草稿", True, "sample-draft"),
                                   ("正式", False, "sample")):
            with self.subTest(mode=mode):
                output = self.site(mode, marker)
                with patch.object(publish_command, "PORTAL_SCRIPT", self.fake_portal), \
                        patch("repo_map_lib.publish_command.subprocess.run",
                              wraps=subprocess.run) as platform_call:
                    result, stdout, stderr = self.invoke(output, name)
                self.assertEqual(result, 23)
                self.assertEqual(json.loads(stdout), ["publish", name, str(output.resolve())])
                self.assertEqual(stderr, "平台诊断\n")
                command = platform_call.call_args.args[0]
                self.assertEqual(command[0], "python3")
                self.assertEqual(command[1], str(self.fake_portal))

    def test_publish_requires_output_and_name(self):
        incomplete_arguments = (
            ["repo_map.py", "publish", "--out", str(self.root)],
            ["repo_map.py", "publish", "--name", "sample"],
        )
        with patch("repo_map_lib.publish_command.subprocess.run") as platform_call:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                for arguments in incomplete_arguments:
                    self.assertEqual(main(arguments), 2)
        platform_call.assert_not_called()

    def test_rejects_draft_formal_name_and_marker_mismatches_before_publishing(self):
        cases = (
            ("草稿", True, "sample"),
            ("正式", False, "sample-draft"),
            ("正式", True, "sample"),
            ("草稿", False, "sample-draft"),
            ("预览", False, "sample"),
        )
        for mode, marker, name in cases:
            with self.subTest(mode=mode, marker=marker, name=name):
                output = self.site(mode, marker)
                with patch("repo_map_lib.publish_command.subprocess.run") as platform_call:
                    result, _, stderr = self.invoke(output, name)
                self.assertNotEqual(result, 0)
                self.assertNotEqual(stderr, "")
                platform_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
