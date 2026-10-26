# 虚拟交易 · 模拟市场

一个用于课程展示的模拟炒股与风险教育网站。系统使用虚构行情和虚拟资金，让学生观察冲动交易、频繁买卖和亏损后加码的风险。没有真实证券行情、充值入口、现金兑换或真实交易。

## 本地 SQLite 启动

本地 Python 网站使用 SQLite，数据库是项目目录里的 `.db` 文件。Python 自带 `sqlite3`，不需要安装或启动 MySQL，也不需要额外安装 Python 包。

```powershell
py server.py
```

浏览器打开 <http://127.0.0.1:8000>。首次启动会自动建表；注册的新账户获得 ¥100,000 虚拟资金和 2,000 玮元体验余额。

- 数据库文件由 `.env` 中的 `SQLITE_PATH` 指定，当前为 `market_sim_from_mysql.db`（从旧 MySQL 迁移而来）。
- 默认只监听 `127.0.0.1`，同一局域网的其他人无法访问。确需局域网演示时可设置 `HOST=0.0.0.0`，但数据库文件里包含真实邮箱和密码哈希，请只在可信网络使用。
- 端口可用 `PORT` 修改，例如 `$env:PORT=8080; py server.py`。
- 旧 MySQL 数据库没有被删除；确认 SQLite 网站数据正常后，可自行决定是否保留 MySQL 作为备份。

## 静态文件白名单

本地服务只把 13 个前端文件当作静态资源：`index.html`、`app.js`、`style.css`、`recharge.css`、`fishing.html`、`fishing.js`、`fishing.css`、`mining.html`、`mining.js`、`mining.css`、`forge.html`、`forge.js`、`forge.css`。

`.env`、`server.py`、`*.db`、`.git/`、`.wrangler/`、`migrations/` 等路径一律返回 404，避免浏览器把本地数据库或源码下载走。404 页面会显示请求路径，方便判断地址是否写错；`/index`、`/index.htm` 和多余斜杠会自动跳转到首页。README、使用说明书等文件请在本机文件管理器中打开。Cloudflare Pages 端由 `.assetsignore` 承担同样的职责。

## 数据库内容

- `Users`：用户名、邮箱、加盐密码摘要、注册时间、管理员标记、拉黑状态与最近在线时间。
- `Sessions`：登录会话，保存随机令牌和 7 天有效期。
- `Stocks`：虚构股票标的和模拟价格，`is_custom` 标记管理员新增的标的。
- `Accounts`：每位用户的虚拟现金余额。
- `Holdings`：持仓数量和加权平均成本。
- `Orders`：模拟买卖方向、价格、数量与时间。
- `Wallets` / `PointBalances` / `Memberships`：玮元、积分、会员到期时间。
- `RechargeOrders`：积分 / 会员模拟购买记录。
- `WalletTopupClaims`：每日一次的虚拟玮元补给领取记录。
- `MarketTicks`：模拟行情历史，启动和每次刷新后只保留每只股票最近 300 条。
- `FishingDaily`：钓鱼练习次数、当日收益和“本轮是否开始”的标记。
- `MiningRuns` / `ForgeRuns`：当前或历史的挖矿、锻剑回合（矿袋、锤数、状态）。
- `GameStats`：每种小游戏的累计次数与累计收益。
- `StockSwaps`：用户之间的股票互换请求、数量、留言、状态和处理时间。
- `EmailCodes`：注册邮箱验证码的哈希、有效期、尝试次数与使用状态。

## 账号与会话

- 密码使用 PBKDF2-SHA256、10 万次迭代和 16 字节随机盐，存储格式为 `pbkdf2_sha256$迭代次数$盐$摘要`。
- 旧的 `<盐>$<摘要>` 格式仍可登录：本地会尝试 10 万和 12 万次两种迭代，因此本地 SQLite 与 Cloudflare D1 的用户数据可以互相迁移。
- 会话保存在 `Sessions` 表中，有效期 7 天；重启本地服务不会让用户掉线，退出登录会立即删除会话。
- 新注册账户需要邮箱验证码；已有账户登录不受影响。
- 本地版对同一 IP 10 分钟内登录失败 10 次会临时锁定，返回“尝试次数过多”；Cloudflare 版由平台侧防护承担，未内置该计数。

## 注册邮箱验证码（Resend）

新注册账户需要邮箱验证码。项目使用 Resend 免费额度（约 3,000 封/月），验证码 6 位、5 分钟有效、60 秒内不可重发、最多尝试 5 次；数据库只保存 HMAC 哈希，不保存明文。

本地 `.env` 配置：

```text
RESEND_API_KEY=re_xxxxxxxx
MAIL_FROM=虚拟交易 <no-reply@virtualtrader123.xyz>
EMAIL_CODE_SECRET=随机长字符串
EMAIL_DRY_RUN=0
```

- `EMAIL_DRY_RUN=1` 时不真正发信：验证码只打印在终端并自动填入注册框，适合还没配置 API Key 时测试。
- `EMAIL_CODE_SECRET` 可用 `python -c "import secrets;print(secrets.token_hex(32))"` 生成。
- `.env` 已被 Git 忽略；不要把 API Key 写进代码或提交到仓库。

Cloudflare 版把密钥存为 Secret：

```powershell
npx wrangler pages secret put RESEND_API_KEY
npx wrangler pages secret put EMAIL_CODE_SECRET
```

`MAIL_FROM` 可写在 `wrangler.toml` 的 `[vars]` 中；线上不要设置 `EMAIL_DRY_RUN`。

Resend 域名验证：在 Resend 控制台添加 `virtualtrader123.xyz`，把给出的 SPF / DKIM / MX 记录添加到阿里云云解析，验证通过后再发信。QQ、163 邮箱可能把海外共享 IP 的邮件放进垃圾箱，注册页会提示用户检查垃圾箱。

## 玮元积分与会员模拟中心

登录后点击右上方“玮元积分中心”可打开体验页。“玮元补给”每天可免费领取 500 玮元虚拟余额；“积分兑换”和“会员兑换”会分别扣除虚拟玮元并保存记录。兑换比例固定为 **1 玮元 = 10 积分 = 100 虚拟余额**；没有真实支付、提现或兑换现金功能。会员和积分只用于演示，不影响模拟股票交易。新账户初始获得 2,000 玮元体验余额；已有账户的钱包余额不会被重置或补发。

- `Wallets`：每位用户的玮元余额。
- `PointBalances`：积分余额。
- `Memberships`：会员到期时间。
- `RechargeOrders`：积分 / 会员模拟购买记录。
- `WalletTopupClaims`：每日一次的虚拟玮元补给领取记录。

## 破产恢复练习：钓鱼

从主页入口进入 `fishing.html`。模拟现金低于 ¥10,000 时开放，练习次数不限。

- 每轮必须先点击“开始钓鱼”，服务端把当天练习次数 +1 并记录开始时间；没有开始的结算请求会被拒绝。
- 按住“拉竿”控制绿色区域，让小鱼留在区域内以填充专注进度。
- **只有专注进度填满 100% 才算钓到鱼并获得虚拟资金**；进度一旦退回零，本轮立即结束，没有鱼也没有奖励（练习次数仍会计入）。
- 结算分数由前端上报，服务端按本轮已用时间校验（进度最快每秒上涨 10%）；未填满 100% 的结算一律不发奖励。
- 可钓到溪流鲦、银鳞鲑、锦鲤、金色锦鲤和稀有钻石鱼王，奖励为虚拟资金（常见鱼 ¥1,000，金色锦鲤 ¥2,000，钻石鱼王 ¥5,000），不影响玮元余额。

## 矿场挖掘

从主页进入 `mining.html`，同样只对余额低于 ¥10,000 的账户开放。

- 每轮先点“开始挖矿”，然后按住面前的方块约 1.6 秒；松手会重置当前这一块的进度。
- 按住时方块外围的环形进度会像技能冷却一样转一圈，挖开后显示泥土、煤、铁、金、钻石或炸弹，结果由服务端随机决定。
- 泥土 +¥10–30（约 50%），煤 +¥40–90（约 20%），铁 +¥90–180（约 15%），金 +¥200–600（约 8%），钻石 +¥600–1200（约 2%），炸弹 5%。
- 挖到炸弹时，炸弹会从泥土下方露出来并触发爆炸，本轮矿袋清零；已经离开矿场的收益不受影响。
- 随时可以点“带着收益离开”把当前矿袋结算到虚拟资金；不离开就继续承担炸弹风险。
- 页面显示已挖矿次数、累计收益和当前矿袋；结算后累计收益才增加。

## 锻剑工坊

从主页进入 `forge.html`，同样只对余额低于 ¥10,000 的账户开放。

- 每轮先点“开始锻剑”，之后每敲一锤，剑的价值 +¥25–60。
- 剑从「平凡的剑」开始；达到 11 / 26 / 46 / 71 锤时依次提升为「优秀的剑」「精湛的剑」「史诗的剑」「传奇的剑」，每次提升时当前价值翻倍。
- 碎裂概率从 1% 起步，随锤数上升，前期较慢、后期加快；到 70 锤时约 60%，风险条会同步上涨。
- 随时可以“出售这把剑”把当前价值结算到虚拟资金，见好就收也是一种选择。
- 页面显示剑名、品质、锤数、当前剑价和碎裂风险；出售后累计收益才增加。

## 时间与金额

- 新写入的时间统一保存为 UTC，接口返回带 `Z` 的 ISO 8601 字符串，由浏览器按本地时区显示；旧数据中原有的时间值保持原样。
- “每日”类限制（补给、钓鱼）统一按 UTC+8 的日期计算，避免凌晨重置时间错位。
- 持仓市值、盈亏和成交金额统一保留两位小数。

## 管理员股票管理

管理员添加股票时会检查代码和名称是否重复（名称不区分大小写），并拒绝包含 HTML 尖括号或控制字符的名称 / 类别。管理员可在模拟市场股票列表中删除自定义股票，系统预置股票不显示删除按钮；已有订单或正数持仓的自定义股票不能删除。

用 DB Browser 打开当前 SQLite 文件，在 Execute SQL 中执行下面语句，把用户名替换成需要授权的已注册账号：

```sql
UPDATE Users SET is_admin = 1 WHERE username = '你的管理员用户名';
```

该账号退出并重新登录后，主页顶栏会显示“股票管理”。SQLite 启动时会自动补齐 `is_admin`、`is_custom`、`FishingDaily.pending`、`FishingDaily.started_at` 等字段。

Cloudflare D1 需依次应用 `migrations/0001` 至 `migrations/0011`；授权管理员可在 D1 控制台执行同样的 `UPDATE Users SET is_admin=1 WHERE username='...'`。

## 管理员用户与互换管理

管理员登录后可以打开“用户管理”，查看注册用户、邮箱、注册时间、在线状态和拉黑状态，并可以踢出会话或拉黑/解除拉黑账号。被拉黑的账号无法登录，已有会话会立即失效。

同一面板还会列出所有用户发起的股票互换记录，包括双方用户名、股票名称、数量、留言、状态和时间。

## 积分兑换虚拟余额

在“玮元积分中心”中，用户可以把积分按 **1 积分 = ¥10 虚拟余额** 兑换成虚拟现金；完整比例为 **1 玮元 = 10 积分 = 100 虚拟余额**。会员有效期内兑换额外增加 **10%** 余额（即 1 积分 = ¥11）。兑换会扣减积分、增加现金，并写入一条模拟记录。

## 用户股票互换

首页“股票互换”允许用户指定对方用户名，用自己持有的股票交换对方的另一只股票；双方数量都由发起者填写。对方可以接受或拒绝，发起者可以取消。接受时系统会校验双方持仓并自动转移，管理员可以在用户管理面板看到全部交易内容。

## 安全提示

- `.env` 和本机 `.db` 文件已加入 Git 忽略列表，也不会被本地服务或 Pages 直传对外提供。
- 数据库文件包含真实邮箱、密码哈希和交易记录，不要提交到 GitHub、不要发给别人、不要放进截图。
- 本地服务响应带 `Content-Security-Policy`、`X-Content-Type-Options`、`Referrer-Policy`、`X-Frame-Options` 等安全头；Cloudflare Pages 通过根目录 `_headers` 应用同样的策略，API 响应由 Functions 自行添加。
- 不要把数据库密码、Cloudflare API Token 写入 `server.py`、`wrangler.toml` 或仓库。

## Cloudflare 免费部署（Pages + Functions + D1）

项目已经包含 `functions/api/[[path]].js` 和 `migrations/`，可以把静态前端和 API 部署到 Cloudflare Pages，并把部署版数据放入 Cloudflare D1。Cloudflare 版和本地 Python + SQLite 版是两套独立数据库，数据不会自动同步。

推荐通过 GitHub 仓库连接 Cloudflare Pages。Pages Functions 需要 Git 集成或 Wrangler 部署，控制台直接上传静态文件不适用于本项目的 API Functions。

大致步骤：

1. 在 Cloudflare 控制台创建免费的 D1 数据库，名字填写 `qinxing-market`。
2. 记录数据库 ID，确认与 `wrangler.toml` 中的 `database_id` 一致。
3. 安装 Node.js 与 Wrangler，并在项目目录登录 Cloudflare：`npx wrangler login`。
4. 初始化 D1：`npx wrangler d1 migrations apply qinxing-market --remote`。
5. 将项目提交到 GitHub。在 Cloudflare Pages 创建项目并连接仓库，框架选择 None，构建命令留空，输出目录填写 `.`。
6. 部署完成后用 Pages 给出的 `https://...pages.dev` 地址注册测试。所有 Cloudflare 版数据将保存在 D1。

`wrangler.toml` 的 `name` 是 Pages 项目名，必须与 Cloudflare 控制台中的项目名一致（当前为 `virtual-trader`）。

Cloudflare 免费套餐的 D1 有每日读取、写入量和总存储限额，课程展示规模一般足够；规则可能调整，部署前请查看官方 [Workers 计划](https://developers.cloudflare.com/workers/platform/pricing/)和 [D1 价格与配额](https://developers.cloudflare.com/d1/platform/pricing/)。Functions 绑定与部署说明见 [Pages Functions](https://developers.cloudflare.com/pages/functions/)。

注意：本机 Python 服务使用 `.env` 中 `SQLITE_PATH` 指定的 SQLite 文件；线上 Pages 版本由 D1 绑定提供数据库。

## 本次更新摘要

- 本地服务改为静态白名单，修复数据库、`.env`、`.git` 可被直接下载的问题；默认只监听本机。
- 会话改为数据库存储，重启不掉线；密码格式带迭代次数，本地与 D1 可互相迁移。
- 注册增加邮箱验证码（Resend），新增 `EmailCodes` 表与 `0008` 迁移；本地没有 API Key 时可用 `EMAIL_DRY_RUN=1` 测试。
- 钓鱼必须“开始本轮”才能结算；只有进度填满 100% 才发奖励，中途失败没有鱼也没有虚拟资金。
- 农场小游戏下线，改为矿场挖掘与锻剑工坊；两者都有服务端回合、统计和余额门槛。
- 钓鱼、挖矿、锻剑入口都显示累计次数与累计收益（`GameStats`）。
- 锻剑增加剑名与五档品质：11 / 26 / 46 / 71 锤升级，当前价值翻倍；碎裂风险改为从 1% 逐步上升，70 锤约 60%。
- 新增管理员用户管理、踢出/拉黑、在线心跳和用户股票互换；积分可按 1:10 兑换虚拟余额。
- 新增 `0011_admin_points_swaps.sql` 迁移，包含封禁/在线字段、积分兑换金额和互换表。
- 行情历史自动裁剪到每只股票 300 条；金额统一两位小数。
- 时间统一 UTC + 浏览器本地显示；每日限制统一按 UTC+8 计算。
- 增加登录限流、管理员输入清洗、JSON 错误兜底和安全响应头。
- 补齐 `.assetsignore`、`_headers`、`0006`、`0007`、`0008`、`0009`、`0010` 迁移，并更新本文件与《使用说明书》。

部署与 GitHub 操作的完整步骤见 [使用说明书.md](使用说明书.md)。
