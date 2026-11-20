import json
from pathlib import Path
import subprocess
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_CARE = REPOSITORY_ROOT / "repository-care"
sys.path.insert(0, str(REPOSITORY_CARE / "xy-cleanup" / "changed_features_only"))
sys.path.insert(0, str(REPOSITORY_CARE / "tests"))
from map_fixture import make_cleanup_repo  # noqa: E402
import approvals  # noqa: E402


class SkipApprovalIntegrationTest(unittest.TestCase):
    def test_skipped_file_changes_do_not_enter_the_commit_based_plan(self):
        repository = make_cleanup_repo(self)
        ignored_path = repository / "alpha" / "ignored.py"
        ignored_path.write_text("secret = 1\n", encoding="utf-8")
        self.configure_git(repository)
        self.commit(repository, "initial files")
        skip_path = repository / "repo-map" / "skip.json"
        skip_path.write_text(json.dumps(["alpha/ignored.py"]), encoding="utf-8")
        skipped = self.commit(repository, "declare skipped file")
        approvals_path = repository / "repo-map" / "approved.json"
        approvals_path.write_text(
            json.dumps({"root": skipped, "alpha": skipped, "beta": skipped}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        self.commit(repository, "record approvals")
        ignored_path.write_text(
            "secret = 'changed while skipped'\n",
            encoding="utf-8",
        )
        self.commit(repository, "change skipped file")

        _data, _values, _records, planned, _approvals, _head = approvals.plan_data(
            repository,
            ["全部"],
            "变过的",
        )

        self.assertEqual(planned, [])

    @staticmethod
    def configure_git(repository):
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "user.email",
                "test@example.invalid",
            ],
            check=True,
        )

    @staticmethod
    def commit(repository, message):
        subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", message], check=True
        )
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
