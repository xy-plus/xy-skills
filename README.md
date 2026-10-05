# xy-skills

一组 Claude Code skill，一个目录一个 skill。

| skill | 用途 |
|---|---|
| `xy-iron-law` | 三条铁律 |
| `xy-repo-rules` | 仓库规范 |
| `xy-workflow` | 标准工作流程 |
| `xy-review` | 对抗审查：怎么派、怎么审、报告格式和格式检查脚本、怎么处理结果（`xy-workflow` 的所有审查都用它） |
| `xy-goal` | 长目标契约与定时自查 |
| `sync-think` | 拿不准的事做成选择题问用户 |

## 安装

```bash
git clone https://github.com/xy-plus/xy-skills ~/xy-skills
for s in xy-iron-law xy-repo-rules xy-workflow xy-review xy-goal sync-think; do
  ln -s ~/xy-skills/$s ~/.claude/skills/$s
done
```

然后在 `~/.claude/CLAUDE.md` 或仓库的 `CLAUDE.md` 里写引用：

```markdown
## 规则

- 铁律：skill `xy-iron-law`（https://github.com/xy-plus/xy-skills/blob/master/xy-iron-law/SKILL.md），任何情况下都要遵守。
- 仓库规范：skill `xy-repo-rules`（https://github.com/xy-plus/xy-skills/blob/master/xy-repo-rules/SKILL.md）。
- 工作流程：讨论问题并可能写代码时，按 skill `xy-workflow`（https://github.com/xy-plus/xy-skills/blob/master/xy-workflow/SKILL.md）走。
```

`xy-workflow` 依赖 Claude Code 插件 `superpowers`（claude-plugins-official 市场），以及本仓库的 `xy-goal`、`xy-review`、`sync-think`。

新加 skill：建目录、写 `SKILL.md`，再按上面的方式软链。
