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
- 安全 HTTP、平台代理、媒体和结果渲染：`core/http.py`、`core/media.py`、`core/rendering.py`
- 解析器公共流程：`core/parser.py`
- 解析结果后处理视图：`core/contracts.py` 中 `ParseResult.content_lines`、`ParseResult.image_references`、`ParseResult.media_metadata`
- 解析结果领域视图：`core/contracts.py` 中的 `ContentDocument`、`MediaBundle`、`ParseDiagnostics`
- 解析结果消息组件渲染：`core/rendering.py` 的 `ParseResultRenderer`
- 平台统一 HTTP 客户端：`core/parser.py` 的 `BaseParser.http_client()`
- 平台扩展接口与能力元数据：`core/platform.py`
- 配置类型读取：`core/settings.py` 的 `PluginSettings`
- 登录文案格式化：`services/login_messages.py` 的 `LoginMessageFormatter`
- 活动登录会话管理：`services/login_sessions.py` 的 `LoginSessionRegistry`
- 视频直链摘要与路由：`services/forward_link_delivery.py` 的 `ForwardLinkDeliveryService`
- 合并转发构建与发送：`services/forward_delivery.py` 的 `ForwardDeliveryService`
- OneBot 机器人身份缓存：`services/onebot_identity.py` 的 `OneBotIdentityResolver`
- 配置、登录、会话历史、消息投递、视频策略、OneBot 适配和 AI 总结编排：`services/`
- 自动解析事件编排：`services/parsing.py` 的 `ParseCoordinator`
- 插件级服务装配：`services/container.py` 的 `ServiceContainer`
- 平台清单以及解析器、登录适配器对应关系：`platforms/registry.py`
- 平台入口和协议实现：`platforms/<platform>/parser.py`、支持登录平台的 `platforms/<platform>/login.py` 及同目录 `client.py`、`models.py`、`content.py` 模块
- 配置声明：`_conf_schema.json`
- 行为验证：`tests/`

跨平台能力进入 `core/` 或 `services/`，平台特有细节留在平台目录。每个平台由 `parser.py` 保留顶层解析入口；复杂平台将请求跳转、领域模型和内容转换分别放入 `client.py`、`models.py`、`content.py`，并从平台包的 `__init__.py` 导出公开解析器或登录提供者。

## 处理常见任务

### 修改解析器

平台解析器和业务服务不直接拼装 AstrBot 消息组件；结果渲染统一通过
`core/rendering.py` 的 `ParseResultRenderer` 完成。

平台请求优先使用 `BaseParser.http_client()`，统一超时、平台代理参数和重定向策略；仅在需要特殊客户端选项时直接创建 `httpx.AsyncClient`。

后处理服务优先使用 `ParseResult.content`、`ParseResult.media`、`ParseResult.diagnostics` 获取领域数据；`content_lines`、`image_references` 和 `media_metadata` 继续作为兼容视图。媒体投递和视频处理使用统一媒体视图；临时文件通过 `core/media.py` 的 `TemporaryFileRegistry` 登记和清理。合并转发决策与节点发送复用 `ForwardDeliveryService`，不要在 `DeliveryService` 重建 OneBot 序列化流程。
修改媒体元数据访问时运行 `tests/test_media_metadata_boundaries.py`，确保服务层没有绕过统一视图。

新增平台或跨层依赖时运行 `tests/test_architecture_dependencies.py`，保持平台适配器不依赖服务层、核心不依赖平台实现。

复用 `BaseParser`、`PlatformSpec`、`PluginSettings`、统一契约、安全 HTTP、平台代理、媒体和投递服务。新增解析器必须覆写 `BaseParser` 的 `match` 与 `parse`，并只通过平台注册表接入；`BaseParser` 本身提供可复用的 HTTP、Cookie 和媒体基础能力。配置值的布尔、数值、枚举和平台开关读取统一使用 `PluginSettings`，不要在服务或基础设施模块重复转换。AI 总结、会话历史等后处理优先使用 `ParseResult.content_lines` 和 `ParseResult.image_references`，不要重复遍历平台字段。新增请求客户端时接入 `core/http.py` 的平台代理参数，确保解析、登录和插件侧媒体请求遵循同一平台开关。保持内容顺序，区分鉴权失败、网络失败、内容不存在和部分媒体失败。新增平台时更新平台包导出、`platforms/registry.py`、`platforms/__init__.py`、`_conf_schema.json`、README、项目事实文档和测试，并运行注册表的 `validate_platform_registry()`、`validate_platform_configuration()` 校验；按用户可见程度更新 CHANGELOG。核心与服务公共扩展点优先通过 `core/__init__.py`、`services/__init__.py` 惰性导出，并同步包边界测试；`services/configuration.py` 与 `services/authentication.py` 从注册表装配，只有装配语义变化时才修改。

修改自动链接解析入口、平台解析器、表情回应或投递流程时，先核对 AstrBot 的事件传播和默认 LLM 触发条件。自动解析只能附加解析输出，禁止停止事件、修改或消费原消息、设置 LLM 禁用状态，或主动请求 LLM 接管后续流程；发送解析结果后仍须让后续插件与 AstrBot 默认流程按原规则处理。若发送副作用会改变事件状态，恢复进入解析处理器前的原值，并用成功解析、匹配异常、解析异常、未匹配和入口已有发送状态测试防止回归。

### 修改平台登录

登录流程编排与用户可见文案分离；状态、错误、过期和用户信息文案统一复用 `LoginMessageFormatter`。

同一平台登录互斥、按私聊取消、活动会话快照和插件卸载清理统一复用 `LoginSessionRegistry`。

视频超限回退只负责选择动作；视频直链摘要和群/私聊路由统一复用 `ForwardLinkDeliveryService`。

复用 `core/platform_login.py` 的契约和 HTTP 基类，以及 `services/authentication.py` 的编排。保留管理员权限边界，以 `main.py` 和测试确认每条命令是否限制私聊。限制二维码与重定向域名，只持久化最小 Cookie；成功、状态和错误输出不得泄漏凭据。遇到风控或设备验证时终止，不尝试绕过。

### 修改配置或依赖

配置变化同步 `_conf_schema.json`、README 和测试，并检查配置服务是否需要调整。项目环境统一通过 `uv` 管理，不假设仓库、AstrBot 或虚拟环境位于固定目录。改变依赖时同步 `pyproject.toml`，运行依赖还需同步 `requirements.txt`。

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
