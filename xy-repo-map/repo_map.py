#!/usr/bin/env python3
"""Check, render, and publish the feature map for one Git repository."""
import argparse
import sys

from repo_map_lib.git_repository import ArgumentError
from repo_map_lib.map_data import build_map
from repo_map_lib.publish_command import PUBLISH_MODES, publish_repository
from repo_map_lib.render_command import render_repository, render_workspace_repository


def parser():
    result = argparse.ArgumentParser(description="管理仓库功能清单与功能图")
    commands = result.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="校验功能清单并推导文件归属")
    check.add_argument("--repo", required=True, help="git 仓根的绝对路径")

    render = commands.add_parser("render", help="生成离线功能树页面")
    render.add_argument("--repo", required=True, help="git 仓根的绝对路径")
    render.add_argument("--out", required=True, help="不存在或为空的输出目录")

    render_workspace = commands.add_parser(
        "render-workspace", help="生成主仓与登记子仓的组合功能图",
    )
    render_workspace.add_argument("--repo", required=True, help="主仓根的绝对路径")
    render_workspace.add_argument("--out", required=True, help="不存在或为空的输出目录")

    publish = commands.add_parser("publish", help="发布 render 生成的页面")
    publish.add_argument("--out", required=True, help="render 生成的目录")
    publish.add_argument(
        "--as", dest="publish_as", required=True, choices=PUBLISH_MODES,
        help="发布当前图或清扫前后快照",
    )
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
        if options.command == "publish":
            return publish_repository(options.out, options.publish_as)
        if options.command == "render":
            directory = render_repository(options.repo, options.out)
            print(f"页面已生成：{directory}")
            return 0
        if options.command == "render-workspace":
            directory = render_workspace_repository(options.repo, options.out)
            print(f"页面已生成：{directory}")
            return 0
        data = build_map(options.repo)
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
