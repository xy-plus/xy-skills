# xy-skills

一组 Claude Code skill，一个目录一个 skill。

| skill | 用途 |
|---|---|
| `xy-iron-law` | 三条铁律 |
| `xy-repo-rules` | 仓库规范 |
| `xy-workflow` | 标准工作流程 |
| `xy-review` | 对抗审查：主审加剃刀两个子代理、报告格式与轮次结构检查（`check.py`）、非正式改—测循环上限（`audit.py`）和怎么处理结果；xy-workflow 的所有审查都用它 |
| `xy-goal` | 长目标契约与定时自查 |
| `xy-runs` | 文件去处、runs 布局与定期清理规则 |
| `xy-handoff` | 会话大任务归属、拆分交接与后台会话收尾 |
| `xy-repo-map` | LikeC4 仓库功能图、文件归属、批准、校验和静态出图 |
| `sync-think` | 只有用户能定的事，做成带建议的选择题问用户 |

## 安装

```bash
git clone https://github.com/xy-plus/xy-skills ~/xy-skills
mkdir -p ~/.claude/skills
for d in ~/xy-skills/*/; do ln -sfn "${d%/}" ~/.claude/skills/$(basename "$d"); done
```

在全局 `~/.claude/settings.json` 的 `env` 里显式设置 runs 的绝对路径，保留已有配置，例如：

```json
{
  "env": {
    "RUNS_ROOT": "/data/user/xy/runs"
  }
}
```

重启会话后，先创建这个根目录，再按 [xy-goal/README.md](xy-goal/README.md) 的安装步骤，将 `UserPromptSubmit` 与 `PostToolUse`（matcher `AskUserQuestion`）两个 hook 合并到全局 settings.json。hook 和命令都用 `--root "$RUNS_ROOT"`，不另设根目录。配好后，用户原话和选择题回答会自动记录，开契约时领取之前的消息。

```bash
mkdir -p "${RUNS_ROOT:?请先在全局 settings.json 的 env 中设置 RUNS_ROOT}"
```

把下面这段按顺序写进全局 `~/.claude/CLAUDE.md`，让每个会话常驻加载文件去处、交接规则、铁律和仓库规范：

```markdown
@~/xy-skills/xy-runs/SKILL.md
@~/xy-skills/xy-handoff/SKILL.md

## 规则

- 铁律：skill `xy-iron-law`（https://github.com/xy-plus/xy-skills/blob/master/xy-iron-law/SKILL.md），任何情况下都要遵守。
- 仓库规范：skill `xy-repo-rules`（https://github.com/xy-plus/xy-skills/blob/master/xy-repo-rules/SKILL.md）。
- 工作流程：讨论问题并可能写代码时，按 skill `xy-workflow`（https://github.com/xy-plus/xy-skills/blob/master/xy-workflow/SKILL.md）走。

@~/xy-skills/xy-iron-law/SKILL.md
@~/xy-skills/xy-repo-rules/SKILL.md
```

`xy-workflow` 依赖 Claude Code 插件 `superpowers` 不低于 6.4.1（装法：`/plugin install superpowers@claude-plugins-official`），以及本仓库的 `xy-goal`、`xy-review`、`sync-think`；`xy-goal` 依赖 Claude Code 的 hook 与定时任务工具 CronCreate、CronList、CronDelete，运行前提见它的 README；`xy-review/check.py` 复用同仓 `xy-handoff` 的 JSON 校验函数。`xy-handoff` 只单向依赖 `xy-runs`（交接说明放哪），以及 Claude Code 自带的 `claude --bg`、`claude agents`、`claude respawn`、`claude rm`、SendMessage、PushNotification。

`xy-goal` 管会话内原话、事项与验收的记账；`xy-workflow` 管一个大任务内部怎么做。

`xy-repo-map` 需要 Node.js 和 `npx`，固定运行 LikeC4 `1.60.0` 做模型校验与导出；在全局 Claude Code 权限配置中加入 `Bash(npx -y likec4@1.60.0 *)`。离线页面布局使用仓库内原样保存的 elkjs `0.9.3`，许可证为 EPL-2.0，文件位于 `xy-repo-map/third_party/elkjs-0.9.3/`。升级 LikeC4 或 elkjs 版本前都要征得用户同意。

草稿图只在批图时给用户看，用 `python3 /home/xy/web-portal/portal.py publish <名字> <render 的输出目录>` 发布，名字必须带 `draft`，不当正式图。正式仓图通过完整校验后，用同一命令发布正式产物，并把命令打印的链接给用户。

按 [xy-runs/README.md](xy-runs/README.md) 安装清理定时器，并按其中说明配置 runs 的清理范围。

新加 skill：建目录、写 `SKILL.md`，再按上面的方式软链。只在有 SKILL.md 装不下的东西时，比如代码、前提，才给 skill 目录加 README。

## 测试

在仓库根目录依次运行：

```bash
python3 -B -m unittest discover -s xy-goal/tests
python3 -B -m unittest discover -s xy-handoff
python3 -B -m unittest discover -s xy-review
python3 -B -m unittest discover -s xy-runs/tests
python3 -B -m unittest discover -s xy-repo-map/tests -p 'test_*.py'
XY_REPO_MAP_CHROMIUM=/path/to/chrome python3 -B -m unittest discover -s xy-repo-map/tests -p 'browser_*_test.py'
python3 -B -m unittest discover -s xy-repo-map/tests -p 'likec4_integration_test.py'
```

浏览器测试使用本机 Python Playwright 和 Chromium；把 `XY_REPO_MAP_CHROMIUM` 设为 Chromium 可执行文件的绝对路径。路径缺失或浏览器不可用时测试会失败，不会跳过。最后一条是真跑 LikeC4 的集成测试；若 `npx` 或固定版本不可用，测试会失败，不会跳过。
