---
name: xy-runs
description: 每个会话都遵守的文件去处、runs 布局与定期清理规则；由全局 CLAUDE.md 引入。
---

**放哪**：你在工作中写出的每个文件，按「要活多久、归谁管」只有三个去处。
1. 仓库：要长期留、又适合版本管理的，提交进仓库。
2. 写明的长期位置：要长期留、又不适合进仓库的，放仓库文档或全局 CLAUDE.md 里写明的长期位置；没有写明的，遇到时问用户，不自己新建长期位置。
3. runs：其余一切都放 runs，包括 harness 提示你放进它临时目录（job 临时目录、scratchpad）的文件。

**runs 怎么用**：一件事一个目录 `<runs>/<项目目录名>/<年月日-时分秒>-<主题>/`。runs 的路径是环境变量 `RUNS_ROOT` 的值；项目目录名是你的会话记录在 `~/.claude/projects/` 下所在目录的名字，直接取，不要自己换算；主题用简短英文短语；目录里怎么组织由你定，只是以工具名命名的子目录（如 `xy-goal/`、`sub-agent-runner/`）归那个工具，不要往里放东西。派子代理时把这件事的目录交给它。

**什么时候删**：runs 里每件事的目录，30 天没有任何改动、本用户也没有任何进程在用（任何会话、tmux、服务都算），就整项删除；项目目录空了 30 天也删。确定作废、没有会话或服务在用的 `-<项目>/<事>` 事目录或其中产物，不用等 30 天，会话自己提前删，不交给用户；逐个写字面绝对路径，在同一条命令里核对真实路径就是写出的路径（runs 里有指向仓库和缓存的软链，真实位置在 runs 内即属 xy，不另核属主）、无进程在用、无 `~/.config/systemd/user` 单元引用（timer 暂无活进程也可能还要用），提前只删事目录或其中的东西，不删 runs 本身、项目目录和整个 `xy-goal-inbox`，删前还要想想别的会话是否仍要用它（如 `sub-agent-runner --dir` 续跑，这类依赖分类器查不到）。

把 `RUNS_ROOT` 的值写成字面绝对路径，不要写 `"$RUNS_ROOT"`；分类器看不到环境变量的值，会把它当作核实不了的目标。`lsof -Q -t` 仅在退出 0 且 stdout 空时确认无占用，stderr 诊断丢弃；`grep -RlF` 跟随单元软链按字面路径查引用，仅退出 1 且 stdout/stderr 均空时继续，其他情况停下不删：

```bash
p='<RUNS_ROOT 的字面值>/-<项目>/<事>/obsolete-build'
[ "$(realpath -e -- "$p")" = "$p" ] &&
{ process_output=$(if [ -d "$p" ]; then lsof -Q -t +D "$p"; else lsof -Q -t -- "$p"; fi 2>/dev/null); process_status=$?; [ "$process_status" -eq 0 ] && [ -z "$process_output" ] || { printf '%s\n' "lsof failed or target in use ($process_status): $process_output" >&2; false; }; } &&
{ unit_output=$(grep -RlF -- "$p" ~/.config/systemd/user 2>&1); unit_status=$?; [ "$unit_status" -eq 1 ] && [ -z "$unit_output" ] || { printf '%s\n' "systemd reference or scan error ($unit_status): $unit_output" >&2; false; }; } &&
rm -rf -- "$p"
```

**不归这条规则管**：git 工作树照旧放仓库的 `.worktrees/`；harness 和各工具自己管的位置由它们自己定，不手动挪。
