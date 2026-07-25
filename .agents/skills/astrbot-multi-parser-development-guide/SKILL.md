---
name: astrbot-multi-parser-development-guide
description: 在 AstrBot 多平台内容解析插件仓库中开发、修复、重构或审查代码，并维护平台解析、登录、配置、测试、依赖、文档、版本和中文提交。任务涉及 main.py、core、services、platforms、tests、_conf_schema.json、pyproject.toml、README、CHANGELOG 或 metadata.yaml 时使用。
---

# AstrBot 多平台内容解析开发指南

## 开始工作

1. 读取仓库根目录的 `AGENTS.md`，以其中记录的项目事实、边界和验证方式为准。
2. 使用 `git status` 识别用户已有改动，再用 `rg` 查找相似实现、调用方、测试和文档。
3. 从代码确认当前行为，不沿用记忆中的平台状态、目录结构、命令权限或版本信息。
4. 将任务拆成可验证的小修改，避免把无关清理混入差异。

## 选择修改位置

- 插件注册、命令权限和服务装配：`main.py`
- 跨平台契约、安全 HTTP、媒体和渲染：`core/`
- 配置、登录、消息投递和视频策略编排：`services/`
- 平台协议、请求、签名和登录适配：`platforms/<platform>/`
- 配置声明：`_conf_schema.json`
- 行为验证：`tests/`

跨平台能力进入 `core/` 或 `services/`，平台特有细节留在平台目录。保持每个平台只有一个目录入口，并从 `__init__.py` 导出公开解析器或登录提供者。

## 处理常见任务

### 修改解析器

复用 `BaseParser`、统一契约、安全 HTTP、媒体和投递服务。保持内容顺序，区分鉴权失败、网络失败、内容不存在和部分媒体失败。新增或移动平台实现时同步注册、配置、README 和测试。

### 修改平台登录

复用 `core/authentication.py` 的契约和 `services/authentication.py` 的编排。保留管理员权限边界，以 `main.py` 和测试确认每条命令是否限制私聊。限制二维码与重定向域名，只持久化最小 Cookie；成功、状态和错误输出不得泄漏凭据。遇到风控或设备验证时终止，不尝试绕过。

### 修改配置或依赖

配置变化同步 `_conf_schema.json`、配置服务、README 和测试。项目环境统一通过 `uv` 管理，不假设仓库、AstrBot 或虚拟环境位于固定目录。改变依赖时同步 `pyproject.toml`、`uv.lock`，运行依赖还需同步 `requirements.txt`。

### 修改版本或发布资料

以 `metadata.yaml` 为唯一版本源。只有用户明确要求发布或升版时才修改版本，并同步 README 徽章、CHANGELOG 和 Git 标签；普通功能、修复和重构不升版。

## 验证与交付

使用 `AGENTS.md` 中的 `uv` 命令。先运行相关测试，再运行全量 pytest、Ruff 检查、本次修改的 Python 文件格式检查和 `git diff --check`。涉及真实适配器、插件加载或外部登录时，单独说明尚需集成验证。

提交前逐项确认：

- 差异只包含本次目标，未覆盖用户改动。
- 新行为有回归测试，安全边界和敏感信息不泄漏已覆盖。
- 文档与当前代码一致，没有固定盘符、父目录、解释器或虚拟环境路径。
- 提交和 PR 使用中文，提交信息符合 Conventional Commits。
