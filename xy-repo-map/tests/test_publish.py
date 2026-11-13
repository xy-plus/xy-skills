from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from map_fixture import COMMAND


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.git_repository import ArgumentError
from repo_map_lib.publish_command import (
    PLATFORM_PYTHON,
    PORTAL_SCRIPT,
    publish_repository,
)


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "rendered"
        self.output.mkdir()

    def write_map_data(self, data):
        (self.output / "map-data.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8",
        )
        (self.output / "index.html").write_text("page", encoding="utf-8")

    def run_publish(self, *arguments):
        return subprocess.run(
            ["python3", "-B", str(COMMAND), "publish", "--out", str(self.output),
             *arguments],
            capture_output=True, text=True, check=False,
        )

    def publish_with_fake_platform(self, as_mode):
        calls = []

        def run(arguments, **options):
            calls.append((arguments, options))
            print("published")
            return subprocess.CompletedProcess(arguments, 0)

        output = io.StringIO()
        with patch("repo_map_lib.publish_command.subprocess.run", run):
            with redirect_stdout(output):
                code = publish_repository(str(self.output), as_mode)
        return code, output.getvalue(), calls

    def test_publish_requires_a_render_directory(self):
        self.output.rmdir()

        result = self.run_publish("--as", "当前")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--out 必须是 render 生成的目录", result.stdout + result.stderr)

    def test_publish_requires_index_and_map_data(self):
        (self.output / "index.html").write_text("page", encoding="utf-8")
        result = self.run_publish("--as", "当前")
        self.assertEqual(result.returncode, 2)
        self.assertIn("map-data.json", result.stdout + result.stderr)

        (self.output / "map-data.json").write_text("{}", encoding="utf-8")
        (self.output / "index.html").unlink()
        result = self.run_publish("--as", "当前")
        self.assertEqual(result.returncode, 2)
        self.assertIn("index.html", result.stdout + result.stderr)

    def test_publish_requires_an_explicit_as_option(self):
        result = self.run_publish()

        self.assertEqual(result.returncode, 2)
        self.assertIn("--as", result.stderr)

    def test_publish_rejects_an_as_option_outside_the_three_choices(self):
        result = self.run_publish("--as", "其他")

        self.assertEqual(result.returncode, 2)
        self.assertIn("choose from 当前, 清扫前, 清扫后", result.stderr)

    def test_publish_uses_the_repository_map_name_for_current(self):
        self.write_map_data({
            "root": "root",
            "features": {"root": {"directory_name": "xy-skills"}},
        })

        code, output, calls = self.publish_with_fake_platform("当前")

        expected = [
            PLATFORM_PYTHON, str(PORTAL_SCRIPT), "publish", "xy-skills-map",
            str(self.output.resolve()),
        ]
        self.assertEqual(code, 0)
        self.assertEqual(output, "published\n")
        self.assertEqual(
            calls,
            [(expected, {"check": False})],
        )

    def test_publish_uses_an_overview_name_when_feature_ids_have_paths(self):
        self.write_map_data({
            "root": "root",
            "features": {
                "root": {"directory_name": "quant-workspace"},
                "quant-a/root": {"directory_name": "quant-a"},
            },
        })

        code, _, calls = self.publish_with_fake_platform("当前")

        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0][3], "quant-workspace-overview-map")

    def test_publish_dates_before_and_after_snapshots_in_the_local_timezone(self):
        self.write_map_data({
            "root": "root",
            "features": {"root": {"directory_name": "quant-deep"}},
        })
        date = datetime.now().astimezone().strftime("%Y%m%d")

        for as_mode, ending in (("清扫前", "before"), ("清扫后", "after")):
            with self.subTest(as_mode=as_mode):
                code, _, calls = self.publish_with_fake_platform(as_mode)
                self.assertEqual(code, 0)
                self.assertEqual(calls[0][0][3], f"quant-deep-map-{date}-{ending}")

    def test_publish_rejects_unreadable_map_data_without_calling_the_platform(self):
        (self.output / "map-data.json").write_text("not json", encoding="utf-8")
        (self.output / "index.html").write_text("page", encoding="utf-8")
        with patch("repo_map_lib.publish_command.subprocess.run") as platform:
            with self.assertRaisesRegex(ArgumentError, "map-data.json"):
                publish_repository(str(self.output), "当前")
        platform.assert_not_called()

    def test_publish_requires_the_root_feature_in_map_data(self):
        for data in ({"root": "root", "features": {}}, []):
            with self.subTest(data=data):
                self.write_map_data(data)
                with patch("repo_map_lib.publish_command.subprocess.run") as platform:
                    with self.assertRaisesRegex(ArgumentError, "缺少根功能"):
                        publish_repository(str(self.output), "当前")
                    platform.assert_not_called()

    def test_publish_requires_directory_name_on_the_root_feature(self):
        self.write_map_data({"root": "root", "features": {"root": {}}})
        with patch("repo_map_lib.publish_command.subprocess.run") as platform:
            with self.assertRaisesRegex(ArgumentError, "directory_name"):
                publish_repository(str(self.output), "当前")
        platform.assert_not_called()
