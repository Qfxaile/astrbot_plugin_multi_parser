# AstrBot 多平台内容解析器

> 自动识别聊天消息中的 Bilibili、抖音、小红书、贴吧、微博、微信、小黑盒和知乎链接，并发送作品信息、正文图片、视频或音频。

<p align="center">
  <img src="logo.png" alt="icon" width="180">
</p>

[![Version](https://img.shields.io/badge/version-v1.0.1-2f6f5e)](https://github.com/Qfxaile/astrbot_multi_parser/releases)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.10-3776ab)](https://www.python.org/)
[![AstrBot Plugin](https://img.shields.io/badge/AstrBot-plugin-4c78a8)](https://astrbot.app/)
[![License](https://img.shields.io/badge/license-MIT-2f6f5e)](LICENSE)

<p align="center">
  <img src="https://count.getloli.com/get/@Qfxaile-astrbot_multi_parser?theme=moebooru" alt="访问次数">
</p>

本项目是面向 AstrBot 的第三方社区插件，不属于 AstrBot 官方项目，也不受下列内容平台赞助、认可或维护。

## 功能概览

- **自动识别链接**：无需命令，直接发送受支持的链接或分享卡片即可触发解析。
- **覆盖八个平台**：支持 Bilibili、抖音、小红书、贴吧、微博、微信、小黑盒和知乎的常见视频、直播、图文及分享链接。
- **保留原图质量**：图片在内存中下载后以原始字节发送，不主动缩放或转码。
- **灵活组织内容**：可选择始终合并、超过图片或文字阈值时合并，或始终普通发送。
- **控制视频体积**：发送前探测远程视频大小，超过限制时改为发送解析链接。
- **解析音乐分享**：识别抖音短链跳转的汽水音乐单曲，发送歌曲简介、封面和音频。
- **按需配置登录态**：大多数平台 Cookie 为可选项；视频号短链需要腾讯元宝 Web 登录令牌换取官方预览令牌。
- **管理员私聊登录**：八个平台均保留登录命令入口；抖音、小红书和知乎二维码登录获取 Cookies 暂未实现，当前调用会登录失败。

## 支持范围

| 平台 | 视频 | 图文 | 短链 | 其他内容 |
| --- | :---: | :---: | :---: | --- |
| Bilibili | BV 号、AV 号 | Opus 图文、专栏 | `b23.tv`、`bili2233.cn` | 动态、直播间 |
| 抖音 | 大陆抖音视频 | 普通图文、Slides | `v.douyin.com`、`jx.douyin.com` | 直播间、分享页链接、汽水音乐单曲 |
| 小红书 | 视频笔记 | 图文笔记 | `xhslink.com` | 部分 JSON 分享卡片 |
| 贴吧 | 首帖视频 | 楼主首帖正文 | - | `tieba.baidu.com/p/<帖子ID>` |
| 微博 | 普通视频、微博视频页、TV | 普通微博、转发微博、长文章 | `mapp.api.weibo.cn` | 桌面端和移动端微博 |
| 微信 | 视频号视频 | 微信公众号文章 | `weixin.qq.com/sph` | 已带 `token/eid` 的视频号预览长链 |
| 小黑盒 | 帖子视频、游戏视频 | 社区帖子、游戏截图 | BBS/API 分享链接 | 游戏简介、评分与价格 |
| 知乎 | 正文内视频 | 问题、回答、专栏文章、想法 | `link.zhihu.com` | 页面数据回退解析 |

> [!NOTE]
> 当前不支持 TikTok，也不解析 Bilibili 音频、独立音轨或 `au` 号。

直播链接解析展示直播间标题、主播、开播状态、封面和平台提供的观看信息。插件不会提取或转发持续直播流，也不会在解析结果中重复发送直播间链接。

### 消息适配器

插件不限制消息适配器名单。只要适配器能够产生标准 `AstrMessageEvent` 并发送 AstrBot 统一消息链，就可以触发解析和接收结果；AstrBot 后续新增的适配器也会默认走这条通用路径，无需修改插件配置。

文本与图片不按消息平台分支，统一通过 AstrBot 标准组件发送；音频和视频使用 `Record`、`Video` 组件，最终能力和体积限制仍取决于目标平台。合并转发节点仅对 AstrBot 当前实现了该组件的 `aiocqhttp` 和 `satori` 启用，其他适配器自动使用普通消息链。表情回应是 OneBot v11 专属增强，不会在其他平台调用协议端 API。

### 环境要求

- AstrBot：建议使用最新稳定版 4.x；本项目尚未锁定最低 AstrBot 版本。
- Python：3.10 或更高版本，与 AstrBot 运行环境保持一致。
- 网络：AstrBot 实例需要能够访问目标内容平台及其媒体 CDN。
- 依赖：AstrBot 安装插件时会读取根目录 `requirements.txt`。

## 安装

### 插件市场

优先通过 AstrBot WebUI 的插件市场安装。安装或更新后，在插件管理页面重载插件，并按需修改配置。

### 手动安装

在 AstrBot 根目录下执行：

```powershell
Set-Location data/plugins
git clone https://github.com/Qfxaile/astrbot_multi_parser.git astrbot_plugin_multi_parser
```

随后按 [AstrBot 官方插件指南](https://docs.astrbot.app/dev/star/plugin-new.html) 完成依赖安装，并在 WebUI 中重载插件。插件目录名建议保持为小写且以 `astrbot_plugin_` 开头。

## 配置说明

所有配置均可在 AstrBot 插件配置页面中修改。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `platform_switches` | 对象（布尔开关） | 八个平台全部启用 | 分别控制 B站、抖音、小红书、贴吧、微博、微信、小黑盒和知乎解析器 |
| `forward_mode` | 选项 | `threshold` | 内容发送方式：始终合并、超过阈值时合并或始终不合并（不推荐） |
| `forward_image_threshold` | 整数 | `2` | 阈值模式下，图片数量严格超过该值时合并发送 |
| `forward_text_threshold` | 整数 | `260` | 阈值模式下，最终可见文字字符数严格超过该值时合并发送 |
| `request_timeout_seconds` | 整数 | `30` | 平台页面和接口请求超时，单位为秒 |
| `image_download_concurrency` | 整数 | `4` | 同时下载的图片数量，取值范围为 `1`～`16` |
| `send_video_by_url` | 布尔值 | `true` | 是否通过远程 URL 直接发送解析到的视频 |
| `max_video_size_mb` | 浮点数 | `50` | 视频直发体积上限，单位为 MB；小于等于 `0` 表示不限制 |
| `allow_unknown_video_size` | 布尔值 | `false` | 无法探测视频大小时，是否仍尝试直接发送 |
| `size_check_timeout_seconds` | 浮点数 | `10` | 视频大小探测超时，单位为秒 |
| `enable_parse_reaction` | 布尔值 | `true` | 识别到受支持链接后，是否通过 OneBot v11 给原消息添加表情回应 |
| `cookies` | 对象 | 各项为空 | 集中管理所有平台的 Cookies |
| `cookies.bilibili_cookies` | 文本 | 空 | 可选；用于 B站页面和接口请求，可提高登录态或风控场景下的解析成功率 |
| `cookies.douyin_cookies` | 文本 | 空 | 可选；用于抖音内容解析；二维码登录入口保留，但当前会登录失败，请手动填写 |
| `cookies.redbook_cookies` | 文本 | 空 | 可选；可提高部分内容或无水印资源的可用性；二维码登录入口保留，但当前会登录失败，请手动填写 |
| `cookies.tieba_cookies` | 文本 | 空 | 可选；用于贴吧页面请求，可降低安全验证导致的解析失败 |
| `cookies.weibo_cookies` | 文本 | 空 | 可选；用于需要登录态的微博页面请求 |
| `cookies.wechat_yuanbao_cookies` | 文本 | 空 | 解析视频号短链时必填；扫码命令会保存 `yb_user_id`、`yb_token` |
| `cookies.xiaoheihe_cookies` | 文本 | 空 | 可选；未配置时自动申请匿名设备令牌，也可通过管理员私聊使用小黑盒 App 扫码登录 |
| `cookies.zhihu_cookies` | 文本 | 空 | 可选；用于知乎页面和接口请求；二维码登录入口保留，但当前会登录失败，请手动填写 |

> [!WARNING]
> Cookie 属于敏感凭据。请仅通过 AstrBot 配置页面或管理员私聊登录命令提供，不要写入代码、README、Issue、测试样例或日志。提交问题前请先删除 URL 查询参数中的令牌及日志中的个人信息。

当平台明确返回未登录、鉴权拒绝或安全验证结果，并最终无法通过公开页面回退获取内容时，插件会提示配置 Cookies；如果已经配置，则提示 Cookies 可能已失效。普通网络错误、内容删除和单张图片下载失败不会显示该提示。

微信公众号文章不使用腾讯元宝登录态。视频号浏览器预览长链如果已经携带 `token` 和 `eid`，可直接请求腾讯视频号预览接口；App 分享的 `weixin.qq.com/sph/...` 短链不包含这两个参数，因此需要先通过用户自己的腾讯元宝登录态换取。插件只在元宝换票请求中把令牌映射为 `X-ID`、`X-Token`，不会发送到公众号、视频号或媒体 CDN；手工填写的 Cookies 也仅绑定到元宝域。

### 管理员私聊登录

八个平台均保留管理员私聊登录命令入口。微信命令通过微信开放平台授权腾讯元宝；小黑盒命令使用小黑盒官网原生二维码接口，二维码必须用小黑盒 App 扫描。抖音、小红书和知乎二维码登录获取 Cookies 暂未实现，当前调用会登录失败，需要在配置页面手动填写。命令只接受中文平台名，非管理员不会进入登录流程，群聊中也不会发送二维码或展示登录状态。

```text
/平台登录 B站
/平台登录 抖音
/平台登录 小红书
/平台登录 贴吧
/平台登录 微博
/平台登录 微信
/平台登录 小黑盒
/平台登录 知乎
/平台登录状态
/平台退出 B站
/平台退出 抖音
/平台退出 小红书
/平台退出 贴吧
/平台退出 微博
/平台退出 微信
/平台退出 小黑盒
/平台退出 知乎
/取消平台登录
```

平台登录命令会发送一次性二维码并等待手机确认；成功后写入对应平台的 Cookies 配置并保存。登录失败统一显示为“登录失败｜平台：平台名｜原因：具体原因”。小黑盒登录保存官方响应中的账号凭据 `pkey`、`heybox_id` 和可选设备令牌 `x_xhh_tokenid`，缺少设备令牌时由解析器按匿名流程申请。同一平台同一时间只允许一个登录流程，二维码过期、取消登录或插件卸载时会清理临时会话。

微信命令使用微信开放平台二维码授权腾讯元宝，只保存视频号短链解析所需的 `yb_user_id` 和 `yb_token`。两项令牌仅在请求 `yuanbao.tencent.com` 时分别映射为 `X-ID`、`X-Token`，不会发送到公众号、视频号预览接口、二维码图片或媒体 CDN。

贴吧使用百度账号官方 Web 二维码流程。登录成功后仅保存贴吧页面解析所需的百度登录 Cookie，并且只在请求 `tieba.baidu.com` 页面时发送。

微博登录仅保存解析所需的微博域 `SUB` Cookie。

贴吧、微博、腾讯元宝和小黑盒 Web 登录可能根据网络、设备或账号状态触发滑块、人机验证、设备验证或其他平台风控。插件不会绕过验证、伪造设备信息或调用打码服务；出现这些情况时会终止登录并提示稍后重试或手工配置 Cookies。各平台短信登录都可能依赖额外验证，当前纯私聊流程不提供验证码兜底。

登录命令保存的 Cookie 与在配置页面手工填写的 Cookie 使用同一个配置项，并按 AstrBot 当前配置存储方式落盘。二维码令牌、Cookie、手机号和验证码不会写入插件日志。

## 消息发送策略

### 图文内容

1. 插件先整理标题、作者、简介等元数据。
2. Bilibili 图文按正文、图片和图片失败提示在原内容中的顺序发送。
3. 图片会先经过安全校验并下载到临时文件，发送完成后立即清理；使用 `aiocqhttp` 合并转发时，插件会直接把已验证的原始图片 URL 交给 OneBot 拉取，避免在 WebSocket 中传输 Base64。
4. `forward_mode` 可选择以下三种方式：

| 模式 | 处理方式 |
| --- | --- |
| `always` | 标题、作者、简介、正文和图片始终放入一条合并转发消息 |
| `threshold` | 图片数严格超过 `forward_image_threshold`，或最终可见文字字符数严格超过 `forward_text_threshold` 时，使用一条合并转发消息 |
| `never` | 始终使用普通消息链，不推荐，可能造成刷屏 |

5. `threshold` 模式默认在图片超过 2 张或文字超过 260 字时合并；两个条件满足任意一个即可触发。
6. 合并转发仅用于 `aiocqhttp` 和 `satori`；其他平台自动使用普通消息链，不会调用 OneBot API。
7. 单张图片下载失败时，原位置会显示“第 N 张图片获取失败”，其余内容继续发送。
8. 合并转发会先合并相邻文本并保留图文顺序；只有超过 QQ 官方 100 节点上限时才均衡分批，投递失败或超时不会自动拆分重试。

> [!NOTE]
> OneBot 协议端必须能够直接访问图片 CDN。部分需要 Cookie 或 Referer 的防盗链图片可能无法由协议端加载，此时应检查协议端网络和对应平台的图片访问限制。

### 视频内容

启用 `send_video_by_url` 后，插件只通过 `HEAD` 或 `Range` 请求探测文件大小，不会把完整视频下载到本地。

| 检查结果 | 处理方式 |
| --- | --- |
| 文件大小未超过 `max_video_size_mb` | 通过远程 URL 单独发送视频 |
| 文件大小超过限制 | OneBot 使用原生转发、Satori 使用节点消息，其他平台发送普通文本解析链接 |
| 文件大小未知，且不允许未知大小 | 按消息平台能力发送解析链接 |
| 文件大小未知，但允许未知大小 | 尝试通过远程 URL 发送视频 |
| 解析链接投递失败 | 降级为被动普通文本，附带失败原因和视频链接 |

插件会先发送作品信息，再处理视频。关闭 `send_video_by_url` 时，视频 URL 只在作品摘要中出现一次；即使视频发送失败，已经解析到的标题和封面仍会保留。

### 音频内容

抖音短链跳转到汽水音乐单曲页时，插件会先发送歌曲名、歌手、页面简介和封面，再通过 AstrBot `Record` 组件单独发送远程音频。音频 CDN 地址会经过协议、端口和受信任域名校验；校验失败时保留歌曲信息并显示无法获取安全音频直链的提示。

## 常见问题

<details>
<summary><strong>为什么解析成功了，却没有直接发出视频？</strong></summary>

视频可能超过 `max_video_size_mb`，也可能无法通过 `HEAD` 或 `Range` 获取大小。此时插件会按消息平台能力发送解析链接；不支持合并转发的平台使用普通文本。可以根据适配器能力调整 `max_video_size_mb` 或 `allow_unknown_video_size`。

</details>

<details>
<summary><strong>为什么视频 URL 在协议端发送失败？</strong></summary>

插件不会下载视频，而是让协议端拉取远程 URL。协议端通常无法携带插件请求使用的 Cookie 或 Referer，因此部分有防盗链或时效限制的 CDN 地址可能失效。

</details>

<details>
<summary><strong>为什么图片看起来被压缩了？</strong></summary>

插件发送的是平台源文件字节，不会主动缩放或转码 JPEG、WebP 等图片。协议端或目标聊天平台仍可能在接收后再次压缩。

</details>

<details>
<summary><strong>为什么表情回应没有出现？</strong></summary>

表情回应仅适用于 OneBot v11，并使用插件内置的回应动作和表情 ID。其他消息平台会跳过该增强；协议端不支持、消息 ID 缺失或调用失败时只记录日志，不会中断内容解析。

</details>

<details>
<summary><strong>为什么同一个链接突然无法解析？</strong></summary>

平台页面结构、公开接口、签名规则和 CDN 策略都可能变化。可以先检查 Cookie 与网络连通性；如果多个链接同时失效，通常需要更新对应平台解析器。

</details>

## 安全与隐私

- 各平台 Cookie 仅随对应平台域请求发送，不会带到分享跳转目标或跨域图片 CDN；微博扫码登录只持久化微博域 `SUB`，腾讯元宝令牌只附加到元宝换票请求。
- 小黑盒未配置 Cookie 时会向其设备指纹服务申请匿名设备 ID，不会上传聊天内容或用户凭据；原生扫码登录只访问小黑盒官方接口，登录 Cookie 只随小黑盒 API 请求发送，不会发送到媒体 CDN。
- 图片地址必须使用 HTTP(S)、默认端口和受信任的平台域名；私有地址及不安全重定向会被拒绝。
- 图片重定向最多跟随 5 次，并在每次跳转前重新校验目标地址。
- 图片错误日志仅记录主机名和错误摘要，避免泄漏带令牌的完整 URL。
- 图片仅在内存中短暂处理；视频始终使用远程 URL，不创建本地媒体缓存。

## 项目结构

```text
astrbot_plugin_multi_parser/
├── main.py                    # 插件装配、事件监听与解析调度
├── core/                      # 解析与登录契约、HTTP 安全、媒体物化和渲染
├── services/                  # 登录、配置迁移、消息交付和视频策略
├── platforms/                 # 各内容平台适配器
│   ├── bilibili/              # B站内容解析与二维码登录
│   │   ├── parser.py          # B站视频、直播、动态、专栏和图文解析
│   │   └── login.py           # B站二维码会话与 Cookie 提取
│   ├── douyin/                # 抖音内容解析与实验性二维码登录
│   │   ├── parser.py          # 抖音链接路由、作品与直播解析
│   │   ├── music.py           # 汽水音乐字段提取与音频地址校验
│   │   └── login.py           # 抖音二维码实验实现，入口保留
│   ├── redbook/               # 小红书内容解析与实验性二维码登录
│   │   ├── parser.py          # 小红书页面回退、笔记与媒体解析
│   │   ├── login.py           # 小红书二维码实验实现，入口保留
│   │   └── signing.py         # 小红书 Web 登录请求签名边界
│   ├── tieba/                 # 贴吧首帖解析与百度二维码登录
│   │   ├── parser.py          # 贴吧首帖正文、图片与视频解析
│   │   └── login.py           # 百度二维码会话、风控识别与 Cookie 提取
│   ├── weibo/                 # 微博内容解析与二维码登录
│   │   ├── parser.py          # 微博状态、视频、长文章与分享链接解析
│   │   └── login.py           # 微博二维码会话、SSO 与最小 Cookie 提取
│   ├── wechat/                # 微信内容解析与腾讯元宝扫码登录
│   │   ├── article.py         # 公众号 HTML 与有序图文提取
│   │   ├── channels.py        # 视频号令牌交换与预览接口
│   │   ├── login.py           # 微信 OAuth 二维码与元宝令牌提取
│   │   └── parser.py          # 微信链接识别与路由
│   ├── xiaoheihe/             # 小黑盒内容解析与原生扫码登录
│   │   ├── parser.py          # 路由、请求上下文与 Cookie 边界
│   │   ├── login.py           # 小黑盒二维码轮询与登录凭据提取
│   │   ├── post.py            # 帖子正文与媒体提取
│   │   ├── game.py            # 游戏详情与媒体提取
│   │   ├── signing.py         # 小黑盒接口签名
│   │   └── fingerprint.py     # 匿名设备指纹载荷
│   └── zhihu/                 # 知乎内容解析与实验性二维码登录
│       ├── parser.py          # 知乎链接路由与内容解析
│       └── login.py           # 知乎二维码实验实现，入口保留
├── tests/                     # pytest 单元测试
├── _conf_schema.json          # AstrBot 插件配置定义
├── metadata.yaml              # AstrBot 插件市场元数据
├── requirements.txt           # Python 运行时依赖
├── CHANGELOG.md               # 版本变更记录
└── LICENSE                    # 项目及第三方许可声明
```

解析器统一继承 `core/parser.py` 中的 `BaseParser`，返回 `core/contracts.py` 中的 `ParseResult`。新增平台时应优先复用 `core/` 与 `services/` 的稳定能力，通常需要：

1. 在 `platforms/` 中实现新的解析器类。
2. 实现链接匹配和内容解析，并复用统一图片物化逻辑。
3. 在 `platforms/__init__.py` 导出解析器。
4. 在 `services/configuration.py` 注册解析器类型。
5. 在 `_conf_schema.json` 的 `platform_switches` 中添加对应布尔开关。
6. 为正常输入、空值、格式错误和网络失败添加测试。

## 开发与验证

插件依赖 AstrBot API，推荐按官方目录布局开发：

```powershell
git clone https://github.com/AstrBotDevs/AstrBot.git
Set-Location AstrBot/data/plugins
git clone https://github.com/Qfxaile/astrbot_multi_parser.git astrbot_plugin_multi_parser
Set-Location astrbot_plugin_multi_parser
```

涉及插件加载、协议端媒体发送或表情回应的修改，还需要启动 AstrBot，在 WebUI 中重载插件并进行集成验证。

## 已知限制

- 平台页面和公开接口发生变化后，对应解析器可能需要同步更新。
- 远程视频能否发送，取决于协议端及目标聊天平台是否支持远程视频 URL。
- 汽水音乐音频能否发送，取决于协议端是否支持远程音频 URL，以及临时 CDN 地址是否仍在有效期内。
- 内容合并转发目前仅对 `aiocqhttp` 和 `satori` 启用，其他适配器使用普通消息链。
- 各消息平台对远程音频、视频、单条消息长度和媒体数量的限制不同；插件无法绕过平台自身限制。
- 视频链接的有效期、防盗链策略和可访问区域由内容平台决定。
- 视频号短链依赖腾讯元宝当前的 Web 登录态和解析接口；令牌失效或腾讯调整接口后需重新登录或更新解析器。

## 贡献与安全

- 普通缺陷、功能建议和新平台适配可通过 [GitHub Issues](https://github.com/Qfxaile/astrbot_multi_parser/issues) 讨论。
- 安全漏洞或凭据泄漏风险请通过 [GitHub Security Advisories](https://github.com/Qfxaile/astrbot_multi_parser/security/advisories/new) 私下报告，不要提交公开 Issue。
- 提交第三方代码或算法前，必须确认许可证兼容，并在 README 与 `LICENSE` 中保留来源和许可声明。

## 参考项目与致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：插件运行平台与开发 API。本项目遵循其插件目录、元数据、异步网络、日志和调试规范。
- [AstrBot 消息发送指南](https://docs.astrbot.app/dev/star/guides/send-message.html)：统一消息链、富媒体组件与合并转发能力说明。
- [AstrBot 消息平台指南](https://docs.astrbot.app/platform/start.html)：当前内置消息平台及接入文档。
- [Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)：微博、视频号、小黑盒和知乎解析实现的参考来源；小黑盒签名算法与匿名设备指纹请求参数在其 MIT 许可实现基础上改写。
- [Cloxl/xhshow](https://github.com/Cloxl/xhshow)：小红书实验性 Web 二维码登录请求使用的纯 Python 签名库，采用 MIT License；当前二维码登录尚未实现，且插件不使用其设备指纹生成能力。

参考范围包括 `platforms/weibo/parser.py`、`platforms/wechat/channels.py`、`platforms/xiaoheihe/` 和 `platforms/zhihu/`。视频号的“元宝换取令牌，再请求官方预览接口”流程参考其 `ShipinhaoParser` 重新实现。上游项目采用 [MIT License](https://github.com/Zhalslar/astrbot_plugin_parser/blob/master/LICENSE)。感谢上述项目及其贡献者。

## 免责声明

- 本项目仅提供公开链接的技术解析与消息展示能力，不提供内容托管、下载站或访问控制绕过服务。
- 使用者应遵守所在地法律法规、AstrBot 使用规范、目标平台服务条款及内容版权要求；不得将本项目用于侵权、批量抓取、规避风控或其他滥用行为。
- Cookie、账号和协议端配置由使用者自行保管。因配置不当、凭据泄漏、账号限制、内容丢失或第三方服务变化造成的损失，项目维护者不承担责任。
- 解析结果来自第三方平台，项目不保证其准确性、完整性、持续可用性或适用于特定目的。
- 各平台名称和商标归其权利人所有；本项目与 AstrBot 官方及各内容平台均无隶属、授权或背书关系。

软件本身还受 MIT 许可证中的“按原样提供、无担保”条款约束。

## 许可证

本项目采用 [MIT License](LICENSE) 发布。参考项目的版权归原作者所有。
