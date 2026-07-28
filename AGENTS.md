# AstrBot 多平台内容解析插件开发指南

## 适用范围

本文件适用于本仓库的代码、测试、配置和文档。先遵循用户要求和上级指令；冲突时以优先级更高的指令为准。

## 项目概览

- 这是 AstrBot 插件，支持 B站、抖音、小红书、贴吧、微博、微信、小黑盒、知乎、GitHub 和 Pixiv。
- `main.py` 负责插件注册、命令入口和服务装配，不承载平台解析细节。
- `core/` 保存跨平台契约和基础能力；`services/` 保存编排与策略；`platforms/` 保存平台特有实现；`tests/` 保存 pytest 测试。
- `metadata.yaml` 是插件版本的唯一来源。除非用户明确要求发布或升版，不修改版本号。
- 当前代码和文档冲突时，以经过测试验证的代码行为为准，并在同次变更中修正文档。

## 开发环境

项目使用 `uv` 管理 Python、虚拟环境和开发依赖。仓库可以位于任意目录，不要求 AstrBot 源码、虚拟环境或本仓库具有固定的盘符、父目录或相对位置。

先确定仓库路径；以下命令中的 `<repo>` 是 `git rev-parse --show-toplevel` 返回的路径：

```bash
uv sync --project <repo> --locked
uv run --project <repo> pytest <repo>/tests
uv run --project <repo> ruff check <repo>
```

在仓库根目录工作时可省略 `--project <repo>` 和测试路径前缀：

```bash
uv sync --locked
uv run pytest
uv run ruff check .
```

- 不使用裸 `python`、`pip` 或已激活环境来代替 `uv run` 和 `uv sync`。
- 不硬编码 `.venv` 的绝对路径，也不假定虚拟环境已激活；环境位置由 `uv` 配置决定。
- `uv.lock` 应随依赖声明提交。正常验证使用 `--locked`，依赖确需变化时更新声明后运行 `uv lock`。
- `requirements.txt` 仍是 AstrBot 安装插件运行依赖的入口；改变运行依赖时同步维护它和 `pyproject.toml` 的开发依赖。
- 进入仓库后若发现 `mise.toml`、`.mise.toml` 或 `.tool-versions`，先遵循其中的运行时版本；当前仓库没有这些文件。

## 模块边界

| 需求 | 首选位置 |
| --- | --- |
| 插件注册、事件入口、依赖装配 | `main.py` |
| 解析结果和上下文契约 | `core/contracts.py` |
| 登录契约、登录 HTTP 基类和二维码渲染 | `core/platform_login.py` |
| 安全 HTTP、可信 URL、Cookie、媒体和渲染 | `core/http.py`、`core/media.py`、`core/rendering.py` |
| 解析器公共流程 | `core/parser.py` |
| 平台清单及解析器、登录适配器对应关系 | `platforms/registry.py` |
| 配置读取和解析器创建 | `services/configuration.py` |
| 登录编排、取消和凭据持久化 | `services/authentication.py` |
| 消息上下文、文本处理和投递 | `services/message_context.py`、`services/text_processing.py`、`services/delivery.py` |
| 视频大小探测与发送策略 | `services/video.py` |
| 平台请求、签名、登录和载荷转换 | `platforms/<platform>/` |

跨平台规则放入 `core/` 或 `services/`；平台协议细节留在对应平台目录。Controller/命令入口只做权限与参数检查、调用服务并返回结果。

每个平台只保留一个顶层解析入口，当前平台清单以 `platforms/registry.py` 中的 `PLATFORM_REGISTRY` 为准。平台实现使用 `platforms/<platform>/` 目录，`parser.py` 负责顶层入口和路由，内容逻辑按职责拆入同目录模块；支持登录的平台另有 `login.py`。各平台从自己的 `__init__.py` 导出解析器或登录提供者。新增平台或调整导出时，同步检查：

- `platforms/registry.py`
- `platforms/__init__.py`
- `_conf_schema.json`
- README 和对应测试

`services/configuration.py` 和 `services/authentication.py` 从注册表装配解析器与登录适配器，只有装配语义变化时才修改。

## 登录与安全边界

- 管理命令必须保留 AstrBot 管理员权限过滤；私聊限制以 `main.py` 中各命令的当前实现和测试为准。
- 登录结果、状态、异常和日志不得输出 Cookie、令牌、二维码会话密钥或带敏感查询参数的完整 URL。
- Cookie 只保存解析所需的最小字段，并限制到对应平台；不要把凭据发送到无关域名。
- 二维码和重定向 URL 必须校验 HTTPS 与受信任域，并限制超时、重定向和响应大小。
- 遇到滑块、人机验证或设备验证时明确终止，不实现绕过、伪造设备或打码流程。
- 修改登录流程时覆盖成功、过期、取消、并发、持久化失败和敏感信息不泄漏测试。

## 实现约束

- 优先复用现有契约、服务和平台模式，只修改完成任务必需的文件。
- 解析器统一返回 `core/contracts.py` 中的契约，保持图文顺序和可读的失败信息。
- 外部请求复用 `core/http.py` 的安全能力；新增网络路径时检查 URL、重定向、超时和响应大小边界。
- 登录适配器复用 `HTTPPlatformLoginProvider`、`read_login_response_body` 和公共二维码渲染；可信域、Cookie 值及 CookieJar 白名单序列化复用 `core/http.py`。
- 平台解析器或登录适配器的增删与顺序只在 `platforms/registry.py` 声明，配置和认证服务从注册表装配，不维护平行清单。
- 公开 API 和关键异步入口使用准确的中文文档字符串。注释解释边界、顺序、并发和降级原因，不逐行复述代码。
- 配置变化同步 `_conf_schema.json`、`services/configuration.py`、README 和测试。
- 不为单次需求增加兼容层、重复入口或无调用方的扩展点。

## 验证流程

1. 修改前查看 `git status`，搜索相似实现和所有调用方。
2. 修复缺陷时先增加可复现的回归测试；新增行为覆盖正常、无效输入和外部失败路径。
3. 先运行受影响测试，再运行全量测试、Ruff 检查，并检查本次修改的 Python 文件格式。
4. 涉及插件加载、协议端媒体发送或表情回应时，说明还需要 AstrBot 实例集成验证；不要把单元测试当成完整集成验证。
5. 提交前检查 `git diff --check`、差异范围和敏感信息。

常用验证命令：

```bash
uv run pytest tests/test_<area>.py -q
uv run pytest
uv run ruff check .
git diff --name-only --diff-filter=ACMR -- '*.py' | xargs -r uv run ruff format --check
uv run python -m compileall main.py core services platforms
git diff --check
```

## 提交与文档

- 提交信息使用 Conventional Commits：`<type>(<scope>): <中文主题>`，主题简洁、使用祈使表达、末尾不加句号。
- PR 使用中文说明行为变化、验证结果和兼容性影响。
- 用户功能、配置或安装方式变化时更新 README；用户可见变化按需更新 CHANGELOG。
- 仅在明确的发布任务中同步 `metadata.yaml`、README 版本徽章、CHANGELOG 和 Git 标签。
- 不提交凭据、调试日志、缓存、临时文件或无关格式化改动。
