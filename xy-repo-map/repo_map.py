#!/usr/bin/env python3
"""Check, render, and publish the feature map for one Git repository."""
import argparse
import sys

from repo_map_lib.git_repository import ArgumentError, require_repository_root
from repo_map_lib.map_data import build_map


def parser():
    result = argparse.ArgumentParser(description="管理仓库功能清单与功能图")
    commands = result.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="校验功能清单并推导文件归属")
    check.add_argument("--repo", required=True, help="git 仓根的绝对路径")
    return result


def _print_markers(data):
    for marker, count in data["markers"].items():
        locations = [feature_id for feature_id, feature in data["features"].items()
                     if marker in feature["markers"]]
        print(f"{marker}：{count}" + (f"（{', '.join(locations)}）" if locations else ""))


def main(argv):
    try:
        options = parser().parse_args(argv[1:])
    except SystemExit as error:
        return int(error.code)
    try:
        repository = require_repository_root(options.repo)
        data = build_map(repository)
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(str(error))
        return 1

    print("功能清单检查通过")
    _print_markers(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
