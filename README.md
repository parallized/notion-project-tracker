# Notion Project Tracker (NPT)

NPT 是一个技能（Claude Code: `/npt`，Codex: `npt`），通过 Notion MCP 管理项目 TODO。它会自动从 Notion 数据库中获取待办任务，在代码库中执行，并将结果写回 Notion。

## 安装

```bash
# 克隆仓库
git clone <repo-url>
cd notion-project-tracker

# 全局安装技能（默认同时安装到 Claude + Codex）
./install.sh

# 只安装 Claude 或 Codex
./install.sh --claude
./install.sh --codex
```

安装后：
- Claude Code 会话中使用 `/npt`
- Codex 会话中通过提示词调用 `npt`（例如 `npt status` / `npt sync`）

## 使用方法

在目标项目目录中运行：

```
/npt              # 完整同步：验证 → 发现任务 → 确认 → 执行 → 回报
/npt auto         # 自动模式：跳过确认，直接执行所有待办任务
/npt init         # 仅初始化工作区和注册项目
/npt status       # 仅查看当前任务状态
```

首次在新项目中使用时，NPT 会自动在 Notion 中创建对应的 TODO 数据库并生成 `.npt.json` 配置文件。

### Codex 使用

1) 确保 Notion MCP 已登录：

```bash
codex mcp add notion --url https://mcp.notion.com/mcp
codex mcp login notion
```

2) 在目标项目目录启动 Codex，会话里输入（作为提示词）：

```
npt status
npt sync
npt auto
npt init
```

## 工作原理

1. **工作区验证** — 检查 Notion 工作区是否由 NPT 管理（通过 `NPT` 页面标识）
2. **项目解析** — 通过 `.npt.json` 或目录名匹配对应的 Notion TODO 数据库
3. **任务执行** — 逐个执行待办任务（写代码、修 bug、加功能等）。支持图片描述：任务中的图片会通过 AI 模型进行视觉分析
4. **结果回报** — 将执行结果以评论或折叠块写回 Notion 页面

## Notion 工作区结构

NPT 管理的工作区根目录包含 3 个项：

| 名称   | 类型     | 用途                              |
|--------|----------|-----------------------------------|
| `NPT`  | 页面     | 系统信息 + 会话日志               |
| `项目` | 页面     | 容器页，每个项目的 TODO 数据库是其直接子项 |
| `概要` | 数据库   | 项目元数据（标签、技术栈、同步时间、摘要） |

### TODO 数据库 Schema

| 字段     | 类型              | 说明                                         |
|----------|-------------------|----------------------------------------------|
| 任务     | title             | 任务名称                                     |
| 状态     | select            | 待办 / 队列中 / 进行中 / 需要更多信息 / 已阻塞 / 已完成 |
| 标签     | multi_select      | 完成时自动生成的分类标签（0-5 个）           |
| 上次同步 | last_edited_time  | 自动记录页面最近编辑时间                     |

任务描述写在页面内容中，执行结果通过评论或折叠块回报。

## 项目结构

```
.claude/skills/npt/SKILL.md   — 核心技能定义（/npt 命令的全部逻辑）
.codex/skills/npt/SKILL.md    — Codex 技能定义（npt）
.mcp.json                     — Notion MCP 服务器配置
templates/.npt.json            — 目标项目的配置模板
install.sh                     — 全局安装脚本
AGENTS.md                      — Codex 兼容指令
CLAUDE.md                      — Claude Code 项目指令
```

## 多设备支持

NPT 按项目名称（非路径）匹配，`.npt.json` 是设备本地文件。不同设备可以在不同路径使用同一个项目，`概要` 中的 `目录路径` 记录最近一次同步的设备路径。

## 兼容性

- **Claude Code** — 通过 `/npt` 技能直接使用
- **Codex** — 通过 `npt` 技能直接使用（也保留 `AGENTS.md` 作为指令兼容/安全边界说明）
