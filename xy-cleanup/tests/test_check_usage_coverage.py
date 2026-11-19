import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-repo-map" / "tests"))

from map_fixture import make_cleanup_repo  # noqa: E402


COMMAND = ROOT / "xy-cleanup" / "check_usage_coverage.py"


class CheckUsageCoverageTest(unittest.TestCase):
    def setUp(self):
        self.repository = make_cleanup_repo(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.coverage_path = self.directory / "usage-coverage.json"
        self.write_readme("Demo.\n\n## 实际使用\n\npython3 app.py\n")

    def write_readme(self, content):
        path = self.repository / "README.md"
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "README.md"], check=True)

    def valid_coverage(self):
        return {
            "sources": [{
                "source": "README.md:5-5",
                "scenarios": [{"command": "python3 app.py", "ran": True, "why_not": ""}],
                "why_none": "",
                "uses": ["config/runtime.yml", "deploy/app.service", "scripts/run-app.sh"],
            }],
            "files": {
                "alpha/main.py": {"missed": []},
                "beta/main.py": {"missed": []},
            },
            "unmeasured": [],
        }

    def run_checker(self, coverage=None, raw=None, arguments=()):
        if raw is not None:
            self.coverage_path.write_text(raw, encoding="utf-8")
        else:
            self.coverage_path.write_text(
                json.dumps(self.valid_coverage() if coverage is None else coverage,
                           ensure_ascii=False),
                encoding="utf-8",
            )
        return subprocess.run([
            "python3", "-B", str(COMMAND), "--repo", str(self.repository),
            "--scope", "全部", "--review", "全部", "--usage-coverage",
            str(self.coverage_path), *arguments,
        ], capture_output=True, text=True, check=False)

    def test_accepts_complete_usage_coverage(self):
        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sources_match_table_rows_list_items_and_paragraphs(self):
        cases = (
            ("## 实际使用\n\n|来源|说明|\n|---|---|\n|本地|运行|\n|发布|部署|\n",
             ["README.md:5-5", "README.md:6-6"]),
            ("## 实际使用\n\n- 本地运行\n  继续说明\n- 发布部署\n",
             ["README.md:3-4", "README.md:5-5"]),
            ("## 实际使用\n\n本地运行\n继续说明\n\n发布部署\n",
             ["README.md:3-4", "README.md:6-6"]),
        )
        for readme, expected in cases:
            with self.subTest(expected=expected):
                self.write_readme(readme)
                coverage = self.valid_coverage()
                coverage["sources"] = [
                    {
                        "source": source,
                        "scenarios": [],
                        "why_none": "无可复现命令。",
                        "uses": ["config/runtime.yml"],
                    }
                    for source in expected
                ]
                coverage["files"] = {}
                coverage["unmeasured"] = [
                    {"path": path, "why": "场景没有执行，未量测。"}
                    for path in ("alpha/main.py", "beta/main.py")
                ]

                result = self.run_checker(coverage)

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_source_uses_must_be_present_nonempty_and_exact(self):
        cases = (
            (lambda source: source.pop("uses"), "缺少字段 uses"),
            (lambda source: source.update(uses=[]), "uses 必须是非空字符串数组"),
            (lambda source: source.update(uses=[""]), "uses[0] 必须是非空字符串"),
            (lambda source: source.update(extra=True), "有多余键 extra"),
        )
        for mutate, message in cases:
            with self.subTest(message=message):
                coverage = self.valid_coverage()
                mutate(coverage["sources"][0])

                result = self.run_checker(coverage)

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stdout + result.stderr)

    def test_requires_a_readme_actual_usage_section(self):
        self.write_readme("Demo.\n\nNo usage notes.\n")
        coverage = self.valid_coverage()
        coverage["sources"] = []

        result = self.run_checker(coverage)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("README.md 缺少「实际使用」一节", result.stdout)

    def test_scenarios_and_why_none_must_agree(self):
        cases = (
            ([], "", "scenarios 为空时"),
            ([{"command": "python3 app.py", "ran": False,
              "why_not": "副本缺少配置。"}], "无场景", "scenarios 非空时"),
        )
        for scenarios, why_none, message in cases:
            with self.subTest(message=message):
                coverage = self.valid_coverage()
                coverage["sources"][0]["scenarios"] = scenarios
                coverage["sources"][0]["why_none"] = why_none
                result = self.run_checker(coverage)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stdout)

    def test_files_must_be_empty_when_no_scenario_ran(self):
        coverage = self.valid_coverage()
        coverage["sources"][0]["scenarios"][0].update(
            ran=False, why_not="副本无法访问所需服务。",
        )

        result = self.run_checker(coverage)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("files 必须是空对象", result.stdout)

    def test_missed_ranges_must_be_sorted_merged_and_inside_the_file(self):
        source = self.repository / "alpha" / "main.py"
        source.write_text("one = 1\ntwo = 2\nthree = 3\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "alpha/main.py"], check=True)
        cases = ((["1-1", "2-2"], "相邻"), (["4-4"], "超出"), (["3-3", "1-1"], "按行序"))
        for missed, message in cases:
            with self.subTest(missed=missed):
                coverage = self.valid_coverage()
                coverage["files"]["alpha/main.py"]["missed"] = missed
                result = self.run_checker(coverage)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stdout)

    def test_each_direct_code_file_must_appear_exactly_once(self):
        coverage = self.valid_coverage()
        del coverage["files"]["alpha/main.py"]
        result = self.run_checker(coverage)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("直属代码文件必须恰好出现在 files 或 unmeasured", result.stdout)

        coverage["unmeasured"] = [{
            "path": "alpha/main.py", "why": "实盘路径不能在副本跑。",
        }]
        coverage["files"]["alpha/main.py"] = {"missed": []}
        result = self.run_checker(coverage)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("恰好出现在 files 或 unmeasured", result.stdout)

    def test_top_level_shape_and_coverage_paths_are_checked(self):
        coverage = self.valid_coverage()
        coverage["extra"] = True
        result = self.run_checker(coverage)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("有多余键 extra", result.stdout)

        coverage = self.valid_coverage()
        coverage["files"]["outside.py"] = {"missed": []}
        result = self.run_checker(coverage)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("不属于本次审查范围内的直属代码文件", result.stdout)

    def test_missing_arguments_and_missing_file_exit_two(self):
        missing_arguments = subprocess.run(
            ["python3", "-B", str(COMMAND)], capture_output=True, text=True,
            check=False,
        )
        self.assertEqual(missing_arguments.returncode, 2)

        self.coverage_path.unlink(missing_ok=True)
        result = subprocess.run([
            "python3", "-B", str(COMMAND), "--repo", str(self.repository),
            "--scope", "全部", "--review", "全部", "--usage-coverage",
            str(self.coverage_path),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_bad_json_and_duplicate_keys_exit_two(self):
        for raw in ('{"sources":', '{"sources": [], "sources": []}'):
            with self.subTest(raw=raw):
                result = self.run_checker(raw=raw)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
