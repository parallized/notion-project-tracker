# Notion Project Tracker (NPT)

## Overview

NPT is a Claude Code skill (`/npt`) that integrates with Notion via MCP to manage project TODOs. It finds pending tasks in a Notion database, executes them in your codebase, and reports results back to Notion.

## Project Structure

```
.claude/skills/npt/SKILL.md   — Core skill definition (the /npt command)
.mcp.json                     — Notion MCP server configuration
templates/.npt.json            — Template config for target projects
install.sh                     — Install skill globally
AGENTS.md                      — Codex compatibility
```

## Development Conventions

- The core logic lives entirely in SKILL.md as prompt instructions — no runtime code.
- All Notion interactions go through the Notion MCP tools.
- The `NPT` page is the workspace validation marker and system info hub.
- All items within a registered TODO database are considered NPT-managed (database-level trust boundary).
- The `.npt.json` file in target projects maps them to Notion databases. Optional `auto_mode` enables skipping confirmation by default.
- Field names are localized (Chinese): 项目名称, 标签, 技术栈, 上次同步, 项目路径, 任务.
- TODO database schema: 任务 (title), 状态 (select: 待办/队列中/进行中/需要更多信息/已阻塞/已完成), 标签 (multi_select, auto-generated on completion), 想法引用 (relation to IDEA, optional), 上次同步 (last_edited_time). `已阻塞` tasks are not auto-retried until manually unblocked by the user.

## Key Concepts

- **Workspace validation**: Strict check that a workspace is either NPT-managed (has an `NPT` page) or empty before any operations.
- **Database-level trust**: All items in an NPT-registered TODO database are managed by NPT. No per-item marker needed.
- **Workspace root structure** (4 items only):
  - `NPT` — system info page + session logs
  - `项目` — container page; each project's TODO database is a direct child
  - `概要` — standalone database tracking project metadata: 标签 (theme), 技术栈 (tech stack), sync history (summaries are written as page content, not a property)
  - `IDEA` — standalone idea database for cross-project inspiration and capability insights
- **TODO databases as direct children of `项目`**: Each project has one TODO database directly under `项目` (no intermediate page).
- **Page content as description**: Task details are written in the page body, not a property.
- **Results via comments/toggles**: Completion results are reported via Notion comments or toggle blocks.
