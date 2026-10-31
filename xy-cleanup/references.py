"""Compare text mentions between owned files with relationships in the repo map."""
import argparse
import json
from pathlib import Path, PurePosixPath
import sys

from check_audit import repository_context
from repo_map_lib.ownership import link_records


def module_names(file_path):
    """Return import-style suffixes derived from a tracked Python or Rust file."""
    path = PurePosixPath(file_path)
    if path.suffix == ".py":
        segments = list(path.with_suffix("").parts)
        if segments and segments[-1] == "__init__":
            segments.pop()
        separator = "."
        rust_module = False
    elif path.suffix == ".rs":
        segments = [segment for segment in path.with_suffix("").parts if segment != "src"]
        if segments and segments[-1] in {"mod", "lib", "main"}:
            segments.pop()
        separator = "::"
        rust_module = True
    else:
        return set()

    names = {separator.join(segments[index:]) for index in range(len(segments))}
    if rust_module and segments:
        names.add("crate::" + separator.join(segments))
    return names


def node_names(model, tracked, owners):
    """Return link paths, exclusive directories, and owned module names by node."""
    names = {element_id: set() for element_id in model.get("elements", {})}
    for element_id, target, _, _ in link_records(model):
        if target is not None:
            names.setdefault(element_id, set()).add(target)

    module_names_by_file = {}
    files_by_single_module_name = {}
    for file_path in tracked:
        if file_path == "repo-map" or file_path.startswith("repo-map/"):
            continue
        candidates = module_names(file_path)
        if not candidates:
            continue
        module_names_by_file[file_path] = candidates
        for candidate in candidates:
            if "." not in candidate and "::" not in candidate:
                files_by_single_module_name.setdefault(candidate, set()).add(file_path)

    for file_path, candidates in module_names_by_file.items():
        owner = owners.get(file_path)
        if owner is None:
            continue
        for candidate in candidates:
            is_qualified = "." in candidate or "::" in candidate
            if is_qualified or len(files_by_single_module_name[candidate]) == 1:
                names.setdefault(owner.element, set()).add(candidate)

    directories_by_file = {}
    all_directories = set()
    for file_path in tracked:
        directories = [directory.as_posix() for directory in PurePosixPath(file_path).parents
                       if directory.as_posix() != "."]
        directories_by_file[file_path] = directories
        all_directories.update(directories)

    directory_name_counts = {}
    for directory in all_directories:
        name = PurePosixPath(directory).name
        directory_name_counts[name] = directory_name_counts.get(name, 0) + 1

    files_by_directory = {}
    for file_path in tracked:
        if file_path == "repo-map" or file_path.startswith("repo-map/"):
            continue
        owner = owners.get(file_path)
        if owner is None:
            continue
        owner_element = owner.element
        for directory in directories_by_file[file_path]:
            files_by_directory.setdefault(directory, set()).add(owner_element)

    for directory, directory_owners in files_by_directory.items():
        if len(directory_owners) != 1:
            continue
        owner = next(iter(directory_owners))
        names.setdefault(owner, set()).add(directory + "/")
        short_name = PurePosixPath(directory).name
        if directory_name_counts[short_name] == 1:
            names[owner].add(short_name)
    return {element_id: sorted(values) for element_id, values in names.items()}


def text_lines(path):
    """Return lines and an explicit skip reason for non-text tracked files."""
    content = path.read_bytes()
    if b"\0" in content:
        return [], "含 NUL 字节"
    try:
        return content.decode("utf-8").splitlines(), None
    except UnicodeError:
        return [], "非 UTF-8 文件"


def relationship_pairs(model):
    """Return normalized directed element pairs from exported relationships."""
    relations = []
    for relation in model.get("relations", {}).values():
        source = relation.get("source", {}).get("model")
        target = relation.get("target", {}).get("model")
        if isinstance(source, str) and isinstance(target, str):
            relations.append({
                "from": source,
                "to": target,
                "title": relation.get("title", ""),
            })
    return sorted(relations, key=lambda item: (item["from"], item["to"], item["title"]))


def build_reference_report(repository, model, tracked, owners):
    """Return text mentions and graph relationships that disagree, as JSON data."""
    root = Path(repository)
    names = node_names(model, tracked, owners)
    evidence = {}
    skipped_files = []
    for file_path in sorted(tracked):
        if file_path == "repo-map" or file_path.startswith("repo-map/"):
            continue
        owner = owners.get(file_path)
        if owner is None:
            continue
        source = owner.element
        lines, skip_reason = text_lines(root / file_path)
        if skip_reason is not None:
            skipped_files.append({"file": file_path, "reason": skip_reason})
            continue
        if not lines:
            continue
        for target, target_names in names.items():
            if target == source:
                continue
            for line_number, line in enumerate(lines, 1):
                matches = [name for name in target_names if name and name in line]
                if matches:
                    pair = (source, target)
                    evidence.setdefault(pair, []).append({
                        "file": file_path,
                        "line": line_number,
                        "matches": sorted(matches),
                    })

    mentions = [
        {"from": source, "to": target,
         "evidence": sorted(lines, key=lambda item: (item["file"], item["line"]))}
        for (source, target), lines in sorted(evidence.items())
    ]
    relationships = relationship_pairs(model)
    mention_pairs = set(evidence)
    relationship_pair_set = {(item["from"], item["to"]) for item in relationships}
    return {
        "repo": str(root),
        "mentions": mentions,
        "skipped_files": skipped_files,
        "references_without_relationship": [
            item for item in mentions if (item["from"], item["to"]) not in relationship_pair_set
        ],
        "relationships_without_reference": [
            item for item in relationships if (item["from"], item["to"]) not in mention_pairs
        ],
    }


def parser():
    """Build the command-line parser with explicit repository input."""
    command = argparse.ArgumentParser(description="对账仓图节点之间的文本引用")
    command.add_argument("--repo", required=True, help="仓根绝对路径")
    return command


def main(argv):
    """Print the JSON reconciliation or return a command-line status."""
    arguments = parser().parse_args(argv)
    try:
        root, model, tracked, owners = repository_context(arguments.repo)
        result = build_reference_report(root, model, tracked, owners)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
