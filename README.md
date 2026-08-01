# AstrBot 多平台内容解析器

<p align="center">
  <img src="logo.png" alt="AstrBot 多平台内容解析器" width="180">
</p>

<p align="center">自动识别聊天消息中的内容链接，并发送作品信息、图文、视频或音频。</p>

<p align="center">
  <a href="https://github.com/Qfxaile/astrbot_multi_parser/releases"><img src="https://img.shields.io/badge/version-v1.1.0-2f6f5e" alt="Version v1.1.0"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-%3E%3D3.10-3776ab" alt="Python 3.10+"></a>
  <a href="https://astrbot.app/"><img src="https://img.shields.io/badge/AstrBot-plugin-4c78a8" alt="AstrBot Plugin"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2f6f5e" alt="MIT License"></a>
</p>

<div align="center">
  <img src="https://count.getloli.com/get/@Qfxaile-astrbot_multi_parser?theme=moebooru" alt="访问次数">
</div>

<p align="center">
  <a href="#功能与支持">功能与支持</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#配置">配置</a> ·
  <a href="#登录管理">登录管理</a> ·
  <a href="#常见问题">常见问题</a>
</p>

> [!NOTE]
> 本项目是 AstrBot 第三方社区插件，不属于 AstrBot 官方项目，也不受相关内容平台赞助、认可或维护。

## 功能与支持

- 无需命令，直接发送受支持的链接或分享卡片即可触发解析。
- 支持常见视频、直播、图文、文章、帖子及短链接。
- 图片保持源文件质量，不主动缩放或转码。
- 支持普通消息、阈值合并与始终合并三种图文发送策略。
- 发送视频前探测体积，超限时可提示、发送直链或尝试上传群文件。
- 可过滤解析结果中的网页链接，不修改用户发送的原消息。
- 可选将解析结果写入当前 AstrBot 会话，并选择仅保留文字或同时保留图片；默认关闭。

| 平台 | 视频 | 图文 | 短链与其他内容 |
| --- | --- | --- | --- |
| Bilibili | BV、AV | Opus、动态、专栏、会员购详情 | `b23.tv`、`bili2233.cn`、直播间、番剧/电影/纪录片介绍、会员购票务/商品/工房/市集链接 |
| 抖音 | 视频、直播 | 普通图文、Slides、商城商品标题与主图 | `v.douyin.com`、`jx.douyin.com`、抖音商城长链、汽水音乐 |
| 番茄小说 | 不支持 | 小说标题、作者、简介与封面 | `changdunovel.com/t/...` 公开分享链接 |
| 小红书 | 视频笔记 | 图文笔记 | `xhslink.com`、部分 JSON 分享卡片 |
| 贴吧 | 首帖视频 | 楼主首帖正文 | `tieba.baidu.com/p/<帖子ID>` |
| 微博 | 普通视频、视频页、TV | 微博、转发、长文章 | 桌面端、移动端及 API 分享链接 |
| 微信 | 视频号 | 公众号文章 | 视频号短链及已带令牌的预览长链 |
| 小黑盒 | 帖子和游戏视频 | 社区帖子、游戏截图 | BBS/API 分享链接、游戏信息 |
| 知乎 | 正文内视频 | 问题、回答、文章、想法 | `link.zhihu.com`、页面数据回退 |
| GitHub | 不支持 | 公开仓库 OpenGraph 卡片 | 仅仓库主页，不解析 Issue、PR、文件等子路径 |
| QQ空间 | 公开说说视频 | 公开说说正文与图片 | `h5.qzone.qq.com/ugc/share/`，无需登录 |
| 淘宝/天猫 | 不支持 | 商品标题与主图 | 商品长链、移动链接及 `m.tb.cn`、`e.tb.cn` 分享短链 |
| 京东 | 不支持 | 商品标题与主图 | 商品长链、移动链接及 `3.cn`、`u.jd.com` 分享短链 |
| 拼多多 | 不支持 | 商品标题与主图 | `mobile.yangkeduo.com` 商品链接及 `p.pinduoduo.com` 分享短链 |
| Pixiv（默认关闭） | 不支持 | 公开插画作品 | `pixiv.net/artworks/<作品ID>`、旧版 `illust_id` 链接 |

> [!IMPORTANT]
> 当前不支持 TikTok，也不解析 Bilibili 音频、独立音轨或 `au` 号。直播仅展示直播间信息，不提取或转发持续直播流；番剧、电影和纪录片仅展示作品介绍，不获取或发送视频。

## 快速开始

### 环境要求

| 项目 | 要求 |
| --- | --- |
| AstrBot | 建议使用最新稳定版 4.x |
| Python | 3.10 或更高版本，与 AstrBot 运行环境一致 |
| 网络 | 能够访问目标内容平台及其媒体 CDN |
| 依赖 | 安装插件时由 AstrBot 读取 `requirements.txt` |

### 插件市场安装

优先通过 AstrBot WebUI 的插件市场安装。安装或更新后，在插件管理页面重载插件，并按需调整配置。

### 手动安装

在 AstrBot 根目录执行：

```powershell
Set-Location data/plugins
git clone https://github.com/Qfxaile/astrbot_multi_parser.git astrbot_plugin_multi_parser
```

随后参考 [AstrBot 插件指南](https://docs.astrbot.app/dev/star/plugin-new.html) 完成依赖安装，并在 WebUI 中重载插件。

## 配置

所有配置均可在 AstrBot 插件配置页面修改。

### 解析与发送

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| `platform_switches` | 十四个平台启用，Pixiv 关闭 | 分别控制各平台的解析器；Pixiv 需显式开启 |
| `filter_output_links` | `false` | 替换解析结果中的网页链接，不修改用户原消息 |
| `filtered_link_text` | `[详细内容请打开原链接查看]` | 链接过滤后的替换文案 |
| `enable_conversation_history` | `false` | 是否将解析结果写入当前 AstrBot LLM 会话 |
| `conversation_history_mode` | `text_only` | `text_only` 仅保留文字，`text_and_images` 同时保留图片 |
| `forward_mode` | `threshold` | `always`、`threshold` 或 `never` |
| `forward_image_threshold` | `2` | 图片数严格超过该值时合并发送 |
| `forward_text_threshold` | `260` | 可见文字严格超过该字符数时合并发送 |
| `request_timeout_seconds` | `30` | 平台页面和接口请求超时，单位为秒 |
| `image_download_concurrency` | `4` | 并发图片下载数，范围为 `1`～`16` |
| `enable_parse_reaction` | `true` | OneBot v11 识别链接后添加表情回应 |

### 视频

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| `send_video_by_url` | `true` | 通过远程 URL 发送视频 |
| `max_video_size_mb` | `50` | 视频直发上限；小于等于 `0` 表示不限制 |
| `video_over_limit_action` | `direct_link` | 超限时仅提示、发送直链或尝试发送群文件 |
| `allow_unknown_video_size` | `false` | 大小未知时是否仍尝试直发 |
| `size_check_timeout_seconds` | `10` | 视频大小探测超时，单位为秒 |

### 平台凭据

平台凭据统一存放在 `cookies` 配置分组中。

| 配置项 | 是否必需 | 说明 |
| --- | :---: | --- |
| `cookies.bilibili_cookies` | 是 | 用于 B站内容解析 |
| `cookies.douyin_cookies` | 否 | 二维码登录暂不可用，可手工填写 |
| `cookies.redbook_cookies` | 否 | 提高部分内容或无水印资源的可用性 |
| `cookies.tieba_cookies` | 是 | 用于贴吧内容解析 |
| `cookies.weibo_cookies` | 否 | 用于需要登录态的微博页面 |
| `cookies.wechat_yuanbao_cookies` | 视频号短链需要 | 保存 `yb_user_id` 和 `yb_token` |
| `cookies.xiaoheihe_cookies` | 否 | 配置后用于游戏详情请求，未配置时使用公开接口 |
| `cookies.zhihu_cookies` | 是 | 用于知乎内容解析 |
| `cookies.taobao_cookies` | 是 | 用于淘宝和天猫商品解析 |
| `cookies.jd_cookies` | 否 | 手工填写，用于京东商品页面请求 |
| `cookies.pinduoduo_cookies` | 是 | 用于拼多多商品解析 |

GitHub 默认启用，仅解析公开仓库主页，不需要 Token；Issue、PR、文件、提交等仓库子路径不会触发解析。

番茄小说仅解析 `changdunovel.com/t/...` 公开分享链接，展示小说标题、作者、简介和封面，不抓取章节正文，也不需要 Cookie。

QQ空间仅解析匿名可访问的公开说说分享页，不读取或保存 QQ Cookie；私密说说、日志和相册暂不支持。

Bilibili 会员购解析覆盖可唯一定位 ID 的新旧票务、普通或商家商品、UP 主工房商品和魔力赏市集商品详情；首页、分类页、兑换列表、购物车和订单页不会触发解析。摘要展示价格、店铺或主办方及类型专属信息，并最多发送 6 张可信详情图。

淘宝/天猫和拼多多需要配置对应 Cookie；京东 Cookie 选填。Cookie 仅发送到对应平台的商品页面请求，不发送到图片 CDN。解析结果只展示商品标题和主图。

Pixiv 仅解析匿名可访问的公开插画作品，不需要 Cookie；动图、小说及登录、年龄或地区限制作品暂不支持。

> [!WARNING]
> Cookie 属于敏感凭据。请仅通过 AstrBot 配置页面或管理员私聊登录命令提供，不要写入代码、README、Issue、测试或日志。提交问题前请清理 URL 查询参数和个人信息。

平台明确返回鉴权拒绝且公开页面回退失败时，插件会提示配置 Cookie；已经配置时则提示凭据可能失效。普通网络错误、内容删除或单张图片下载失败不会误报为登录问题。

## 登录管理

登录、退出和取消命令仅允许 AstrBot 管理员在私聊中使用，平台名必须使用中文。
登录状态命令允许管理员在私聊或群聊中使用。

```text
/平台登录 <平台名>
/平台登录状态
/平台退出 <平台名>
/取消平台登录
```

例如：`/平台登录 B站`、`/平台退出 微博`。

| 平台 | 扫码客户端 | 当前状态 |
| --- | --- | --- |
| B站 | 哔哩哔哩 App | 可用 |
| 抖音 | 抖音 App | 获取 Cookie 暂未实现，请手工配置 |
| 小红书 | 小红书 App | 获取 Cookie 暂未实现，请手工配置 |
| 贴吧 | 百度 App | 可用，可能触发百度风控 |
| 微博 | 微博 App | 可用，仅保存微博域 `SUB` |
| 微信 | 微信 | 可用，授权腾讯元宝以解析视频号短链 |
| 小黑盒 | 小黑盒 App | 可用，使用官网原生二维码接口 |
| 知乎 | 知乎 App | 获取 Cookie 暂未实现，请手工配置 |

登录成功后，凭据会写入对应配置项，并返回当前账号的昵称和 UID；仅能可靠取得 UID 的平台只显示 UID。登录状态会实时查询已配置凭据对应的账号，平台接口或网络异常时显示“用户信息获取失败”，不会输出 Cookie。二维码过期、取消登录或插件卸载时会清理临时会话；同一平台同一时间只允许一个登录流程。

微信登录仅保存 `yb_user_id` 和 `yb_token`，并只在请求 `yuanbao.tencent.com` 换取视频号预览令牌时映射为认证头。公众号文章和已携带 `token/eid` 的视频号长链不依赖腾讯元宝登录态。

贴吧、微博、腾讯元宝和小黑盒登录可能触发滑块、人机或设备验证。插件不会绕过验证、伪造设备信息或调用打码服务，遇到风控时会终止流程并提示手工配置。

## 消息发送

### 适配器兼容性

插件通过 AstrBot 标准消息链发送文本和媒体，不限制消息适配器名单。

| 能力 | 适用范围 |
| --- | --- |
| 文本、图片、音频、视频 | 取决于适配器对 AstrBot 标准组件的支持 |
| 合并转发 | `aiocqhttp`、`satori` |
| 表情回应 | OneBot v11 |
| OneBot 群文件 | OneBot 群聊，失败时自动降级为直链 |

### 图文

- `always` 始终合并；`threshold` 超过图片或文字阈值时合并；`never` 始终使用普通消息链。
- 图片经安全校验后下载到临时文件，以原始字节发送，发送完成后立即清理。
- 图文内容保持原始顺序；单张图片失败时在原位置显示提示，其余内容继续发送。
- 合并转发会合并相邻文本；超过 100 个节点时才均衡分批。

> [!NOTE]
> `aiocqhttp` 合并转发会让 OneBot 协议端直接拉取已验证的图片 URL。协议端必须能够访问对应 CDN，且部分防盗链图片仍可能加载失败。

### 视频与音频

插件通过 `HEAD` 或 `Range` 请求探测视频大小，并按以下规则处理：

| 检查结果 | 处理方式 |
| --- | --- |
| 未超过上限 | 通过远程 URL 发送视频 |
| 超过上限 | 按配置提示、发送直链或尝试群文件 |
| 大小未知 | 根据 `allow_unknown_video_size` 决定直发或执行超限策略 |
| 群文件不可用或上传失败 | 自动降级为视频直链 |

插件先发送作品信息，再处理视频。关闭 `send_video_by_url` 时，视频地址只在作品摘要中出现一次。

汽水音乐单曲会先发送歌曲信息和封面，再通过 AstrBot `Record` 组件发送远程音频；音频地址校验失败时仍保留歌曲信息。

## 常见问题

<details>
<summary><strong>解析成功后为什么没有直接发送视频？</strong></summary>

视频可能超过 `max_video_size_mb`，也可能无法探测大小。插件会按 `video_over_limit_action` 处理；群文件仅适用于 OneBot 群聊，其他场景自动发送直链。

</details>

<details>
<summary><strong>为什么远程视频或图片发送失败？</strong></summary>

协议端通常无法携带插件请求使用的 Cookie 或 Referer。带有防盗链、时效限制或地区限制的 CDN 地址可能由插件成功解析，却无法被协议端拉取。

</details>

<details>
<summary><strong>为什么图片看起来被压缩了？</strong></summary>

插件不会主动缩放或转码图片，但协议端或目标聊天平台仍可能在接收后压缩。

</details>

<details>
<summary><strong>为什么同一个链接突然无法解析？</strong></summary>

内容平台可能调整页面结构、公开接口、签名规则或 CDN 策略。请先检查网络和 Cookie；多个链接同时失效时，通常需要更新插件或对应平台解析器。

</details>

## 安全与限制

- Cookie 仅随对应平台请求发送，不会附加到分享跳转目标或媒体 CDN。
- 图片 URL 必须使用 HTTP(S)、默认端口和受信任域名；私有地址与不安全重定向会被拒绝。
- 图片重定向最多跟随 5 次，每次跳转前都会重新校验目标地址。
- 图片错误日志只记录主机名和错误摘要，不输出带令牌的完整 URL。
- 图片使用临时文件并在发送后清理；视频和群文件使用远程 URL，不创建本地视频缓存。
- 平台接口、CDN 地址和登录流程可能随第三方服务调整而失效。
- 音频、视频、消息长度和媒体数量仍受目标适配器及聊天平台限制。

## 开发与贡献

```text
astrbot_plugin_multi_parser/
├── main.py          # 插件装配与事件调度
├── core/            # 领域契约、HTTP、媒体与渲染
├── services/        # 配置、登录、消息适配与投递策略
├── platforms/       # 平台适配器
├── tests/           # pytest 单元测试
└── _conf_schema.json
```

解析器统一继承 `core/parser.py` 中的 `BaseParser`，返回 `core/contracts.py` 中的 `ParseResult`。新增平台时应复用 `core/` 和 `services/` 的公共能力，并同步注册、配置和测试。

- 普通缺陷、功能建议和新平台适配请使用 [GitHub Issues](https://github.com/Qfxaile/astrbot_multi_parser/issues)。
- 安全漏洞或凭据泄漏风险请通过 [GitHub Security Advisories](https://github.com/Qfxaile/astrbot_multi_parser/security/advisories/new) 私下报告。
- 涉及插件加载、协议端媒体发送或表情回应的修改，需要通过 AstrBot 本地实例进行集成验证。

## 参考与致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：插件运行平台与开发 API。
- [AstrBot 消息发送指南](https://docs.astrbot.app/dev/star/guides/send-message.html)：统一消息链、富媒体组件与合并转发说明。
- [AstrBot 消息平台指南](https://docs.astrbot.app/platform/start.html)：消息平台接入文档。
- [Soulter/astrbot_plugin_github_cards](https://github.com/Soulter/astrbot_plugin_github_cards)：GitHub OpenGraph 仓库卡片方案参考，采用 AGPL-3.0 License。
- [Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)：微博、视频号、小黑盒和知乎解析实现的参考来源；相关实现基于其 MIT 许可代码重新设计。
- [Cloxl/xhshow](https://github.com/Cloxl/xhshow)：小红书实验性 Web 登录签名的参考实现，采用 MIT License；本插件不使用其设备指纹生成能力。

小黑盒请求签名、视频号“元宝换取令牌后请求官方预览接口”等流程均在上述参考项目基础上重新实现。第三方项目版权归原作者所有，许可证信息见项目 [LICENSE](LICENSE)。

## 免责声明

本项目仅提供公开链接的技术解析与消息展示能力，不提供内容托管、批量抓取或访问控制绕过服务。使用者应遵守所在地法律法规、AstrBot 使用规范、目标平台服务条款及内容版权要求，并妥善保管 Cookie、账号和协议端配置。

解析结果来自第三方平台，本项目不保证其准确性、完整性或持续可用性。各平台名称和商标归其权利人所有；本项目与 AstrBot 官方及各内容平台均无隶属、授权或背书关系。

## 许可证

本项目采用 [MIT License](LICENSE) 发布，软件按许可证约定“按原样”提供且不附带担保。
