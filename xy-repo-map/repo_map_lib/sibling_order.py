"""Precompute visual sibling order before embedding data in the map page.

The former page algorithm minimizes relationship-arc crossings, then total
endpoint span. It exhaustively visits permutations for up to eight siblings;
larger groups use up to twelve adjacent-swap passes. Strict improvements keep
ties in the FQN-sorted starting order (or the first exhaustive optimum). A
repository placeholder uses its child repository root's children as its own.
"""


EXACT_ORDER_LIMIT = 8
HEURISTIC_ORDER_PASSES = 12


def _objective(order, edges):
    positions = {node: index for index, node in enumerate(order)}
    crossings = 0
    span = 0
    for index, (source, target) in enumerate(edges):
        first, second = positions[source], positions[target]
        span += abs(first - second)
        left, right = sorted((first, second))
        for other_source, other_target in edges[index + 1:]:
            if source in (other_source, other_target) or target in (other_source, other_target):
                continue
            other_left, other_right = sorted((positions[other_source], positions[other_target]))
            if left < other_left < right < other_right or \
                    other_left < left < other_right < right:
                crossings += 1
    return crossings, span


def _exhaustive_order(siblings, edges):
    prefix = []
    remaining = list(siblings)
    best = list(siblings)
    best_objective = _objective(best, edges)

    def visit():
        nonlocal best, best_objective
        if not remaining:
            candidate_objective = _objective(prefix, edges)
            if candidate_objective < best_objective:
                best = list(prefix)
                best_objective = candidate_objective
            return
        for index in range(len(remaining)):
            node = remaining.pop(index)
            prefix.append(node)
            visit()
            prefix.pop()
            remaining.insert(index, node)

    visit()
    return best


def _heuristic_order(siblings, edges):
    order = list(siblings)
    objective = _objective(order, edges)
    for _pass in range(HEURISTIC_ORDER_PASSES):
        improved = False
        for index in range(len(order) - 1):
            candidate = list(order)
            candidate[index], candidate[index + 1] = candidate[index + 1], candidate[index]
            candidate_objective = _objective(candidate, edges)
            if candidate_objective < objective:
                order = candidate
                objective = candidate_objective
                improved = True
        if not improved:
            break
    return order


def _ordered_siblings(siblings, relations, repo_alias):
    if len(siblings) < 2:
        return list(siblings)
    members = set(siblings)
    edges = []
    for relation in relations:
        source_id = relation["source"]
        target_id = relation["target"]
        source = repo_alias[source_id] if source_id in repo_alias else source_id
        target = repo_alias[target_id] if target_id in repo_alias else target_id
        if source != target and source in members and target in members:
            edges.append((source, target))
    if not edges:
        return list(siblings)
    if len(siblings) <= EXACT_ORDER_LIMIT:
        return _exhaustive_order(siblings, edges)
    return _heuristic_order(siblings, edges)


def visual_children_order(nodes, children, repo_roots, relations):
    """Return page-only child lists in the exact visual tree order."""
    repo_alias = {root: placeholder for placeholder, root in repo_roots.items()}
    result = {}
    for parent in nodes:
        child_root = repo_roots[parent] if parent in repo_roots else parent
        result[parent] = _ordered_siblings(children[child_root], relations, repo_alias)
    return result
