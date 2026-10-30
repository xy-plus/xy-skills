"""Behavior tests for the approved part of a LikeC4 model."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.structure import structure_digest, structure_from_export


def exported_model(link_url, description):
    return {
        "elements": {
            "app": {"id": "app", "kind": "node", "title": "App",
                    "description": {"txt": "Whole app"}, "style": {}},
            "app.feature": {"id": "app.feature", "kind": "node", "title": "Feature",
                             "description": {"txt": description}, "style": {},
                             "links": [{"url": link_url, "title": "code"}]},
        },
        "relations": {
            "random-a": {"id": "random-a", "source": {"model": "app"},
                         "target": {"model": "app.feature"}, "title": "contains"},
        },
    }


class StructureTest(unittest.TestCase):
    def test_file_links_do_not_change_the_approved_structure(self):
        first = structure_digest(exported_model("../src/a.py", "A feature"))
        second = structure_digest(exported_model("../src/new-name.py", "A feature"))
        self.assertEqual(first, second)

    def test_title_description_parent_kind_and_relationships_change_the_digest(self):
        baseline = exported_model("../src/a.py", "A feature")
        expected = structure_digest(baseline)

        changes = []
        for element, field, value in (
            ("app.feature", "title", "Different title"),
            ("app.feature", "description", {"txt": "Different description"}),
            ("app.feature", "kind", "repo"),
        ):
            changed = exported_model("../src/a.py", "A feature")
            changed["elements"][element][field] = value
            changes.append(changed)
        changed_parent = exported_model("../src/a.py", "A feature")
        moved = changed_parent["elements"].pop("app.feature")
        moved["id"] = "other.feature"
        changed_parent["elements"]["other.feature"] = moved
        changes.append(changed_parent)
        changed_relation = exported_model("../src/a.py", "A feature")
        changed_relation["relations"]["random-a"]["title"] = "uses"
        changes.append(changed_relation)

        for changed in changes:
            with self.subTest(changed=changed):
                self.assertNotEqual(expected, structure_digest(changed))

    def test_digest_is_sha256_of_sorted_canonical_function_structure(self):
        model = exported_model("../src/a.py", "A feature")
        expected_structure = {
            "elements": [
                {"id": "app", "kind": "node", "title": "App",
                 "description": "Whole app", "parent": None},
                {"id": "app.feature", "kind": "node", "title": "Feature",
                 "description": "A feature", "parent": "app"},
            ],
            "relationships": [
                {"source": "app", "target": "app.feature", "title": "contains"},
            ],
        }
        canonical = json.dumps(expected_structure, ensure_ascii=False,
                               sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(structure_from_export(model), expected_structure)
        self.assertEqual(structure_digest(model), hashlib.sha256(canonical).hexdigest())


if __name__ == "__main__":
    unittest.main()
