# Notion Project Tracker (NPT) — Agent Instructions

## Purpose

You are operating in a project that uses Notion Project Tracker (NPT). NPT manages TODO tasks via a Notion workspace. This file provides instructions for AI agents (including OpenAI Codex) to interact with the NPT system.

## Prerequisites

- A Notion workspace accessible via API (Notion MCP or Notion API with integration token).
- The workspace must contain a page titled `NPT` (created by NPT during initialization).

## Workflow

### 1. Validate Workspace

Before any operation, search the Notion workspace for a page titled `NPT`.

- **Found**: The workspace is NPT-managed. Workspace root has 3 items: `NPT` (page), `项目` (page), `概要` (database).
- **Not found + empty workspace**: Initialize by creating:
  1. `NPT` page — system info and session logs
  2. `项目` page — container for project TODO databases (direct children)
  3. `概要` database (schema: 项目名称, 标签, 技术栈, 上次同步, 项目路径; summaries written as page content)
- **Not found + has content**: STOP. Do not modify a workspace that NPT did not create.

### 2. Resolve Project

Check for `.npt.json` in the working directory:

```json
{
  "project_name": "my-project",
  "notion_database_id": "uuid-here"
}
```

If absent, use the directory basename as the project name. Look up the project in the `概要` database. If not registered, create a TODO database **directly under `项目`** with schema:

| Property | Type   | Notes                    |
|----------|--------|--------------------------|
| 任务     | title  | Task name                |
| 状态     | select | 待办, 进行中, 需要更多信息, 已完成 |

Task descriptions are written as page content (the page body). Results are reported via Notion comments or toggle blocks appended to the page content.

Register the project in `概要` and write `.npt.json`.

### 3. Execute TODOs

Query the TODO database for items where 状态 = "待办", "进行中", or "需要更多信息". All items in a registered TODO database are considered NPT-managed. For each:

1. Fetch the page to read the task title and page content (description).
2. Set 状态 → "进行中".
3. Execute the task in the codebase (write code, fix bugs, etc.).
4. Set 状态 → "已完成" and report results via comment or toggle block on the page.
5. If info is missing, set 状态 → "需要更多信息" and append questions to the page content.

### 4. Report

Output a summary of completed, blocked, and remaining tasks. Update the project's page content (summary) in the `概要` database. Append session logs to the TODO database page and the `NPT` page.

## Safety Rules

- NEVER modify Notion content outside of registered TODO databases, the `概要` database, the `项目` page, and the `NPT` page.
- NEVER skip workspace validation.
- NEVER delete Notion pages or databases.
- NEVER mark incomplete work as done.
