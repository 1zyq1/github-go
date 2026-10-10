# GitHub 文件加速

粘贴 GitHub 地址，解析为同源链接并由后端流式转发。支持 Release 资产、源码归档、Raw 与 Blob 文件，保留 `Range` 与分块响应，因此大文件可以断点续传；仓库页、用户主页等 GitHub 页面则直接给出同源镜像地址。输入框接受简写：填主页名 `1zyq1` 或项目 `acme/widgets` 即可，前端自动补全为 `https://github.com/...` 后再交给后端校验。

后端 FastAPI + HTTPX2（真实 HTTP/2），前端 Vue 3 + Vite + TypeScript。

访问需要账号：注册或登录后，每个账号**每小时可以发起 10 次文件或页面访问**，超出即被拒绝并告知等待时间。

站点另有一页公开的数据页 `/stats`，按 1 小时、24 小时、7 天、1 个月、全部五个窗口展示下载次数、页面浏览与访问用户数。

## 支持范围

| 类型 | 接受 | 上游 |
| --- | --- | --- |
| Release 资产 | `github.com/{owner}/{repo}/releases/download/...` | `release-assets.githubusercontent.com`、`objects.githubusercontent.com` |
| 源码归档 | `github.com/{owner}/{repo}/archive/...` | `codeload.github.com` |
| Raw | `raw.githubusercontent.com/...`、`github.com/.../raw/...` | `raw.githubusercontent.com` |
| Blob | `github.com/.../blob/...?raw=1` | 重写为 `.../raw/...` |
| 页面 | `github.com/{owner}`、`github.com/{owner}/{repo}/...` | 同源镜像 `github.com`，不走 `/proxy/` |

文件类返回 `proxy_url` 为 `/proxy/{token}`；页面类返回 `kind: "page"` 与镜像路径本身，例如 `github.com/1zyq1` 得到 `/1zyq1`。文件优先判定，因此 `.../archive/refs/heads/main.zip` 仍按归档下载而非镜像页面。

明确拒绝：git clone、Gist、GitHub API、其他 CDN、任意网址。服务不是开放代理——只有 GitHub 官方域名可以通过。

## 快速开始

需要 Python 3.13、Node.js 与 pnpm 10.34.5。

```powershell
uv sync
corepack pnpm@10.34.5 --dir frontend install
corepack pnpm@10.34.5 --dir frontend build
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/>，首次访问会要求注册或登录，之后按账号计量；页头导航可以切到公开的数据页 `/stats`。`pnpm build` 未执行时服务仍可启动，但只提供 API（同样需要登录）。

字体是自托管的（4.36 MiB），因为 CSP 为 `font-src 'self'`。重新抓取用 `uv run python scripts/fetch_fonts.py`。

## 接口

```
GET  /api/health          -> {"service":"github-accelerator","status":"ok"}
POST /api/auth/register   -> {"authenticated":false,"username":"...","quota":null,"email":"...","email_verified":false}
POST /api/auth/login      -> 登录成功后为 {"authenticated":true,"username":"...","quota":{...},"email":"...","email_verified":true}
POST /api/auth/logout     -> {"authenticated":false,"username":null,"quota":null}
GET  /api/auth/me         -> 当前登录状态、剩余额度与邮箱验证状态
POST /api/auth/verify/request -> 重新发送验证邮件（需要密码）
GET  /api/auth/verify?token=... -> 浏览器 303 回 /?verified=1 或 /?verify_failed=1；脚本收到 JSON
GET  /api/stats           -> {"windows":[{"key":"hour","downloads":0,"pages":0,"users":0}, ...]}
POST /api/resolve         -> {"kind":"...","source_url":"...","proxy_url":"/proxy/..." | "/1zyq1"}
GET  /proxy/{token}       -> 流式下载（支持 HEAD）
```

`POST /api/resolve` 请求体为 `{"url": "..."}`，URL 长度 1..8192。先按文件类判定，失败再按页面类判定，两者都失败才报错，因此原有错误语义不变。文件类的 token 是源地址的 base64url 编码，不含密钥；页面类不签发 token，直接返回镜像路径。

错误统一为 `{"error":{"code":"...","message":"..."}}`。

前端只把地址留在页面内存里，不做持久化、不写日志。

## 账号与额度

- 注册是 `POST /api/auth/register`，请求体 `{"username": "...", "password": "...", "email": "..."}`；登录是 `POST /api/auth/login`，请求体 `{"username": "...", "password": "..."}`。用户名 3..32 位，只允许字母、数字、`_`、`-`，且必须以字母或数字开头和结尾；密码 8..128 位、不做 trim；邮箱需形如 `name@example.com`。密码加盐后用 `scrypt`（N=16384）存储；登录态是随机 token，库里只存它的 SHA-256，明文只存在于 Cookie。
- **注册必须验证邮箱**：注册不再直接登录，返回 `email_verified:false` 且不下发 Cookie，服务器把验证链接发到填写的地址。浏览器点开链接会 303 回到 `/?verified=1` 并**顺手登录**——点开链接本身就证明了对地址的控制，所以落地页已经是登录态，不用再输一次密码；同一个浏览器里原来那个停在「验证邮箱」的标签页切回来、或浏览器窗口重获焦点时会自动变成已登录。链接失效则回到 `/?verify_failed=1`。脚本调用 `GET /api/auth/verify?token=...` 只拿 JSON（成功为 `email_verified:true`，失败为 400 `verification_invalid`），不会拿到 Cookie。
- **未验证的账号无法登录**：登录会返回 `email_verified:false` 且不设 Cookie，前端停在「验证邮箱」界面，可用 `POST /api/auth/verify/request`（需要密码）重发链接，默认 60 秒冷却，过于频繁返回 429 `verification_throttled`。没有邮箱的老账号在登录时会被要求补填地址，填好后同样走一次验证。
- 邮件发送失败时注册整体回滚（账号、用户名、邮箱都不占位），返回 503 `email_unavailable`。验证链接默认 24 小时有效，`VERIFICATION_TTL_SECONDS` 可调；同一账号同时只保留一封有效邮件，重发会作废旧链接。
- SMTP 用环境变量配置（`SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_STARTTLS`），兼容 QQ 邮箱、163、腾讯企业邮、Gmail 与 Resend。`SMTP_HOST` 留空时**不发送邮件**，链接会写进服务日志，便于本地开发。链接地址由 `PUBLIC_BASE_URL` 拼出，部署时要改成用户真正访问的地址。
- 登录态放在 `ga_session` Cookie 里（`HttpOnly`、`SameSite=Lax`），默认 30 天（`SESSION_TTL_SECONDS=2592000`）。这是**从登录那一刻算起的绝对有效期，不会因为持续使用而续期**，到期后重新登录即可。站点跑在 HTTPS 上时把 `SESSION_COOKIE_SECURE` 设为 `true`。
- **计价对象是「真正取走内容」**：`GET` 命中 `/proxy/{token}`（文件下载）或镜像页面时扣 1 次。`POST /api/resolve`（生成链接）、`HEAD`（探测存在性）、`POST /api/auth/*` 都不计入额度，所以反复解析不会平白掉次数。
- 默认 `USAGE_LIMIT=10`、`USAGE_WINDOW_SECONDS=3600`，按账号滑动窗口计算。额度用尽时：JSON 客户端收到 429 与 `Retry-After`；浏览器直接访问下载链接会被 302 送回 `/?limited=1`，页面上给出提示，生成按钮同时置灰。
- **失败不扣费**：上游返回非 200/206（例如 404、502）或请求抛错时，本次占用立即退还。
- 未登录的 JSON 客户端收到 401 `unauthorized`；浏览器导航被 302 送回 `/`。`/api/health`、`/api/stats` 与自有前端产物（`/`、`/stats`、`/assets/*`、`/fonts/*`）保持公开，否则登录页与数据页本身都加载不出来。
- 账号与用量存在 MySQL 里（必须配置 `MYSQL_HOST`、`MYSQL_USER`、`MYSQL_DATABASE`，表在首次使用时自动创建）。密码只存 scrypt 加盐摘要，会话令牌只存 SHA-256 摘要，库被读走也换不来可用口令或 Cookie。

## 与博客互通（跨站单点登录）

登录面板的「用博客账号登录」按钮与博客登录页的「使用 GitHub 加速站账号登录」按钮，是同一套
**签名断言**式单点登录：两边后端共用一把密钥（`GITHUB_ACCELERATOR_SSO_SHARED_SECRET`，与博客的
`SSO_SHARED_SECRET` 填**同一个值**），互相签发/验证短时效登录票据（HMAC-SHA256，默认 5 分钟），
按**邮箱**认人 —— 有同邮箱账号就直接登录，没有就自动建号（邮箱视为已验证）。配置见
`.env.example` 的「Cross-site single sign-on」段；留空即关闭（前端自动隐藏按钮）。

新增端点（`/api/sso/*` 在鉴权层按 `OPTIONAL` 处理，登录前后都能访问）：

```
GET /api/sso/providers      -> {"blog": true|false}   前端据此决定是否显示按钮
GET /api/sso/start          -> 302 到博客 authorize（本站作为 RP）
GET /api/sso/authorize      -> 已登录签发断言；未登录先回本站登录（本站作为 IdP）
GET /api/sso/callback       -> 验签断言 → 建号/登录 → 302 回 /?sso=ok
```

## 用博客积分兑换次数

额度用尽时，登录用户可以**用博客积分兑换额外次数**（默认 10 积分 → 10 次，可配）。加速站后端拿共享密钥签一张 `action=spend` 票据，**服务端到端**打到博客后端扣积分；扣成功才把次数加到本账号的「兑换额度」（`users.bonus_units`）。窗口额度用满后自动优先消耗兑换额度，因此可以超出每小时窗口上限。按邮箱认人（即 SSO 建立的绑定关系）—— 余额不足或没绑定博客账号会明确报错。

- 端点：`POST /api/redeem`（需登录，不消耗额度）。返回 `credits_spent` / `attempts_granted` / `bonus` / 更新后的 `quota`。
- 额度逻辑：`consume` 先扣滑动窗口，窗口满则扣 `bonus_units`；失败访问会把用掉的那一单位（窗口或兑换）退回。`snapshot` 的 `remaining` = 窗口剩余 + 兑换额度。
- 配置见 `.env.example` 的 `GITHUB_ACCELERATOR_REDEEM_*` 与 `GITHUB_ACCELERATOR_BLOG_API_BASE_URL`。
- 博客侧：`POST /api/credits/accelerator-spend`（服务端到端，共享密钥验票据），需在博客后端配好 `SSO_SHARED_SECRET`。

## 数据页

`/` 是加速页，`/stats` 是数据页；两者共用同一份前端产物与同一个页头，页头里的导航标明当前所在页面。

- `GET /api/stats` 返回五个窗口的计数：`hour`（1 小时）、`day`（24 小时）、`week`（7 天）、`month`（1 个月）、`total`（全部）。每个窗口都是 `{downloads, pages, users}`：`downloads` 是文件代理的实际交付，`pages` 是镜像页面的实际交付，`users` 是窗口内去重的登录账号数。
- 记账口径与额度一致，只记**真正取走内容**的成功交付（响应 200/206）。被退还的失败请求（404、502、上游不可达）不进入统计，所以「下载次数」不会把失败也算进去。
- `usage` 表会随额度窗口推进被裁剪，装不下一个月以上的历史，因此每次成功交付会**另写一行**到只追加、不删除的 `access_log` 表。额度路径仍然是那张有界的表，统计不影响它的性能与语义。
- 报表**不包含**用户名、地址或任何单次请求明细，只有计数，因此保持公开：未登录也能看。读取统计本身也不消耗额度。
- 数据表在 320px 宽度下仍然保持真实表格结构（不折叠成块级元素，否则会丢掉行列关联），靠 14px 字号与四个窄列放下。

## 反代 GitHub 页面

除文件代理外，任意不在保留前缀下的路径都会同源镜像 `github.com`，可以直接浏览：

```
GET /1zyq1                 -> github.com/1zyq1
GET /acme/widgets          -> github.com/acme/widgets
GET /acme/widgets?tab=...  -> 查询串一并透传
```

- 只镜像 `github.com` 的公开页面，且仅接受 GET/HEAD；不代理其他站点。
- 前端把页面地址一并受理：`POST /api/resolve` 对 `github.com/{owner}` 这类页面返回 `kind: "page"` 与镜像路径，页面显示反代地址与「打开反代地址」按钮，而不是下载按钮。owner 段若是 `/api`、`/proxy`、`/assets`、`/fonts` 则拒绝，避免镜像路径与自有路由互相遮蔽。输入框同时接受主页名 / 项目简写与完整链接，简写在前端补全。`/`（SPA shell）带 `Cache-Control: no-cache`，因为 shell 里写死了带内容哈希的产物名，否则浏览器会用启发式缓存把自己钉在旧版本上。
- **只取服务端渲染的 HTML，并剥掉所有 `<script>` 与 `modulepreload`**。原因不是省流量，而是正确性：GitHub 的脚本以 `crossorigin` 模块方式加载，在这个「外来域」上会执行、无法 hydrate 服务端渲染的 DOM，然后把整份文档反复追加，页面上会堆出十几份重复内容（实测 200 KB 文档涨成 3.3 MB、17 份副本且仍在增长）。剥掉脚本后是干净的一份。CSP 因此设为 `script-src 'none'`，让「不执行脚本」成为保证而非承诺。
- 资源 URL 本身就是绝对的（`github.githubassets.com`、`avatars.githubusercontent.com`、`user-images.githubusercontent.com` 等），所以不需要改写地址，浏览器直接向 Fastly 取，镜像页面的出口流量很小。
- 客户端声明 `Accept-Encoding: gzip` 时，镜像 HTML 在服务端压缩后再下发（实测 184 KB → 27 KB，约 6.8×），并补上 `Vary: Accept-Encoding`。对上游的请求始终是 `Accept-Encoding: identity`，因为剥离脚本本身就要先拿到明文。
- 文件形状的路径会 302 转交给已有的文件代理，自动复用归档缓存与 Range 断点续传，仓库页的「Download ZIP」因此可用。
- 指向 `github.com` 的重定向会改写成同源路径，避免浏览器跳回被阻断的域名；指向其他站点的重定向原样透传。
- 请求侧的 `Cookie`、`Authorization`、客户端 IP 一律不转发；响应侧的 `Set-Cookie` 也不转发。
- 自有页面（`/`、`/assets/*`、`/fonts/*`）使用自有 CSP，除访问统计脚本外不放行任何第三方来源；镜像页面则是 `script-src 'none'`，两者互不影响。
- 自有前端的文本产物（`/assets/*` 下的 JS 与 CSS）由 Starlette `GZipMiddleware` 压缩，但中间件只挂在静态挂载点上：挂到整个应用会连二进制下载流一起压缩，纯属浪费。`/fonts/*` 不挂——woff2 本身已压缩，而 `mimetypes` 不认这个扩展名，文件会被当成 `application/octet-stream` 再压一遍。
- `/api/*` 与 `/proxy/*` 是保留前缀，其中的未知路径仍返回 JSON 错误信封，不会被转发到 GitHub。

## 配置

全部设置使用 `GITHUB_ACCELERATOR_` 前缀，样例见 `.env.example`。无需任何 token。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `STATIC_DIR` | `frontend/dist` | 静态资源目录 |
| `CONNECT_TIMEOUT_SECONDS` | `10` | 建连超时（整数秒） |
| `READ_TIMEOUT_SECONDS` | `300` | 相邻数据块的最大间隔（整数秒） |
| `POOL_TIMEOUT_SECONDS` | `10` | 连接池等待超时（整数秒） |
| `MAX_CONNECTIONS` | `100` | 上游连接上限 |
| `MAX_KEEPALIVE_CONNECTIONS` | `20` | 不得大于 `MAX_CONNECTIONS` |
| `MAX_CONCURRENT_PROXIES` | `100` | 并发下载上限 |
| `USER_AGENT` | `github-accelerator/0.1` | 出站 UA |
| `ARCHIVE_CACHE_DIR` | `.cache/archives` | 归档缓存目录，须对运行用户可写 |
| `ARCHIVE_CACHE_MAX_BYTES` | `21474836480` | 缓存总量上限（20 GiB），设为 `0` 关闭缓存 |
| `TRUSTED_HOSTS` | 空 | 逗号分隔的 Host 白名单。留空接受任意 Host；非空时由 Starlette `TrustedHostMiddleware` 在最外层拦截伪造 Host，返回 400 `Invalid host header` |
| `USAGE_LIMIT` | `10` | 每个账号在每个窗口内的访问次数上限 |
| `USAGE_WINDOW_SECONDS` | `3600` | 额度窗口长度（秒） |
| `MYSQL_HOST` | 空 | 账号库地址，**必填**；三项必填项缺一即启动报错 |
| `MYSQL_PORT` | `3306` | 账号库端口 |
| `MYSQL_USER` | 空 | 账号库用户名，**必填** |
| `MYSQL_PASSWORD` | 空 | 账号库密码 |
| `MYSQL_DATABASE` | 空 | 账号库库名，**必填** |
| `MYSQL_TABLE_PREFIX` | 空 | 表名前缀，只允许小写字母、数字与下划线；同一套库内靠它隔离测试与正式数据 |
| `MYSQL_POOL_MIN_SIZE` | `1` | 连接池下限 |
| `MYSQL_POOL_MAX_SIZE` | `10` | 连接池上限，不得小于下限 |
| `SESSION_TTL_SECONDS` | `2592000` | 登录态有效期（秒），默认 30 天 |
| `SESSION_COOKIE_SECURE` | `false` | 为 `true` 时 Cookie 只随 HTTPS 发送，部署到 HTTPS 站点时应开启 |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | 验证邮件里链接的基地址。**必须改成用户真正访问到的地址**，否则邮件里的链接只有本机能打开 |
| `VERIFICATION_TTL_SECONDS` | `86400` | 验证链接有效期（秒），默认 24 小时 |
| `VERIFICATION_RESEND_SECONDS` | `60` | 同一账号两次发信的最小间隔（秒），`0` 表示不限 |
| `SMTP_HOST` | 空 | 留空则不发信，验证链接写进服务日志；填上后启用 SMTP 发信 |
| `SMTP_PORT` | `587` | 配 `SMTP_STARTTLS=true`；改用 `465` 时须把 `SMTP_STARTTLS` 设为 `false` |
| `SMTP_USERNAME` | 空 | SMTP 登录名；QQ 邮箱填完整邮箱地址 |
| `SMTP_PASSWORD` | 空 | SMTP 密码；QQ 邮箱填「授权码」而不是登录密码 |
| `SMTP_FROM` | 空 | 发件地址，留空时回退到 `SMTP_USERNAME` |
| `SMTP_STARTTLS` | `true` | `587` 用 `true`（STARTTLS），`465` 用 `false`（隐式 TLS） |

表中是代码默认值；`.env.example` 另给了一套更保守的起步值（连接 16、keepalive 8、并发 8），适合直接复制的场景。

账号与用量在 MySQL 里，不在容器卷里，所以 `accelerator-cache` 卷只承载归档缓存。数据库账号需要有**建表权限**（首次运行会建表）；测试用 `MYSQL_TABLE_PREFIX` 指到另一套表名，因此测试与正式数据可以共用一个库而互不干扰。

## 搜索引擎与访问统计

- 自有页面（`/` 与 `/stats`）是可索引的。`index.html` 带 `rel="canonical"`、Open Graph、Twitter Card 与 `WebApplication` 结构化数据；`<noscript>` 里另有一段可被爬虫读到的正文，否则 SPA 除了首屏模板对爬虫没有可读内容。
- `/robots.txt` 与 `/sitemap.xml` 由后端生成，基地址取 `PUBLIC_BASE_URL`，所以换域名不需要改代码。`robots.txt` 禁掉 `/api/` 与 `/proxy/`（不是内容页）并指向 sitemap。`/og.png` 是社交预览图，构建产物里没有就返回 404 信封。
- **镜像页面一律带 `X-Robots-Tag: noindex, nofollow`**。镜像内容与 GitHub 原文逐字相同，若允许收录，本站会与原文争夺同一批查询，两边都受损。
- 访问统计用 Umami：自有页面加载 `cloud.umami.is` 的脚本并上报到 `gateway.umami.is`，这两个来源在自有 CSP 的 `script-src` 与 `connect-src` 里被显式列出；镜像页面仍是 `script-src 'none'`，统计只在自有 shell 上运行。注意 `Referrer-Policy: no-referrer` 与统计并存，因此报表里没有来源页，这是有意的隐私取舍。
- `index.html` 里的 canonical 与 `og:*` 地址写死为 `https://github.1zyq1.com/`。**换域名时这里必须和 `PUBLIC_BASE_URL` 一起改**：`spa_shell` 用 `FileResponse` 直接下发产物，没有做变量注入，理由是保留 ETag/304 这条廉价路径。

## 部署

先把 `.env.example` 复制成 `.env`（该文件已被 `.gitignore` 与 `.dockerignore` 排除，不会进仓库也不会进镜像），至少改掉 `PUBLIC_BASE_URL`，需要发信时再补 `SMTP_*`。配置只有一个点：直接用 `uvicorn` 时 pydantic-settings 自动读取项目根目录的 `.env`，用 `docker compose` 时 `compose.yaml` 通过 `${...}` 插值读同一份文件并注入容器（缺省值保证未配置时仍能启动，只是不发信）。真实环境变量的优先级高于 `.env`。

```powershell
docker compose up --build
```

镜像分三阶段构建：Node 构建前端、Python 装依赖、最终镜像以非 root 用户（uid 1001）运行。compose 开启只读根文件系统、丢弃全部 capability、`no-new-privileges`，健康检查不依赖 curl。

归档缓存需要可写空间，compose 已用命名卷挂到 `/app/.cache`；镜像里该目录预先以 1001 建立，所以卷会继承正确属主。直接跑 `uvicorn` 时，把 `ARCHIVE_CACHE_DIR` 指到一个存在的可写目录即可，否则缓存会写入失败（响应仍正常，但每次都回源）。

## 归档缓存

源码归档由 `codeload.github.com` 按需生成，且**按来源 IP 限流**。单机代理意味着所有用户共用一个出口 IP，热门归档被反复请求时会被限流（429/403），随后升级为静默挂起。缓存是唯一有效的对策。

- 只缓存**归档**。归档在 tag/commit 定死之后内容不变，缓存安全。raw 指向分支头、内容会变，因此不缓存；Release 资产走 CDN，也不缓存。
- 首次请求边回源边落盘，写临时文件后**原子改名**，因此不会出现半截文件。
- 同一 URL 的并发请求**合并成一次回源**，避免几十个用户同时打 codeload。
- 命中后直接由 `FileResponse` 从磁盘下发，**Range 断点续传、HEAD、If-Range、416 全部原生支持**，比回源更快。
- **LRU 淘汰**：超过 `ARCHIVE_CACHE_MAX_BYTES` 时按最近使用时间删除最旧的条目；单文件大于上限则不落盘。崩溃遗留的 `.part` 临时文件超过一小时会被清理。
- 磁盘写失败（如目录不可写、磁盘满）不会破坏响应，只是本次不缓存。

清空缓存直接删目录内容即可：

```powershell
Remove-Item -Recurse -Force .cache/archives/*
```

## 安全边界

- 只接受 HTTPS/443；拒绝 userinfo、IP 字面量、非标端口、控制字符、反斜杠、点路径段、畸形百分号编码。
- 重定向逐跳重新校验，最多 5 跳。
- 只转发 `Range`、`If-Range`、`If-None-Match`、`If-Modified-Since`；不转发 Cookie、Authorization、客户端 IP、逐跳首部。
- 出站 `trust_env=False`，环境里的代理变量无法劫持流量。
- 响应带 CSP、`nosniff`、`no-referrer`、权限策略、`frame-ancestors 'none'`、`frame` DENY。
- `POST /api/resolve` 有 8192 字节请求体上限。
- Host 白名单是**可选**的：`TRUSTED_HOSTS` 留空时接受任意 Host 首部，暴露到公网前应当显式设置。非空时校验发生在最外层中间件，先于任何路由与请求体限制。
- 上游 403/429 映射为 `upstream_rate_limited` 503 并原样透传 `Retry-After`（仅接受纯数字秒数，HTTP-date 形式丢弃），不会退化成含糊的 502。
- 账号门禁在最外层中间件里完成：先解析会话、再判权限、最后计量，因此未授权的请求根本到不了上游。
- 登录不区分「用户不存在」与「密码错误」，统一返回 401 `invalid_credentials`；注册会如实区分 `username_taken`（409）与 `email_taken`（409）。也就是说，注册接口可以拿来确认某个用户名或邮箱是否已被占用——这是「注册时立刻告知冲突」换来的代价，重发接口的 60 秒冷却只能限制邮件频率，不能消除这一点。
- 验证邮件只带一个随机 token（库里存 SHA-256），链接本身不含用户名或密码；链接默认 24 小时有效，同一账号同时只有一封邮件有效，验证成功后链接立即作废。

## 验证结果

后端：`ruff check`、`ruff format --check`、`basedpyright`（`typeCheckingMode = "all"`）全绿；**357 个测试通过，覆盖率 93.29%**。

前端：`vue-tsc` + `tsc` 类型检查、`biome check`（25 个文件）、**159 个 Vitest 测试**全绿；产物 CSS 115.41 kB、JS 91.13 kB，实际下发 gzip 后为 38.21 kB / 34.06 kB。

端到端（真实 `uvicorn` + 56 项检查）：注册与重复注册 409、额度从 10 次用尽到第 11 次 429（带 `Retry-After`）、额度用尽时浏览器下载被 302 到 `/?limited=1`、`HEAD` 与 `resolve` 不计费、上游 404 后额度退还、登出再登录后额度保持不变。数据页另跑 23 项：报表初始全零、一次成功下载在五个窗口都记为 1 次并计入 1 个用户、反复读取报表不扣额度、失败的请求不入统计、`/stats` 与 `/api/stats` 匿名可读、`github.com/stats/widgets` 仍被拒为 400。邮箱验证另跑 43 项：注册不下发 Cookie、未验证账号登录同样不设 Cookie、链接验证成功后才能登录并正常计费、同一 token 二次使用被拒（浏览器被 303 到 `/?verify_failed=1`）、已确认账号再申请链接返回 409 `already_verified`、重复地址注册 409 `email_taken` 且失败注册会释放用户名、重发冷却返回 429 `verification_throttled` 与 `Retry-After`、老库（无 email 列）自动补列并允许补填地址后登录。

真实浏览器（headless Edge + Playwright）：1280/768/375/320 视口及 200% 缩放等效宽度下均无横向滚动；命中区域全部 ≥44×44；文字对比度达 WCAG AA；无遮挡裁切；键盘可达输入框与按钮并有 2px 焦点环；无任何外部请求；实测 LCP 16ms、CLS 0、INP 0。登录面板与账号条沿用同一套 token 与类，复测 27 项：匿名访客只看到登录面板、方向键可切换登录/注册、注册后出现账号条、额度耗尽后页面提示并禁用生成按钮、320/375 视口无横向滚动、控制台零错误。数据页复测 28 项：五行按服务端顺序渲染、数字与 `/api/stats` 逐格一致、表格保持真实单元格（不折叠成块级元素）、320px 下四列仍在视口内、导航用 `aria-current` 标明当前页、链接命中高度 ≥44px、控制台零错误。邮箱验证流程再复测 23 项：登录页不要地址、切到注册才出现地址框、地址不合法时本地拦截且不发请求、注册后进入待验证界面并显示收件地址、邮件链接回到站点后提示验证成功、确认登录后账号条出现并显示「剩余 10 / 10 次」、320px 待验证界面无横向滚动、命中区域全部达标、控制台零错误。

端到端对真实 GitHub 验证：四类 URL 解析后 token 均可还原原地址，Release、Raw、Blob 代理返回 200/206（21 MB 资产与 Raw 内容均正确），五类非法输入返回 400。页面类（`kind: "page"`）已在部署环境验证：镜像页返回 200 与约 200 KB 的干净 HTML。

## 已知限制

- **归档在本次验证环境下不可用**：`github.com` 在本机解析到 `20.205.243.166` 且直连 5 秒超时（DNS 六次一致），而 `codeload.github.com`、`raw.githubusercontent.com`、`release-assets.githubusercontent.com` 均 0.1 秒连通。归档请求因此在首跳超时并返回 504。阻断来自本机到 `github.com` 的网络，而不是链路本身：本机直连 `codeload.github.com` 返回 `200 application/zip` 且字节以 `PK\x03\x04` 开头；部署环境（能访问 `github.com`）上归档 `HEAD` 同样返回 `200 application/zip`、`GET` 返回以 `PK` 开头的 zip 字节。
- **容器未实机验证**：本机没有 Docker CLI（`docker` 与 `docker compose` 均为 CommandNotFoundException），`Dockerfile` 与 `compose.yaml` 只做过静态核对，未执行 `docker build` / `compose up`。
- 8080/8000 端口被其他进程占用时，改用其他端口启动。

设计规范见 [DESIGN.md](DESIGN.md)：浅色单一 azure 主色、仅靠色阶拉开层次、无阴影无渐变无毛玻璃、无虚假指标。
