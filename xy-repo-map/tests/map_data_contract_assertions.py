"""Assertions for the map-data document emitted by a real render."""


COUNT_FIELDS = ("code", "docs", "comments", "tests")
NODE_FIELDS = {"title", "kind", "parent", "own", "total"}


def assert_map_data_contract(test_case, document):
    test_case.assertEqual(set(document), {"mode", "nodes"})
    test_case.assertIn(document["mode"], {"草稿", "正式"})
    nodes = document["nodes"]
    test_case.assertTrue(nodes)
    children = {element_id: [] for element_id in nodes}
    roots = []
    for element_id, node in nodes.items():
        test_case.assertTrue(
            isinstance(element_id, str) and all(element_id.split(".")), element_id)
        test_case.assertEqual(set(node), NODE_FIELDS, element_id)
        test_case.assertIsInstance(node["title"], str, element_id)
        test_case.assertIn(node["kind"], {"node", "repo"}, element_id)
        parent = node["parent"]
        if parent is None:
            roots.append(element_id)
        else:
            test_case.assertIn(parent, nodes, element_id)
            children[parent].append(element_id)
        for field in ("own", "total"):
            counts = node[field]
            test_case.assertEqual(set(counts), set(COUNT_FIELDS), f"{element_id}.{field}")
            for count_name in COUNT_FIELDS:
                count = counts[count_name]
                test_case.assertIs(type(count), int, f"{element_id}.{field}.{count_name}")
                test_case.assertGreaterEqual(count, 0, f"{element_id}.{field}.{count_name}")

    test_case.assertEqual(len(roots), 1)
    for element_id, node in nodes.items():
        for count_name in COUNT_FIELDS:
            expected = node["own"][count_name] + sum(
                nodes[child_id]["total"][count_name] for child_id in children[element_id])
            test_case.assertEqual(node["total"][count_name], expected,
                                  f"{element_id}.total.{count_name}")
