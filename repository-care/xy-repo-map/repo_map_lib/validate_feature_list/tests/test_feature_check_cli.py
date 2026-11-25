"""Check the repository-map command's CLI path errors."""

from pathlib import Path
import subprocess
import tempfile

from test_feature_check import (  # noqa: E402
    FeatureCheckBase,
    map_command_arguments,
)


class FeatureCheckCliTest(FeatureCheckBase):
    def test_bad_arguments_exit_with_two(self):
        result = subprocess.run(
            map_command_arguments("check", self.repo / "missing"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)

    def _assert_repository_error_for_check_and_render(self, repository, message):
        with tempfile.TemporaryDirectory() as temporary:
            for command in ("check", "render"):
                arguments = map_command_arguments(command, repository)
                if command == "render":
                    arguments.extend(["--out", str(Path(temporary) / "rendered")])
                result = subprocess.run(
                    arguments,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                with self.subTest(command=command):
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(message, result.stderr)

    def test_relative_repository_path_exits_with_two(self):
        self._assert_repository_error_for_check_and_render(
            "relative/path",
            "--repo 必须是绝对路径",
        )

    def test_non_root_repository_path_exits_with_two(self):
        self._assert_repository_error_for_check_and_render(
            self.repo / "repo-map",
            "--repo 必须是仓根",
        )
