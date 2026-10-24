# xy-skills

一组 Claude Code skill，一个目录一个 skill。

| skill | 用途 |
|---|---|
| `xy-iron-law` | 三条铁律 |
| `xy-repo-rules` | 仓库规范 |
| `xy-workflow` | 标准工作流程 |
| `xy-review` | 对抗审查：主审加剃刀两个子代理、报告格式和格式检查脚本、怎么处理结果；xy-workflow 的所有审查都用它 |
| `xy-goal` | 长目标契约与定时自查 |
| `xy-runs` | 文件去处、runs 布局与定期清理规则 |
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

在全局 `~/.claude/CLAUDE.md` 加一行，让每个会话加载 runs 规则：

```markdown
@~/xy-skills/xy-runs/SKILL.md
```

按 [xy-runs/README.md](xy-runs/README.md) 安装清理定时器，并按其中说明配置 runs 的清理范围。

然后在 `~/.claude/CLAUDE.md` 或仓库的 `CLAUDE.md` 里写引用，两行 `@` 让两把尺子启动即全文载入：

```markdown
## 规则

- 铁律：skill `xy-iron-law`（https://github.com/xy-plus/xy-skills/blob/master/xy-iron-law/SKILL.md），任何情况下都要遵守。
- 仓库规范：skill `xy-repo-rules`（https://github.com/xy-plus/xy-skills/blob/master/xy-repo-rules/SKILL.md）。
- 工作流程：讨论问题并可能写代码时，按 skill `xy-workflow`（https://github.com/xy-plus/xy-skills/blob/master/xy-workflow/SKILL.md）走。

@~/xy-skills/xy-iron-law/SKILL.md
@~/xy-skills/xy-repo-rules/SKILL.md
```

`xy-workflow` 依赖 Claude Code 插件 `superpowers` 不低于 6.4.1（装法：`/plugin install superpowers@claude-plugins-official`），以及本仓库的 `xy-goal`、`xy-review`、`sync-think`；`xy-goal` 依赖 Claude Code 的 hook 与定时任务工具 CronCreate、CronList、CronDelete，运行前提见它的 README。

新加 skill：建目录、写 `SKILL.md`，再按上面的方式软链。只在有 SKILL.md 装不下的东西时，比如代码、前提、测试命令，才给 skill 目录加 README。
