from pathlib import Path
import sys


def setup_cleanup_imports(script_file, feature_directories):
    script_path = Path(script_file).resolve()
    cleanup_directory = script_path.parents[1]
    repository_care = script_path.parents[2]
    repository_root = script_path.parents[3]
    search_paths = [
        repository_root,
        repository_care / "xy-repo-map",
        cleanup_directory / "changed_features_only",
        *(cleanup_directory / name for name in feature_directories),
    ]
    for search_path in search_paths:
        sys.path.insert(0, str(search_path))
