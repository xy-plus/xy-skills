**放哪**：工作中写出的每个文件，按「要活多久、归谁管」只有三处：
1. 仓库：长期留且适合版本管理的，提交进仓库。
2. 写明的长期位置：长期留但不适合进仓库的，放仓库文档或全局 CLAUDE.md 写明的位置；没写明时问用户，不自建长期位置。
3. runs：其余文件都放 runs，包括 harness 提示你放进其临时目录（job 临时目录、scratchpad）的文件。

**runs 怎么用**：一件事一个目录 `<runs>/<项目目录名>/<年月日-时分秒>-<主题>/`。runs 路径取环境变量 `RUNS_ROOT`；项目目录名取会话记录在 `~/.claude/projects/` 下的目录名，不换算；主题用简短英文短语。目录内组织由你定；以工具名命名的子目录（如 `xy-goal/`、`sub-agent-runner/`）归该工具，不要往里放东西。派子代理时把事目录交给它。

**什么时候删**：每件事的 runs 目录满 30 天未改且本用户无进程在用（会话、tmux、服务都算）时整项删除；空项目目录满 30 天也删。

确定作废且无会话或服务在用的 `-<项目>/<事>` 目录或其中产物，本会话可提前删，不用等 30 天，也不交给用户。逐项写出字面绝对路径，并在同一命令核对真实路径与目标完全相同。runs 内软链若指向仓库或缓存，且真实位置也在 runs，属 xy，不另核属主。还须确认无进程在用、无 `~/.config/systemd/user` 单元引用；timer 暂无活进程也可能仍要用。提前只删事目录或其中内容，不删 runs 本身、项目目录或整个 `xy-goal-inbox`。删除前查其他会话是否仍依赖它（如 `sub-agent-runner --dir` 续跑）；分类器查不到这类依赖。

把 `RUNS_ROOT` 写成字面绝对路径，不写 `"$RUNS_ROOT"`；分类器看不到变量值，会把目标视为无法核实：

```bash
p='<RUNS_ROOT 的字面值>/-<项目>/<事>/obsolete-build'
[ "$(realpath -e -- "$p")" = "$p" ] &&
{ lsof_output=$(if [ -d "$p" ]; then lsof -Q -t +w +D "$p"; else lsof -Q -t +w -- "$p"; fi 2>&1); lsof_status=$?; [ "$lsof_status" -eq 0 ] && ! printf '%s\n' "$lsof_output" | grep -qxE '[0-9]+' && ! printf '%s\n' "$lsof_output" | grep -qF -- "$p" || { printf '%s\n' "lsof failed, target in use or target scan incomplete ($lsof_status): $lsof_output" >&2; false; }; } &&
{ unit_output=$(grep -RlF -- "$p" ~/.config/systemd/user 2>&1); unit_status=$?; [ "$unit_status" -eq 1 ] && [ -z "$unit_output" ] || { printf '%s\n' "systemd reference or scan error ($unit_status): $unit_output" >&2; false; }; } &&
rm -rf -- "$p"
```

**不归这条规则管**：git 工作树仍放仓库 `.worktrees/`；harness 和各工具自管的位置由它们决定，不手动挪。
