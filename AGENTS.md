# Notion Project Tracker (NPT) — Agent Instructions

## Purpose

You are operating in a project that uses Notion Project Tracker (NPT). NPT manages TODO tasks via a Notion workspace. This file provides instructions for AI agents (including OpenAI Codex) to interact with the NPT system.

**Full workflow details**: See `.codex/skills/npt/SKILL.md` for the complete skill definition with all phases, error handling, and formatting specs.

## Prerequisites

- A Notion workspace accessible via Notion MCP.
- For Codex: run `codex mcp add notion --url https://mcp.notion.com/mcp && codex mcp login notion` to configure Notion access.
- The workspace must contain a page titled `NPT` (created by NPT during initialization).

## Invocation

When the user asks to "run npt", "sync tasks", "check todos", or similar — follow the full workflow in `.codex/skills/npt/SKILL.md`.

**Arguments** (first token after `npt`):
- `sync` (default): Full cycle — validate workspace, find TODOs, execute them, report results.
- `auto`: Toggle auto mode in `.npt.json` (`auto_mode: true/false`). Does NOT run sync — just toggles and exits. `auto on|off` sets explicitly.
- `init`: Initialize workspace and register current project only (no execution).
- `status`: Display current TODO status without executing.

## Interaction Rules

- Preferred language first:
  - All user-facing terminal output and Notion comments must use the user's preferred language.
  - Resolve language in this priority:
    1. Explicit language requested in current conversation
    2. `NPT` page `配置项` database key `language` (if present and readable)
    3. Language inferred from the user's latest message
- Global config precedence:
  - Read workspace-level config from `NPT` page `配置项` database (`Key -> Value`) before local execution.
  - Supported keys: `language`, `auto_mode`, `max_tags`, `session_log`, `result_method`.
  - Precedence: explicit user instruction > local `.npt.json` > `配置项` > built-in defaults.
  - `result_method` is hard-limited to `comment`; invalid values must be ignored and treated as `comment`.
- Single-project scope:
  - One `npt` run must operate on exactly one project directory.
  - Never run sync/status from broad container paths like `/`, `~`, `~/Desktop`; ask user to enter a concrete project directory first.

## Workflow Summary

### 1. Validate Workspace (NEVER SKIP)

Search the Notion workspace for a page titled `NPT` (query: `NPT Notion Project Tracker`).

- **Found**: The workspace is NPT-managed. Workspace root has 4 items: `NPT` (page, with `配置项`/`系统信息`/`会话日志` child DBs), `项目` (page), `概要` (database), `IDEA` (database).
- **Not found + empty workspace** (≤ 2 pages): Initialize by creating `NPT` page, `项目` page, `IDEA` database（想法, 标签, 状态, 优先级, 关联项目）, and `概要` database (schema: 项目名称, 标签, 技术栈, 上次同步, 项目路径; summaries as page content).
- **Not found + has content**: STOP. Do not modify a workspace that NPT did not create.

### 2. Resolve Project

Before reading `.npt.json`, validate current directory is a concrete project workspace. If launched from `/`, `~`, `~/Desktop` (or similarly broad directory), stop and ask user to choose a single project directory. If needed, guide user to run `npt init` in that target directory first.

Before consuming `.npt.json`, read global defaults from `NPT` page `配置项` database.

Check for `.npt.json` in the working directory:

```json
{
  "project_name": "my-project",
  "notion_database_id": "uuid-here",
  "auto_mode": false,
  "known_task_page_ids": ["page-id-1", "page-id-2"],
  "last_discovery_at": "2026-02-15T20:40:00Z"
}
```
`auto_mode` is optional. If missing, inherit from `配置项.auto_mode` (default `false`).
`known_task_page_ids` and `last_discovery_at` are optional discovery cache fields.

If absent, derive project name from directory basename. Look up in `概要` database. If not registered, create a TODO database **directly under `项目`** with schema:

| Property | Type             | Notes                                                    |
|----------|------------------|----------------------------------------------------------|
| 任务     | title            | Task name                                                |
| 状态     | select           | 待办, 队列中, 进行中, 需要更多信息, 已阻塞, 已完成       |
| 标签     | multi_select     | Auto-tags on completion (0-5, ≤ 15 total types)          |
| 想法引用 | relation         | Optional relation to IDEA database                        |
| 上次同步 | last_edited_time | Auto-updated on edit                                     |

Task descriptions = page content (body). Results must be reported via comments (no toggle fallback).

Register in `概要` and write `.npt.json` (initialize `known_task_page_ids` to `[]` and `last_discovery_at` to an empty string for first run).

### 3. Execute TODOs

Query for items where 状态 = `待办`, `队列中`, `进行中`, or `需要更多信息`. Collect `已阻塞` items for reporting only (never auto-change them).
Use Notion API exact query only (via skill-local helper script `scripts/notion_api.py query-active`) with `NOTION_API_KEY`.
Do not assume MCP OAuth login token is reusable in shell; treat it as internal to MCP unless the runtime explicitly exposes it.
If API query is unavailable or fails, STOP and report error (`run npt init first if not initialized, then set NOTION_API_KEY`). Do not fall back to MCP semantic search.
Query confidence is `high` only when exact API query succeeds.

**Batch queue**: Before execution, mark all selected tasks as `队列中` to distinguish from tasks added during the session.

For each task:
1. Fetch the page content (description). Read any images by downloading and analyzing them.
2. Set 状态 → `进行中`.
3. Execute the task in the codebase.
4. Set 状态 → `已完成`, assign 0-5 标签, and report results via comment only. Prefer `scripts/notion_api.py create-comment` first so the comment author is NPT integration; if REST comment fails, fallback to MCP comment API.
5. If info is missing → `需要更多信息` (append question template to page).
6. If technically blocked → `已阻塞` (report blocker reason).

If effective `auto_mode: true` (local `.npt.json` overrides global `配置项`), skip user confirmation. Otherwise, present the task list and ask before proceeding.

### 4. Report

1. Output a terminal summary (completed / needs info / blocked / remaining).
2. Update `概要` entry: set `上次同步`, replace page content with a ~100-char project slogan.
3. Append session entry to the project's TODO database page content.
4. Append session entry to the `NPT` page Session Log only when effective `session_log` is not `false`.

## Safety Rules

- NEVER modify Notion content outside of registered TODO databases, the `概要` database, the `IDEA` database, the `项目` page, and the `NPT` page.
- NEVER skip workspace validation.
- NEVER delete Notion pages or databases.
- NEVER delete a database/page via MCP/API when it contains (or may contain) `>= 3` child pages/databases; provide manual Notion UI deletion steps instead.
- NEVER mark incomplete work as done.
