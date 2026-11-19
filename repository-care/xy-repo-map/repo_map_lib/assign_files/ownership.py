from ..validate_feature_list.features import deepest_directory


def assign_files(features, order, paths):
    """Assign each tracked path to its deepest containing feature directory."""
    return {
        path: deepest_directory(path, features, order)
        for path in paths
    }
