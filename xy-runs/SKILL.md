**文件去处**：每个工作文件按生命周期和归属只放一处：
1. 仓库：长期留且适合版本管理的，提交。
2. 已注明的长期位置：长期留但不适合入库的，放仓库文档或全局 CLAUDE.md 所列位置；未注明时问用户，不自建位置。
3. `runs`：其余文件，包括 harness 指向的临时目录（job 临时目录、scratchpad）。

**runs 使用**：每件事用 `<runs>/<项目目录名>/<年月日-时分秒>-<主题>/`；`RUNS_ROOT` 取环境变量，项目名取 `~/.claude/projects/` 下会话目录名且不换算，主题用简短英文。目录内结构自定；工具名子目录（如 `xy-goal/`、`sub-agent-runner/`）归工具，不放其他文件。派子代理时交事目录。

**什么时候删**：每件事的 runs 目录满 30 天未改且本用户无进程（会话、tmux、服务）在用时整项删；空项目目录也须满 30 天。

确定作废且无会话或服务在用的 `-<项目>/<事>` 或其中产物可提前删。逐项写字面绝对路径，并在同一命令核对真实路径完全相同。runs 内软链若指向仓库或缓存且真实位置也在 runs，属 xy，不另核属主。还须确认无进程在用、无 `~/.config/systemd/user` 单元引用（timer 即使暂无活进程也可能仍要用）。只删事目录或其中内容，不删 runs、项目目录或整个 `xy-goal-inbox`；先查其他会话是否依赖（如 `sub-agent-runner --dir` 续跑），分类器查不到这类依赖。

`RUNS_ROOT` 必须写字面绝对路径，不能写 `"$RUNS_ROOT"`；分类器看不到变量值，会认为目标无法核实：

```bash
p='<RUNS_ROOT 的字面值>/-<项目>/<事>/obsolete-build'
[ "$(realpath -e -- "$p")" = "$p" ] &&
{ lsof_output=$(if [ -d "$p" ]; then lsof -Q -t +w +D "$p"; else lsof -Q -t +w -- "$p"; fi 2>&1); lsof_status=$?; [ "$lsof_status" -eq 0 ] && ! printf '%s\n' "$lsof_output" | grep -qxE '[0-9]+' && ! printf '%s\n' "$lsof_output" | grep -qF -- "$p" || { printf '%s\n' "lsof failed, target in use or target scan incomplete ($lsof_status): $lsof_output" >&2; false; }; } &&
{ unit_output=$(grep -RlF -- "$p" ~/.config/systemd/user 2>&1); unit_status=$?; [ "$unit_status" -eq 1 ] && [ -z "$unit_output" ] || { printf '%s\n' "systemd reference or scan error ($unit_status): $unit_output" >&2; false; }; } &&
rm -rf -- "$p"
```

**例外**：git 工作树仍放在仓库 `.worktrees/`；harness 和工具自管的位置由它们决定，不手动移动。