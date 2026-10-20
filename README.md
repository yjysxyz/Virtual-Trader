# 清醒投资 · 模拟市场

一个用于课程展示的模拟炒股与风险教育网站。系统使用虚构行情和虚拟资金，让学生观察冲动交易、频繁买卖和亏损后加码的风险。没有真实证券行情、充值入口、现金兑换或真实交易。

## 连接 MySQL 并启动

1. 确认 MySQL 服务已启动，在 MySQL Workbench 或命令行执行 `init.sql`，创建 `market_sim` 数据库。
2. 将 `.env.example` 复制为 `.env`，填写本机 MySQL 主机、端口、用户名和密码。`.env` 已加入 Git 忽略列表，不会被提交。
3. 在项目目录安装 Python 依赖并启动：

   ```powershell
   py -m pip install -r requirements.txt
   py server.py
   ```

4. 浏览器打开 <http://localhost:8000>。首次启动时会自动创建 `Users`、`Stocks`、`Accounts`、`Holdings`、`Orders` 五张表。注册的新账户会获得 ¥100,000 虚拟资金。

## 数据库内容

- `Users`：用户名、邮箱、加盐密码摘要、注册时间。
- `Stocks`：虚构股票标的和模拟价格。
- `Accounts`：每位用户的虚拟现金余额。
- `Holdings`：持仓数量和加权平均成本。
- `Orders`：模拟买卖方向、价格、数量与时间。

## 玮元积分与会员模拟中心

登录后点击右上方“玮元充值”可打开体验中心。新账户获得 2,000 玮元虚拟余额，可以购买模拟积分套餐或体验会员时长；购买后会扣除玮元并保存模拟记录。没有真实支付、提现、兑换现金功能。会员和积分只用于演示，不影响模拟股票交易。

- `Wallets`：每位用户的玮元余额。
- `PointBalances`：积分余额。
- `Memberships`：会员到期时间。
- `RechargeOrders`：积分 / 会员模拟购买记录。

旧账户首次运行新版项目时也会获得一次 2,000 玮元体验余额；已有余额不会被重置。

网站的模拟股票交易功能保持不变：用户注册登录后可模拟买卖、查看持仓与盈亏、查询交易记录并重置账户。行情为虚构演示数据。

## 安全提示

不要把 MySQL 密码写入 `server.py` 或提交到 GitHub。只把本机连接配置填写到 `.env`。如果 MySQL 用户没有建库权限，请用有权限的账号先执行 `init.sql`。

## 管理员股票管理

管理员添加股票时，系统会同时检查股票代码和股票名称是否重复（名称不区分大小写）。管理员可在模拟市场股票列表中删除自定义股票；系统预置股票不会显示删除按钮。为保留交易历史，已有订单或正数持仓的自定义股票不能删除。MySQL 版首次启动会自动添加 `Stocks.is_custom` 字段并标记旧的自定义股票；Cloudflare D1 需依次应用 `migrations/0002_add_admin.sql` 和 `migrations/0003_custom_stocks.sql`。


## 本次新增

- 模拟行情折线图：每次模拟行情刷新会将价格时点写入 `MarketTicks`，图表展示最近最多 80 个时点。旧版数据库会在启动时自动补表和首个历史点。
- 破产恢复钓鱼练习：从主页入口跳转到独立 `fishing.html` 游戏页面；模拟现金低于 ¥10,000 时开放，练习次数不限。原创像素风水域场景中，玩家按住拉竿控制绿色区域、让小鱼留在区域内以填充专注进度；按完成度奖励 50–1,000 元虚拟资金，不影响玮元余额。该玩法仅为技能小游戏，不是随机抽奖或现实获利途径。
- 数据库访问仍通过项目自定义 `Database` 类封装，负责连接、参数化执行和提交事务。

## Cloudflare 免费部署（Pages + Functions + D1）

项目已经包含 `functions/api/[[path]].js` 和 `migrations/0001_initial.sql`，可以把静态前端和 API 部署到 Cloudflare Pages，并把部署版数据放入 Cloudflare D1。Cloudflare 版和本地 Python + MySQL 版是两套运行后端：部署版不会连接你电脑上的 MySQL，也不会自动复制 MySQL 里的用户数据。

推荐通过 GitHub 仓库连接 Cloudflare Pages。Pages Functions 需要 Git 集成或 Wrangler 部署，控制台直接上传静态文件不适用于本项目的 API Functions。

大致步骤：

1. 在 Cloudflare 控制台创建免费的 D1 数据库，名字填写 `qinxing-market`。
2. 记录数据库 ID，把 `wrangler.toml` 中 `database_id` 的提示文字替换为实际 ID。
3. 安装 Node.js 与 Wrangler，并在项目目录登录 Cloudflare：`npx wrangler login`。
4. 初始化 D1：`npx wrangler d1 migrations apply qinxing-market --remote`。
5. 将项目提交到 GitHub。在 Cloudflare Pages 创建项目并连接仓库，框架选择 None，构建命令留空，输出目录填写 `.`。
6. 部署完成后用 Pages 给出的 `https://...pages.dev` 地址注册测试。所有 Cloudflare 版数据将保存在 D1。

Cloudflare 免费套餐的 D1 有每日读取、写入量和总存储限额，课程展示规模一般足够；规则可能调整，部署前请查看官方[Workers Paid / Free 计划](https://developers.cloudflare.com/workers/platform/pricing/)和[D1 价格与配额](https://developers.cloudflare.com/d1/platform/pricing/)。Functions 绑定与部署说明见[Pages Functions](https://developers.cloudflare.com/pages/functions/)。

注意：本机 Python 服务仍然使用 `.env` 中的 MySQL 配置；线上 Pages 版本由 D1 绑定提供数据库。不要把 `.env` 或真实数据库密码提交到 GitHub。


## 管理员添加模拟股票

用户表新增 `is_admin` 管理员标记。服务端启动时会为旧 MySQL 数据库补充该字段；新注册用户默认不是管理员。先在 Navicat 用管理员连接执行下面的 SQL，把用户名替换成需要授权的已注册账号：

```sql
UPDATE Users SET is_admin = 1 WHERE username = '你的管理员用户名';
```

该账号退出并重新登录后，主页顶栏会显示“股票管理”。管理员填写股票代码、名称、初始模拟价格和类别即可添加标的。股票代码不能重复；新增股票会立即写入行情历史，并和其他标的一样参与每 30 秒一次的随机涨跌。该功能仅创建虚构模拟标的，不接入真实行情。

Cloudflare D1 首次部署需应用新增的 `0002_add_admin.sql` 迁移；授权管理员可在 D1 控制台执行同样的 `UPDATE Users SET is_admin=1 WHERE username='...'`。
