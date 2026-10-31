#!/usr/bin/env python3
"""Initialize, approve, check, and render a repository's LikeC4 function map."""
import argparse
from datetime import date
from pathlib import Path
import sys

from repo_map_lib.approval import record_approval
from repo_map_lib.approval_command import approval_report
from repo_map_lib.checker import check_repository
from repo_map_lib.git_repository import ArgumentError, require_repository_root
from repo_map_lib.initializer import init_map
from repo_map_lib.likec4 import export_model
from repo_map_lib.publish_command import publish_repository
from repo_map_lib.render_command import render_repository
from repo_map_lib.structure import structure_digest


def parser():
    result = argparse.ArgumentParser(description="管理仓库的 LikeC4 功能图")
    commands = result.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="在仓根建立空白仓图草稿")
    initialize.add_argument("--repo", required=True, help="git 仓根的绝对路径")
    initialize.add_argument("--id", required=True, help="根节点 id")
    initialize.add_argument("--title", required=True, help="根节点标题")
    initialize.add_argument("--description", required=True, help="根节点的一句话描述")

    approval = commands.add_parser("approval", help="比较并打印功能结构变化与摘要")
    approval.add_argument("--repo", required=True, help="git 工作树根的绝对路径")

    record = commands.add_parser("record", help="核验用户原话并记录改图同意")
    record.add_argument("--repo", required=True, help="git 工作树根的绝对路径")
    record.add_argument("--approval", required=True, help="hook 原话文件的绝对路径")

    check = commands.add_parser("check", help="检查仓图与文件归属")
    check.add_argument("--repo", required=True, help="git 仓根或工作树根的绝对路径")

    render = commands.add_parser("render", help="生成草稿或正式静态网站")
    render.add_argument("--repo", required=True, help="git 仓根或工作树根的绝对路径")
    render.add_argument("--mode", choices=("草稿", "正式"), required=True)
    render.add_argument("--out", required=True, help="不存在或为空的输出目录")

    publish = commands.add_parser("publish", help="按产物模式校验并发布静态网站")
    publish.add_argument("--out", required=True, help="render 生成目录")
    publish.add_argument("--name", required=True, help="平台站点名")
    return result


def _print_problems(problems):
    for problem in problems:
        print(problem)


def main(argv):
    try:
        options = parser().parse_args(argv[1:])
    except SystemExit as error:
        return int(error.code)
    try:
        if options.command == "init":
            destination = init_map(options.repo, options.id, options.title, options.description)
            print(f"已创建仓图草稿：{destination}")
            return 0
        if options.command == "approval":
            changes, digest_line = approval_report(options.repo)
            print("变化清单：")
            for line in changes:
                print(f"- {line}")
            print(digest_line)
            return 0
        if options.command == "record":
            require_repository_root(options.repo)
            approval_path = Path(options.approval)
            if not approval_path.is_absolute():
                raise ArgumentError("--approval 必须是绝对路径")
            model = export_model(Path(options.repo) / "repo-map")
            digest = structure_digest(model)
            added = record_approval(Path(options.repo) / "repo-map", approval_path,
                                    digest, date.today().isoformat())
            print("已记录改图同意" if added else "该摘要已经记录，不重复写入")
            return 0
        if options.command == "check":
            problems, _ = check_repository(options.repo, True)
            _print_problems(problems)
            if problems:
                return 1
            print("仓图检查通过")
            return 0
        if options.command == "render":
            require_repository_root(options.repo)
            problems = render_repository(options.repo, options.mode, options.out)
            _print_problems(problems)
            if problems:
                return 1
            print(f"网站已生成：{Path(options.out).expanduser()}")
            return 0
        if options.command == "publish":
            return publish_repository(options.out, options.name)
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
