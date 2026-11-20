import inspect
from pathlib import Path
import sys
import unittest


ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
sys.path.insert(0, str(ROOT / "repository-care" / "tests"))

import map_fixture  # noqa: E402


class MapFixtureTest(unittest.TestCase):
    def test_factories_and_map_wrapper_require_every_input(self):
        for function in (
            map_fixture.feature,
            map_fixture.root_feature,
            map_fixture.build_map,
        ):
            with self.subTest(function=function.__name__):
                self.assertTrue(
                    all(
                        parameter.default is inspect.Parameter.empty
                        for parameter in inspect.signature(function).parameters.values()
                    )
                )

    def test_root_factory_has_required_keyword_only_root_fields(self):
        parameters = inspect.signature(map_fixture.root_feature).parameters
        self.assertEqual(
            list(parameters),
            [
                "feature_id",
                "directory",
                "children",
                "source",
                "items",
                "subrepositories",
                "external_dependencies",
            ],
        )
        for name in (
            "children",
            "source",
            "items",
            "subrepositories",
            "external_dependencies",
        ):
            with self.subTest(parameter=name):
                self.assertIs(
                    parameters[name].kind,
                    inspect.Parameter.KEYWORD_ONLY,
                )
                self.assertIs(parameters[name].default, inspect.Parameter.empty)

    def test_root_fields_are_separate_and_missing_fields_differ_from_empty_lists(self):
        child = map_fixture.feature(
            "child",
            "child",
            source="原话",
            children=[],
            items=map_fixture.MISSING,
        )
        root = map_fixture.root_feature(
            "root",
            "",
            source="原话",
            children=[child],
            items=[],
            subrepositories=map_fixture.MISSING,
            external_dependencies=[],
        )

        self.assertNotIn("items", child)
        self.assertEqual(root["items"], [])
        self.assertNotIn("subrepositories", root)
        self.assertEqual(root["external_dependencies"], [])
