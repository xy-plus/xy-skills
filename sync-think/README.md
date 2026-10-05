# sync-think

一个 Claude Code skill：调用后，Claude 会用提问工具（AskUserQuestion）把拿不准、需要你拍板、可能有问题的地方做成**详细的选择题**交给你决定；
能在网上找到答案、参考方案或行业做法的问题，**先搜索，再基于搜索结果给建议**，不凭空猜。

## 安装

```bash
git clone https://github.com/xy-plus/sync-think-skill.git ~/.claude/skills/sync-think
```

## 怎么用

在 Claude Code 里输入 `/sync-think`。只在你主动调用时生效，不会自动触发。

规则原文见 [`SKILL.md`](SKILL.md)。
