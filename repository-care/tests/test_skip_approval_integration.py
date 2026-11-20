import json
from pathlib import Path
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_CARE = REPOSITORY_ROOT / "repository-care"
sys.path.insert(0, str(REPOSITORY_CARE / "xy-cleanup" / "changed_features_only"))
sys.path.insert(0, str(REPOSITORY_CARE / "tests"))
from map_fixture import build_map, feature, make_repo, root_feature as make_root_feature  # noqa: E402
import approvals  # noqa: E402


class SkipApprovalIntegrationTest(unittest.TestCase):
    def test_skipped_file_content_does_not_change_approval_fingerprint(self):
        repository = make_repo(
            self,
            {
                "README.md": "Demo.\n",
                "alpha/main.py": "value = 1\n",
                "alpha/ignored.py": "secret = 1\n",
            },
            make_root_feature(
                "root",
                "",
                subrepositories=[],
                external_dependencies=[],
                children=[
                    feature("alpha", "alpha", children=[], source="原话", items=[]),
                ],
                source="原话",
                items=[],
            ),
        )
        data = build_map(repository)
        values, _order = approvals.feature_values(repository)

        including_file = approvals.feature_fingerprints(repository, data, values)[
            "alpha"
        ]
        skip_path = repository / "repo-map" / "skip.json"
        skip_path.write_text(json.dumps(["alpha/ignored.py"]), encoding="utf-8")
        excluding_file = approvals.feature_fingerprints(repository, data, values)[
            "alpha"
        ]
        (repository / "alpha" / "ignored.py").write_text(
            "secret = 'changed while skipped'\n",
            encoding="utf-8",
        )
        after_skipped_change = approvals.feature_fingerprints(repository, data, values)[
            "alpha"
        ]

        self.assertNotEqual(including_file, excluding_file)
        self.assertEqual(excluding_file, after_skipped_change)
