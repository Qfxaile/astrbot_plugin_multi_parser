---
name: astrbot-multi-parser-development-guide
description: Use when Codex 在 AstrBot 多平台内容解析插件仓库中开发、修复、重构或审查代码，或维护平台解析、登录、配置、测试、依赖、项目文档、Skill 文档、版本与中文提交。
---

# AstrBot 多平台内容解析开发指南

## 开始工作

1. 读取仓库根目录的 `AGENTS.md`，以其中记录的项目事实、边界和验证方式为准。
2. 使用 `git status` 识别用户已有改动，再用 `rg` 查找相似实现、调用方、测试和文档。
3. 从代码确认当前行为，不沿用记忆中的平台状态、目录结构、命令权限或版本信息。
4. 将任务拆成可验证的小修改，避免把无关清理混入差异。

## 选择修改位置

- 插件注册、命令权限和服务装配：`main.py`
- 解析结果和上下文契约：`core/contracts.py`
- 登录契约、HTTP 登录基类和二维码渲染：`core/platform_login.py`
- 安全 HTTP、媒体和结果渲染：`core/http.py`、`core/media.py`、`core/rendering.py`
- 解析器公共流程：`core/parser.py`
- 配置、登录、会话历史、消息投递和视频策略编排：`services/`
- 平台清单以及解析器、登录适配器对应关系：`platforms/registry.py`
- 平台入口和协议实现：`platforms/<platform>/parser.py`、支持登录平台的 `platforms/<platform>/login.py` 及同目录内容模块
- 配置声明：`_conf_schema.json`
- 行为验证：`tests/`

跨平台能力进入 `core/` 或 `services/`，平台特有细节留在平台目录。每个平台由 `parser.py` 保留顶层解析入口，文章、视频、图集、签名等内容逻辑按职责拆入同目录模块，并从平台包的 `__init__.py` 导出公开解析器或登录提供者。

## 处理常见任务

### 修改解析器

复用 `BaseParser`、统一契约、安全 HTTP、媒体和投递服务。保持内容顺序，区分鉴权失败、网络失败、内容不存在和部分媒体失败。新增平台时更新平台包导出、`platforms/registry.py`、`platforms/__init__.py`、`_conf_schema.json`、README、项目事实文档和测试，并按用户可见程度更新 CHANGELOG；`services/configuration.py` 与 `services/authentication.py` 从注册表装配，只有装配语义变化时才修改。

### 修改平台登录

复用 `core/platform_login.py` 的契约和 HTTP 基类，以及 `services/authentication.py` 的编排。保留管理员权限边界，以 `main.py` 和测试确认每条命令是否限制私聊。限制二维码与重定向域名，只持久化最小 Cookie；成功、状态和错误输出不得泄漏凭据。遇到风控或设备验证时终止，不尝试绕过。

### 修改配置或依赖

配置变化同步 `_conf_schema.json`、README 和测试，并检查配置服务是否需要调整。项目环境统一通过 `uv` 管理，不假设仓库、AstrBot 或虚拟环境位于固定目录。改变依赖时同步 `pyproject.toml`、`uv.lock`，运行依赖还需同步 `requirements.txt`。

### 修改版本或发布资料

以 `metadata.yaml` 为唯一版本源。只有用户明确要求发布或升版时才修改版本，并同步 README 徽章、CHANGELOG 和 Git 标签；普通功能、修复和重构不升版。

### 同步项目指导文档

平台清单、模块职责、公共 API、目录结构、配置、依赖、命令权限或验证流程变化时，必须在同次变更中检查并更新根目录 `AGENTS.md` 和本 Skill。`AGENTS.md` 维护稳定项目事实与组件索引，本 Skill 维护 AI 执行步骤，避免复制相同段落。普通功能可以修正 `metadata.yaml` 的描述、短描述和仓库地址，但不能借此修改版本号。

## 验证与交付

使用 `AGENTS.md` 中的 `uv` 命令。先运行相关测试，再运行全量 pytest、Ruff 检查、本次修改的 Python 文件格式检查和 `git diff --check`。涉及真实适配器、插件加载或外部登录时，单独说明尚需集成验证。

提交前逐项确认：

- 差异只包含本次目标，未覆盖用户改动。
- 新行为有回归测试，安全边界和敏感信息不泄漏已覆盖。
- README、CHANGELOG、`AGENTS.md`、项目 Skill、配置 Schema 和元数据描述与当前代码一致，没有固定盘符、父目录、解释器或虚拟环境路径。
- 提交和 PR 使用中文，提交信息符合 Conventional Commits。

## 确保 Skill 有效性

修改本 Skill 时，必须同时验证其结构正确性和实际指导效果：

1. 先查看本 Skill 上次修改以来的提交和文件变动，识别重命名、迁移、新增平台及装配关系变化，不沿用旧目录结构。
2. 使用 `rg --files` 和 `rg` 核对正文提到的本地路径、模块、类、函数和配置项；再对照 `platforms/registry.py`、`_conf_schema.json` 及边界测试确认平台清单和职责归属。
3. 使用当前 `skill-creator` 提供的 `scripts/quick_validate.py` 校验 Skill 目录，确认 YAML frontmatter、必填字段和命名规则有效。在 Windows 中文区域遇到默认编码错误时，以 UTF-8 模式运行校验器，例如为 Python 增加 `-X utf8`。
4. 检查 `agents/openai.yaml`，确保 `display_name`、`short_description` 和 `default_prompt` 与 `SKILL.md` 一致。
5. 对触发条件或工作流程的改动，至少使用一个代表性仓库任务验证 Skill 能被正确触发，并能指导执行者遵守 `AGENTS.md`、模块边界和验证流程。
6. 重新读取差异，确认 Skill、`AGENTS.md`、README、配置和元数据之间没有过时事实、互相矛盾的要求、无效路径或无法执行的命令。
7. 只有结构校验、事实核对和代表性任务验证均通过后，才能声明 Skill 有效；无法执行的验证必须在交付结果中明确说明。
