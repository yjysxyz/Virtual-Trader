// 虚拟交易 · 模拟市场 —— Cloudflare Pages Functions + D1 后端。
// 与本地 server.py 保持同一套接口、鉴权规则和数据结构；所有金额均为虚拟演示数据。

const SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "X-Frame-Options": "DENY",
  "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
  "Content-Security-Policy":
    "default-src 'self'; img-src 'self' data:; " +
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; " +
    "font-src 'self' https://fonts.gstatic.com; script-src 'self'; " +
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
};

const json = (data, status = 200, headers = {}) =>
  Response.json(data, {
    status,
    headers: { ...SECURITY_HEADERS, "Cache-Control": "no-store", ...headers },
  });
const bad = (message, status = 400) => json({ error: message }, status);
const money = (value) => Math.round(Number(value || 0) * 100) / 100;

// 与本地版一致：每日限制使用 UTC+8 的日期，避免凌晨重置时间错位。
const SHANGHAI_OFFSET = 8 * 3600 * 1000;
const todayLocal = () => new Date(Date.now() + SHANGHAI_OFFSET).toISOString().slice(0, 10);
const isoZ = (value) => {
  if (value === null || value === undefined) return value ?? null;
  const text = String(value).trim().replace(" ", "T");
  if (!text) return text;
  return /Z$|[+-]\d{2}:\d{2}$/.test(text) ? text : `${text}Z`;
};
const parseUtc = (value) => {
  const text = String(value).trim().replace(" ", "T");
  return Date.parse(/Z$|[+-]\d{2}:\d{2}$/.test(text) ? text : `${text}Z`);
};
const cleanText = (value, minimum = 1, maximum = 100) => {
  const text = String(value ?? "").trim();
  if (text.length < minimum || text.length > maximum) return null;
  if (/[<>\x00-\x1f\x7f]/.test(text)) return null;
  return text;
};
const forgeGrade = (hits) => {
  let tier = 0;
  for (const threshold of FORGE_GRADE_THRESHOLDS) {
    if (Number(hits || 0) >= threshold) tier += 1;
    else break;
  }
  return { tier, name: FORGE_GRADES[tier] };
};
const forgeRisk = (hits) => {
  const progress = Math.min(1, Math.max(0, Number(hits || 0)) / FORGE_RISK_TARGET_HITS);
  return FORGE_BASE_SHATTER + (FORGE_MAX_SHATTER - FORGE_BASE_SHATTER) * progress * progress;
};
const isAdmin = async (env, uid) => {
  const row = await env.DB.prepare("SELECT is_admin FROM Users WHERE id=?").bind(uid).first();
  return Boolean(row?.is_admin);
};
const isOnline = (lastSeen) => {
  if (!lastSeen) return false;
  return (Date.now() - parseUtc(lastSeen)) / 1000 <= ONLINE_WINDOW_SECONDS;
};
async function holdingMoveStatements(env, fromUid, toUid, code, quantity, price) {
  const source = await env.DB.prepare("SELECT quantity FROM Holdings WHERE user_id=? AND code=?")
    .bind(fromUid, code)
    .first();
  if (!source || Number(source.quantity) < quantity) return null;
  const statements = [];
  const remaining = Number(source.quantity) - quantity;
  if (remaining === 0) {
    statements.push(env.DB.prepare("DELETE FROM Holdings WHERE user_id=? AND code=?").bind(fromUid, code));
  } else {
    statements.push(
      env.DB.prepare("UPDATE Holdings SET quantity=? WHERE user_id=? AND code=?").bind(remaining, fromUid, code)
    );
  }
  const target = await env.DB.prepare("SELECT quantity,avg_cost FROM Holdings WHERE user_id=? AND code=?")
    .bind(toUid, code)
    .first();
  if (target) {
    const newQuantity = Number(target.quantity) + quantity;
    const newCost = (Number(target.avg_cost) * Number(target.quantity) + price * quantity) / newQuantity;
    statements.push(
      env.DB.prepare("UPDATE Holdings SET quantity=?,avg_cost=? WHERE user_id=? AND code=?")
        .bind(newQuantity, newCost, toUid, code)
    );
  } else {
    statements.push(
      env.DB.prepare("INSERT INTO Holdings(user_id,code,quantity,avg_cost) VALUES(?,?,?,?)")
        .bind(toUid, code, quantity, price)
    );
  }
  return statements;
}

const DEFAULT_CASH = 100000;
const DEFAULT_WEI = 2000;
const FISHING_UNLOCK_CASH = 10000;
const GAME_UNLOCK_CASH = FISHING_UNLOCK_CASH;
const MINING_BOMB_CHANCE = 0.05;
const MINING_DIAMOND_CHANCE = 0.02;
const MINING_GOLD_CHANCE = 0.08;
const MINING_IRON_CHANCE = 0.15;
const MINING_COAL_CHANCE = 0.2;
const MINING_SOIL_REWARD = [10, 30];
const MINING_COAL_REWARD = [40, 90];
const MINING_IRON_REWARD = [90, 180];
const MINING_GOLD_REWARD = [200, 600];
const MINING_DIAMOND_REWARD = [600, 1200];
const FORGE_BASE_SHATTER = 0.01;
const FORGE_MAX_SHATTER = 0.6;
const FORGE_RISK_TARGET_HITS = 70;
const FORGE_GRADES = ["平凡的剑", "优秀的剑", "精湛的剑", "史诗的剑", "传奇的剑"];
const FORGE_GRADE_THRESHOLDS = [11, 26, 46, 71];
const FORGE_HIT_REWARD = [25, 60];
const POINT_CASH_RATE = 10;
const ONLINE_WINDOW_SECONDS = 120;
const TICKS_KEPT = 300;
const SESSION_DAYS = 7;
const PBKDF2_ITERATIONS = 100000; // Workers 的 Web Crypto 上限
const HASH_PREFIX = "pbkdf2_sha256";

const USERNAME_RE = /^[^\s<>@]{2,32}$/;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const hex = (bytes) => Array.from(bytes, (x) => x.toString(16).padStart(2, "0")).join("");

async function passwordHash(password, salt = crypto.getRandomValues(new Uint8Array(16)), iterations = PBKDF2_ITERATIONS) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", salt, iterations, hash: "SHA-256" }, key, 256);
  return `${HASH_PREFIX}$${iterations}$${hex(salt)}$${hex(new Uint8Array(bits))}`;
}

// 兼容三种格式：新格式（带迭代次数）、旧版本地 120000 次、旧版 D1 100000 次。
async function checkPassword(password, stored) {
  const parts = String(stored || "").split("$");
  let iterations = null;
  let saltHex;
  let expected;
  if (parts.length === 4 && parts[0] === HASH_PREFIX) {
    iterations = Number(parts[1]);
    if (!Number.isInteger(iterations) || iterations <= 0) return false;
    saltHex = parts[2];
    expected = parts[3];
  } else if (parts.length === 2) {
    saltHex = parts[0];
    expected = parts[1];
  } else {
    return false;
  }
  if (!saltHex || !expected || !/^[0-9a-f]+$/i.test(saltHex)) return false;
  const salt = new Uint8Array(saltHex.match(/.{2}/g).map((x) => parseInt(x, 16)));
  for (const rounds of iterations ? [iterations] : [PBKDF2_ITERATIONS, 120000]) {
    try {
      const actual = await passwordHash(password, salt, rounds);
      if (actual.split("$")[3] === expected) return true;
    } catch {
      // Workers 不支持超过 100000 次 PBKDF2 时跳过该候选。
    }
  }
  return false;
}

// ---------- 邮箱验证码（Resend） ----------

const EMAIL_CODE_TTL_SECONDS = 300;
const EMAIL_CODE_RESEND_SECONDS = 60;
const EMAIL_CODE_MAX_ATTEMPTS = 5;
const EMAIL_CODE_MAX_PER_HOUR = 5;
const EMAIL_CODE_MAX_PER_IP_HOUR = 20;

async function hashEmailCode(env, email, code) {
  const secret = String(env.EMAIL_CODE_SECRET || "");
  if (!secret) throw new Error("EMAIL_CODE_SECRET 未配置");
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`${String(email).toLowerCase()}:${code}`)
  );
  return hex(new Uint8Array(signature));
}

async function sendEmailCode(env, email, code) {
  if (String(env.EMAIL_DRY_RUN || "") === "1") {
    console.log("[email dry-run]", email, code);
    return;
  }
  if (!env.RESEND_API_KEY) throw new Error("未配置 RESEND_API_KEY，暂时无法发送验证码");
  const from = env.MAIL_FROM || "虚拟交易 <no-reply@virtualtrader123.xyz>";
  const response = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
      Accept: "application/json",
      "User-Agent": "dubosystem/1.0",
    },
    body: JSON.stringify({
      from,
      to: [email],
      subject: "【虚拟交易】邮箱验证码",
      text: `你的验证码是 ${code}，5 分钟内有效。\n如果不是你本人操作，请忽略这封邮件。`,
    }),
  });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 300);
    throw new Error(`邮件服务返回 ${response.status}：${detail}`);
  }
}

async function createSession(env, uid) {
  const token = crypto.randomUUID() + crypto.randomUUID().replaceAll("-", "");
  await env.DB.prepare("INSERT INTO Sessions(token,user_id,expires_at) VALUES(?,?,datetime('now','+7 days'))")
    .bind(token, uid)
    .run();
  return token;
}

const sessionCookie = (token) =>
  token
    ? `sid=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${SESSION_DAYS * 86400}`
    : "sid=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0";

async function handle(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method;
  const token = (
    request.headers.get("Authorization")?.replace(/^Bearer\s+/i, "") ||
    url.searchParams.get("sid") ||
    request.headers.get("Cookie")?.match(/(?:^|;\s*)sid=([^;]+)/)?.[1] ||
    ""
  ).trim();
  const session = token
    ? await env.DB.prepare(
        "SELECT s.user_id FROM Sessions s JOIN Users u ON u.id=s.user_id " +
          "WHERE s.token=? AND s.expires_at>CURRENT_TIMESTAMP AND u.is_banned=0"
      )
        .bind(token)
        .first()
    : null;
  const uid = session?.user_id ?? null;
  const body = method === "POST" ? await request.json().catch(() => ({})) : {};

  // ---------- 公开接口 ----------

  if (path === "/api/me" && method === "GET") {
    if (!uid) return json(null);
    const user = await env.DB.prepare(
      "SELECT u.id,u.username,u.email,u.created_at,u.is_admin,a.cash " +
        "FROM Users u LEFT JOIN Accounts a ON a.user_id=u.id WHERE u.id=?"
    )
      .bind(uid)
      .first();
    if (!user) return json(null);
    return json({ ...user, created_at: isoZ(user.created_at), cash: money(user.cash) });
  }

  if (path === "/api/register" || path === "/api/login") {
    const name = String(body.username || "").trim();
    const email = String(body.email || "").trim().toLowerCase();
    const password = String(body.password || "");
    let userId;
    if (path.endsWith("register")) {
      if (!USERNAME_RE.test(name) || !EMAIL_RE.test(email) || password.length < 6 || password.length > 128) {
        return bad("请填写有效用户名、邮箱，密码至少 6 位");
      }
      const existing = await env.DB.prepare("SELECT id FROM Users WHERE username=? OR email=? LIMIT 1")
        .bind(name, email)
        .first();
      if (existing) return bad("用户名或邮箱已注册", 409);
      const code = String(body.code || "").trim();
      if (!/^\d{6}$/.test(code)) return bad("请输入 6 位邮箱验证码");
      const record = await env.DB.prepare(
        "SELECT id,code_hash,expires_at,attempts FROM EmailCodes WHERE email=? AND used=0 ORDER BY id DESC LIMIT 1"
      )
        .bind(email)
        .first();
      if (!record) return bad("请先获取邮箱验证码");
      if (parseUtc(record.expires_at) < Date.now()) return bad("验证码已过期，请重新获取");
      if (record.attempts >= EMAIL_CODE_MAX_ATTEMPTS) return bad("验证码错误次数过多，请重新获取");
      const expectedHash = await hashEmailCode(env, email, code);
      if (expectedHash !== record.code_hash) {
        await env.DB.prepare("UPDATE EmailCodes SET attempts=attempts+1 WHERE id=?").bind(record.id).run();
        const left = Math.max(0, EMAIL_CODE_MAX_ATTEMPTS - record.attempts - 1);
        return bad(`验证码不正确，还可尝试 ${left} 次`);
      }
      await env.DB.prepare("UPDATE EmailCodes SET used=1 WHERE id=?").bind(record.id).run();
      try {
        const result = await env.DB.prepare("INSERT INTO Users(username,email,password) VALUES(?,?,?)")
          .bind(name, email, await passwordHash(password))
          .run();
        userId = result.meta.last_row_id;
        await env.DB.batch([
          env.DB.prepare("INSERT INTO Accounts(user_id,cash) VALUES(?,?)").bind(userId, DEFAULT_CASH),
          env.DB.prepare("INSERT INTO Wallets(user_id,balance) VALUES(?,?)").bind(userId, DEFAULT_WEI),
          env.DB.prepare("INSERT INTO PointBalances(user_id,points) VALUES(?,0)").bind(userId),
          env.DB.prepare("INSERT INTO Memberships(user_id,expires_at) VALUES(?,NULL)").bind(userId),
        ]);
      } catch (error) {
        if (String(error?.message || error).includes("UNIQUE constraint failed")) {
          return bad("用户名或邮箱已注册", 409);
        }
        throw error;
      }
    } else {
      const row = await env.DB.prepare("SELECT id,password,is_banned FROM Users WHERE username=? OR email=?")
        .bind(name, email || name.toLowerCase())
        .first();
      if (!row || !(await checkPassword(password, row.password))) return bad("账号或密码错误", 401);
      if (row.is_banned) return bad("该账号已被管理员拉黑，无法登录", 403);
      userId = row.id;
    }
    const sid = await createSession(env, userId);
    await env.DB.prepare("UPDATE Users SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?").bind(userId).run();
    return json({ ok: true, token: sid }, 200, { "Set-Cookie": sessionCookie(sid) });
  }

  if (path === "/api/logout" && method === "POST") {
    if (token) await env.DB.prepare("DELETE FROM Sessions WHERE token=?").bind(token).run();
    return json({ ok: true }, 200, { "Set-Cookie": sessionCookie(null) });
  }

  if (path === "/api/email/send-code" && method === "POST") {
    const email = String(body.email || "").trim().toLowerCase();
    if (!EMAIL_RE.test(email)) return bad("请输入有效邮箱");
    const existing = await env.DB.prepare("SELECT id FROM Users WHERE email=? LIMIT 1").bind(email).first();
    if (existing) return bad("该邮箱已注册，请直接登录", 409);
    const latest = await env.DB.prepare("SELECT created_at FROM EmailCodes WHERE email=? ORDER BY id DESC LIMIT 1")
      .bind(email)
      .first();
    if (latest) {
      const elapsed = (Date.now() - parseUtc(latest.created_at)) / 1000;
      if (elapsed < EMAIL_CODE_RESEND_SECONDS) {
        const wait = Math.floor(EMAIL_CODE_RESEND_SECONDS - elapsed) + 1;
        return json({ error: `请 ${wait} 秒后再试`, retry_after: wait }, 429);
      }
    }
    const perEmail = await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM EmailCodes WHERE email=? AND created_at>datetime('now','-1 hour')"
    )
      .bind(email)
      .first();
    if ((perEmail?.n || 0) >= EMAIL_CODE_MAX_PER_HOUR) return bad("该邮箱发送次数过多，请稍后再试", 429);
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    const perIp = await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM EmailCodes WHERE ip=? AND created_at>datetime('now','-1 hour')"
    )
      .bind(ip)
      .first();
    if ((perIp?.n || 0) >= EMAIL_CODE_MAX_PER_IP_HOUR) return bad("发送过于频繁，请稍后再试", 429);
    const code = String(Math.floor(100000 + Math.random() * 900000));
    const codeHash = await hashEmailCode(env, email, code);
    await env.DB.prepare(
      "INSERT INTO EmailCodes(email,code_hash,ip,expires_at) VALUES(?,?,?,datetime('now','+5 minutes'))"
    )
      .bind(email, codeHash, ip)
      .run();
    await env.DB.prepare("DELETE FROM EmailCodes WHERE created_at<datetime('now','-2 days')").run();
    try {
      await sendEmailCode(env, email, code);
    } catch (error) {
      await env.DB.prepare("DELETE FROM EmailCodes WHERE email=? AND code_hash=?").bind(email, codeHash).run();
      return bad(error.message, 502);
    }
    const payload = { ok: true, retry_after: EMAIL_CODE_RESEND_SECONDS };
    if (String(env.EMAIL_DRY_RUN || "") === "1") payload.dev_code = code;
    return json(payload);
  }

  if (path === "/api/stocks" && method === "GET") {
    const items = await env.DB.prepare("SELECT code,price FROM Stocks").all();
    for (const stock of items.results) {
      const latest = await env.DB.prepare("SELECT MAX(created_at) AS t FROM MarketTicks WHERE code=?")
        .bind(stock.code)
        .first();
      if (!latest?.t || Date.now() - parseUtc(latest.t) > 30000) {
        const pct = Math.random() * 7 - 3.5;
        const price = money(Math.max(0.5, Number(stock.price) * (1 + pct / 100)));
        await env.DB.batch([
          env.DB.prepare("UPDATE Stocks SET price=?,change_pct=? WHERE code=?").bind(price, pct, stock.code),
          env.DB.prepare("INSERT INTO MarketTicks(code,price) VALUES(?,?)").bind(stock.code, price),
          env.DB.prepare(
            "DELETE FROM MarketTicks WHERE code=? AND id NOT IN " +
              `(SELECT id FROM MarketTicks WHERE code=? ORDER BY id DESC LIMIT ${TICKS_KEPT})`
          ).bind(stock.code, stock.code),
        ]);
      }
    }
    const query = `%${(url.searchParams.get("q") || "").toLowerCase()}%`;
    return json(
      (await env.DB.prepare("SELECT * FROM Stocks WHERE code LIKE ? OR name LIKE ? ORDER BY code").bind(query, query).all()).results
    );
  }

  const history = path.match(/^\/api\/stocks\/([^/]+)\/history$/);
  if (history && method === "GET") {
    const rows = await env.DB.prepare("SELECT price,created_at FROM MarketTicks WHERE code=? ORDER BY id DESC LIMIT 80")
      .bind(decodeURIComponent(history[1]))
      .all();
    return json(rows.results.reverse().map((row) => ({ price: money(row.price), created_at: isoZ(row.created_at) })));
  }

  // ---------- 以下接口一律需要登录 ----------

  if (!uid) return bad("请先登录", 401);

  if (path === "/api/wallet" && method === "GET") {
    const [wallet, points, member, historyRows, claim, admin] = await Promise.all([
      env.DB.prepare("SELECT balance FROM Wallets WHERE user_id=?").bind(uid).first(),
      env.DB.prepare("SELECT points FROM PointBalances WHERE user_id=?").bind(uid).first(),
      env.DB.prepare("SELECT expires_at FROM Memberships WHERE user_id=?").bind(uid).first(),
      env.DB.prepare(
        "SELECT item_type,item_name,wei_amount,points_added,cash_amount,member_days,created_at " +
          "FROM RechargeOrders WHERE user_id=? ORDER BY id DESC LIMIT 10"
      )
        .bind(uid)
        .all(),
      env.DB.prepare("SELECT user_id FROM WalletTopupClaims WHERE user_id=? AND claim_day=?").bind(uid, todayLocal()).first(),
      isAdmin(env, uid),
    ]);
    return json({
      balance: money(wallet?.balance),
      points: points?.points || 0,
      member_expires_at: isoZ(member?.expires_at),
      topup_available: admin || !claim,
      points_cash_rate: POINT_CASH_RATE,
      history: historyRows.results.map((row) => ({ ...row, created_at: isoZ(row.created_at) })),
    });
  }

  if (path === "/api/heartbeat" && method === "POST") {
    await env.DB.prepare("UPDATE Users SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?").bind(uid).run();
    return json({ ok: true, is_admin: await isAdmin(env, uid) });
  }

  if (path === "/api/points/exchange" && method === "POST") {
    const points = Number(body.points);
    if (!Number.isInteger(points) || points <= 0 || points > 1000000) return bad("请输入有效的积分数量");
    const balance = await env.DB.prepare("SELECT points FROM PointBalances WHERE user_id=?").bind(uid).first();
    if (!balance || Number(balance.points) < points) return bad("积分不足");
    const amount = money(points * POINT_CASH_RATE);
    await env.DB.batch([
      env.DB.prepare("UPDATE PointBalances SET points=points-? WHERE user_id=?").bind(points, uid),
      env.DB.prepare("UPDATE Accounts SET cash=cash+? WHERE user_id=?").bind(amount, uid),
      env.DB.prepare(
        "INSERT INTO RechargeOrders(user_id,item_type,item_name,wei_amount,points_added,cash_amount,member_days) " +
          "VALUES(?,'points_cash','积分兑换虚拟余额',0,?,?,0)"
      ).bind(uid, -points, amount),
    ]);
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    const left = await env.DB.prepare("SELECT points FROM PointBalances WHERE user_id=?").bind(uid).first();
    return json({ ok: true, points: left?.points || 0, amount, cash: money(account?.cash), rate: POINT_CASH_RATE });
  }

  if (path === "/api/admin/users" && method === "GET") {
    if (!(await isAdmin(env, uid))) return bad("仅管理员可以查看用户", 403);
    const rows = (
      await env.DB.prepare(
        "SELECT id,username,email,is_admin,is_banned,created_at,last_seen_at FROM Users ORDER BY id"
      ).all()
    ).results;
    return json(
      rows.map((row) => ({
        ...row,
        online: isOnline(row.last_seen_at),
        created_at: isoZ(row.created_at),
        last_seen_at: row.last_seen_at ? isoZ(row.last_seen_at) : null,
      }))
    );
  }

  if (path === "/api/admin/swaps" && method === "GET") {
    if (!(await isAdmin(env, uid))) return bad("仅管理员可以查看股票互换", 403);
    const rows = (
      await env.DB.prepare(
        "SELECT s.*,fu.username AS from_username,tu.username AS to_username," +
          "f.name AS from_name,t.name AS to_name " +
          "FROM StockSwaps s JOIN Users fu ON fu.id=s.from_user_id " +
          "JOIN Users tu ON tu.id=s.to_user_id " +
          "JOIN Stocks f ON f.code=s.from_code JOIN Stocks t ON t.code=s.to_code " +
          "ORDER BY s.id DESC LIMIT 100"
      ).all()
    ).results;
    return json(
      rows.map((row) => ({
        ...row,
        created_at: isoZ(row.created_at),
        responded_at: row.responded_at ? isoZ(row.responded_at) : null,
      }))
    );
  }

  if (path === "/api/swaps" && method === "GET") {
    const rows = (
      await env.DB.prepare(
        "SELECT s.*,fu.username AS from_username,tu.username AS to_username," +
          "f.name AS from_name,t.name AS to_name " +
          "FROM StockSwaps s JOIN Users fu ON fu.id=s.from_user_id " +
          "JOIN Users tu ON tu.id=s.to_user_id " +
          "JOIN Stocks f ON f.code=s.from_code JOIN Stocks t ON t.code=s.to_code " +
          "WHERE s.from_user_id=? OR s.to_user_id=? ORDER BY s.id DESC LIMIT 50"
      )
        .bind(uid, uid)
        .all()
    ).results;
    const items = rows.map((row) => ({
      ...row,
      created_at: isoZ(row.created_at),
      responded_at: row.responded_at ? isoZ(row.responded_at) : null,
    }));
    return json({
      incoming: items.filter((row) => row.to_user_id === uid),
      outgoing: items.filter((row) => row.from_user_id === uid),
    });
  }

  if (path === "/api/portfolio" && method === "GET") {
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    const rows = (
      await env.DB.prepare(
        "SELECT h.code,s.name,h.quantity,h.avg_cost,s.price FROM Holdings h JOIN Stocks s ON s.code=h.code " +
          "WHERE h.user_id=? AND h.quantity>0"
      )
        .bind(uid)
        .all()
    ).results;
    const positions = rows.map((row) => {
      const value = money(row.quantity * row.price);
      const gain = money((row.price - row.avg_cost) * row.quantity);
      return { ...row, avg_cost: money(row.avg_cost), price: money(row.price), market_value: value, profit: gain };
    });
    return json({
      cash: money(account?.cash),
      positions,
      market_value: money(positions.reduce((sum, item) => sum + item.market_value, 0)),
      profit: money(positions.reduce((sum, item) => sum + item.profit, 0)),
    });
  }

  if (path === "/api/orders" && method === "GET") {
    const rows = await env.DB.prepare(
      "SELECT o.*,s.name FROM Orders o JOIN Stocks s ON s.code=o.code WHERE user_id=? ORDER BY id DESC LIMIT 30"
    )
      .bind(uid)
      .all();
    return json(rows.results.map((row) => ({ ...row, created_at: isoZ(row.created_at) })));
  }

  if ((path === "/api/mining/status" || path === "/api/forge/status") && method === "GET") {
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    const cash = Number(account?.cash || 0);
    const admin = await isAdmin(env, uid);
    const game = path.startsWith("/api/mining") ? "mining" : "forge";
    const stats = await env.DB.prepare("SELECT runs,earned FROM GameStats WHERE user_id=? AND game=?")
      .bind(uid, game)
      .first();
    let active = null;
    if (game === "mining") {
      active = await env.DB.prepare(
        "SELECT id,pot,digs FROM MiningRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1"
      )
        .bind(uid)
        .first();
      if (active) active.pot = money(active.pot);
    } else {
      active = await env.DB.prepare(
        "SELECT id,value,hits,risk FROM ForgeRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1"
      )
        .bind(uid)
        .first();
      if (active) {
        active.value = money(active.value);
        active.risk = forgeRisk(active.hits);
        const grade = forgeGrade(active.hits);
        active.quality = grade.tier;
        active.quality_name = grade.name;
      }
    }
    return json({
      unlocked: admin || cash < GAME_UNLOCK_CASH,
      cash: money(cash),
      runs: stats?.runs || 0,
      earned: money(stats?.earned),
      active,
    });
  }

  if (path === "/api/mining/start" && method === "POST") {
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    if (!(await isAdmin(env, uid)) && Number(account?.cash || 0) >= GAME_UNLOCK_CASH) return bad("虚拟资金需低于 ¥10,000 才能进入矿场", 403);
    await env.DB.batch([
      env.DB.prepare("UPDATE MiningRuns SET status='abandoned',ended_at=CURRENT_TIMESTAMP WHERE user_id=? AND status='active'").bind(uid),
      env.DB.prepare("INSERT INTO MiningRuns(user_id,pot,digs) VALUES(?,0,0)").bind(uid),
      env.DB.prepare(
        "INSERT INTO GameStats(user_id,game,runs) VALUES(?,'mining',1) " +
          "ON CONFLICT(user_id,game) DO UPDATE SET runs=runs+1"
      ).bind(uid),
    ]);
    return json({ ok: true });
  }

  if (path === "/api/mining/dig" && method === "POST") {
    const run = await env.DB.prepare(
      "SELECT id,pot,digs FROM MiningRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1"
    )
      .bind(uid)
      .first();
    if (!run) return bad("请先进入矿场");
    const roll = Math.random();
    if (roll < MINING_BOMB_CHANCE) {
      await env.DB.prepare(
        "UPDATE MiningRuns SET status='bombed',pot=0,digs=digs+1,ended_at=CURRENT_TIMESTAMP WHERE id=?"
      )
        .bind(run.id)
        .run();
      return json({ ok: true, result: "bomb", amount: 0, pot: 0, digs: run.digs + 1, status: "bombed" });
    }
    const bands = [
      ["diamond", MINING_DIAMOND_CHANCE],
      ["gold", MINING_GOLD_CHANCE],
      ["iron", MINING_IRON_CHANCE],
      ["coal", MINING_COAL_CHANCE],
    ];
    const rewards = {
      soil: MINING_SOIL_REWARD,
      coal: MINING_COAL_REWARD,
      iron: MINING_IRON_REWARD,
      gold: MINING_GOLD_REWARD,
      diamond: MINING_DIAMOND_REWARD,
    };
    let cursor = MINING_BOMB_CHANCE;
    let result = "soil";
    for (const [name, chance] of bands) {
      cursor += chance;
      if (roll < cursor) {
        result = name;
        break;
      }
    }
    const [min, max] = rewards[result];
    const amount = min + Math.floor(Math.random() * (max - min + 1));
    const pot = money(Number(run.pot) + amount);
    await env.DB.prepare("UPDATE MiningRuns SET pot=?,digs=digs+1 WHERE id=?").bind(pot, run.id).run();
    return json({ ok: true, result, amount, pot, digs: run.digs + 1, status: "active" });
  }

  if (path === "/api/mining/cashout" && method === "POST") {
    const run = await env.DB.prepare(
      "SELECT id,pot FROM MiningRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1"
    )
      .bind(uid)
      .first();
    if (!run) return bad("没有可结算的矿场");
    const amount = money(run.pot);
    const statements = [];
    if (amount > 0) statements.push(env.DB.prepare("UPDATE Accounts SET cash=cash+? WHERE user_id=?").bind(amount, uid));
    statements.push(env.DB.prepare("UPDATE MiningRuns SET status='cashed',ended_at=CURRENT_TIMESTAMP WHERE id=?").bind(run.id));
    statements.push(
      env.DB.prepare(
        "INSERT INTO GameStats(user_id,game,runs,earned) VALUES(?,'mining',0,?) " +
          "ON CONFLICT(user_id,game) DO UPDATE SET earned=earned+excluded.earned"
      ).bind(uid, amount)
    );
    await env.DB.batch(statements);
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    return json({ ok: true, amount, cash: money(account?.cash), status: "cashed" });
  }

  if (path === "/api/forge/start" && method === "POST") {
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    if (!(await isAdmin(env, uid)) && Number(account?.cash || 0) >= GAME_UNLOCK_CASH) return bad("虚拟资金需低于 ¥10,000 才能进入锻剑工坊", 403);
    await env.DB.batch([
      env.DB.prepare("UPDATE ForgeRuns SET status='abandoned',ended_at=CURRENT_TIMESTAMP WHERE user_id=? AND status='active'").bind(uid),
      env.DB.prepare("INSERT INTO ForgeRuns(user_id,value,hits,risk) VALUES(?,0,0,?)").bind(uid, FORGE_BASE_SHATTER),
      env.DB.prepare(
        "INSERT INTO GameStats(user_id,game,runs) VALUES(?,'forge',1) " +
          "ON CONFLICT(user_id,game) DO UPDATE SET runs=runs+1"
      ).bind(uid),
    ]);
    return json({
      ok: true,
      run: { value: 0, hits: 0, risk: FORGE_BASE_SHATTER, quality: 0, quality_name: FORGE_GRADES[0] },
    });
  }

  if (path === "/api/forge/hit" && method === "POST") {
    const run = await env.DB.prepare(
      "SELECT id,value,hits,risk FROM ForgeRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1"
    )
      .bind(uid)
      .first();
    if (!run) return bad("请先开始锻剑");
    const risk = forgeRisk(run.hits);
    const chance = Math.min(FORGE_MAX_SHATTER, risk);
    if (Math.random() < chance) {
      const grade = forgeGrade(run.hits);
      await env.DB.prepare(
        "UPDATE ForgeRuns SET status='shattered',value=0,ended_at=CURRENT_TIMESTAMP WHERE id=?"
      )
        .bind(run.id)
        .run();
      return json({
        ok: true, shattered: true, gain: 0, value: 0, hits: run.hits, status: "shattered",
        risk, quality: grade.tier, quality_name: grade.name,
      });
    }
    const gain = FORGE_HIT_REWARD[0] + Math.floor(Math.random() * (FORGE_HIT_REWARD[1] - FORGE_HIT_REWARD[0] + 1));
    const hits = run.hits + 1;
    const grade = forgeGrade(hits);
    const upgraded = FORGE_GRADE_THRESHOLDS.includes(hits);
    let value = Number(run.value) + gain;
    if (upgraded) value *= 2;
    const newRisk = forgeRisk(hits);
    await env.DB.prepare("UPDATE ForgeRuns SET value=?,hits=?,risk=? WHERE id=?")
      .bind(value, hits, newRisk, run.id)
      .run();
    return json({
      ok: true, shattered: false, gain, value: money(value), hits, status: "active",
      risk: newRisk, quality: grade.tier, quality_name: grade.name, upgraded,
    });
  }

  if (path === "/api/forge/sell" && method === "POST") {
    const run = await env.DB.prepare(
      "SELECT id,value,hits FROM ForgeRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1"
    )
      .bind(uid)
      .first();
    if (!run) return bad("没有可出售的剑");
    const amount = money(run.value);
    const statements = [];
    if (amount > 0) statements.push(env.DB.prepare("UPDATE Accounts SET cash=cash+? WHERE user_id=?").bind(amount, uid));
    statements.push(env.DB.prepare("UPDATE ForgeRuns SET status='sold',ended_at=CURRENT_TIMESTAMP WHERE id=?").bind(run.id));
    statements.push(
      env.DB.prepare(
        "INSERT INTO GameStats(user_id,game,runs,earned) VALUES(?,'forge',0,?) " +
          "ON CONFLICT(user_id,game) DO UPDATE SET earned=earned+excluded.earned"
      ).bind(uid, amount)
    );
    await env.DB.batch(statements);
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    return json({ ok: true, amount, cash: money(account?.cash), status: "sold" });
  }

  if (path === "/api/fishing/status" && method === "GET") {
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    const cash = Number(account?.cash || 0);
    const admin = await isAdmin(env, uid);
    const row = await env.DB.prepare("SELECT attempts,earned FROM FishingDaily WHERE user_id=? AND day=?")
      .bind(uid, todayLocal())
      .first();
    return json({
      unlocked: admin || cash < FISHING_UNLOCK_CASH,
      cash: money(cash),
      attempts: row?.attempts || 0,
      earned: money(row?.earned),
      daily_limit: null,
    });
  }

  if (path === "/api/fishing/start" && method === "POST") {
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    if (!(await isAdmin(env, uid)) && Number(account?.cash || 0) >= FISHING_UNLOCK_CASH) return bad("虚拟现金需低于 ¥10,000 才能开始练习");
    const day = todayLocal();
    await env.DB.prepare("INSERT OR IGNORE INTO FishingDaily(user_id,day) VALUES(?,?)").bind(uid, day).run();
    await env.DB.prepare(
      "UPDATE FishingDaily SET attempts=attempts+1,pending=1,started_at=CURRENT_TIMESTAMP WHERE user_id=? AND day=?"
    )
      .bind(uid, day)
      .run();
    const row = await env.DB.prepare("SELECT attempts,earned FROM FishingDaily WHERE user_id=? AND day=?")
      .bind(uid, day)
      .first();
    return json({ ok: true, attempts: row.attempts, earned: money(row.earned) });
  }

  if (path === "/api/fishing/catch" && method === "POST") {
    let score = Math.max(0, Math.min(100, Number(body.score) || 0));
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    if (!(await isAdmin(env, uid)) && Number(account?.cash || 0) >= FISHING_UNLOCK_CASH) {
      return bad("余额高于 ¥10,000，恢复小游戏暂未开放。请先暂停交易并复盘。");
    }
    const day = todayLocal();
    const row = await env.DB.prepare("SELECT attempts,earned,pending,started_at FROM FishingDaily WHERE user_id=? AND day=?")
      .bind(uid, day)
      .first();
    if (!row?.pending) return bad("请先开始本轮钓鱼练习");
    // 进度最快每秒上涨 10%；只有填满 100% 才算钓到鱼并获得奖励。
    const elapsed = row.started_at ? (Date.now() - parseUtc(row.started_at)) / 1000 : 0;
    score = Math.min(score, Math.min(100, Math.floor(elapsed * 10) + 1));
    if (score < 100) {
      await env.DB.prepare("UPDATE FishingDaily SET pending=0 WHERE user_id=? AND day=?").bind(uid, day).run();
      return json({
        ok: true,
        won: false,
        reward: 0,
        score,
        fish: null,
        attempts: row.attempts,
        daily_limit: null,
      });
    }
    const fishPool = [
      ["溪流鲦", "🐟", 60],
      ["银鳞鲑", "🐠", 23],
      ["锦鲤", "🎏", 12],
      ["金色锦鲤", "🐡", 4],
      ["钻石鱼王", "💎🐟", 1],
    ];
    const roll = Math.random() * 100;
    let mark = 0;
    let fish = fishPool[0];
    for (const candidate of fishPool) {
      mark += candidate[2];
      if (roll < mark) {
        fish = candidate;
        break;
      }
    }
    const multiplier = fish[0] === "钻石鱼王" ? 5 : fish[0] === "金色锦鲤" ? 2 : 1;
    const reward = 1000 * multiplier;
    await env.DB.batch([
      env.DB.prepare("UPDATE FishingDaily SET pending=0,earned=earned+? WHERE user_id=? AND day=?").bind(reward, uid, day),
      env.DB.prepare("UPDATE Accounts SET cash=cash+? WHERE user_id=?").bind(reward, uid),
    ]);
    return json({
      ok: true,
      won: true,
      reward,
      score,
      fish: { name: fish[0], emoji: fish[1] },
      attempts: row.attempts,
      daily_limit: null,
    });
  }

  if (path === "/api/admin/users/kick" || path === "/api/admin/users/ban") {
    if (!(await isAdmin(env, uid))) return bad("仅管理员可以管理用户", 403);
    const targetId = Number(body.id);
    if (!Number.isInteger(targetId) || targetId === uid) return bad("不能操作当前登录账号");
    const target = await env.DB.prepare("SELECT id,is_admin FROM Users WHERE id=?").bind(targetId).first();
    if (!target) return bad("用户不存在", 404);
    if (target.is_admin) return bad("不能操作其他管理员");
    if (path.endsWith("/kick")) {
      await env.DB.batch([
        env.DB.prepare("DELETE FROM Sessions WHERE user_id=?").bind(targetId),
        env.DB.prepare("UPDATE Users SET last_seen_at=NULL WHERE id=?").bind(targetId),
      ]);
      return json({ ok: true, action: "kick" });
    }
    const banned = body.banned !== false;
    const statements = [
      env.DB.prepare("UPDATE Users SET is_banned=? WHERE id=?").bind(banned ? 1 : 0, targetId),
    ];
    if (banned) {
      statements.push(env.DB.prepare("DELETE FROM Sessions WHERE user_id=?").bind(targetId));
      statements.push(env.DB.prepare("UPDATE Users SET last_seen_at=NULL WHERE id=?").bind(targetId));
    }
    await env.DB.batch(statements);
    return json({ ok: true, action: banned ? "ban" : "unban", banned });
  }

  if (path === "/api/swaps" && method === "POST") {
    const toUsername = String(body.to_username || "").trim();
    const fromCode = String(body.from_code || "").trim();
    const toCode = String(body.to_code || "").trim();
    const fromQuantity = Number(body.from_quantity);
    const toQuantity = Number(body.to_quantity);
    if (fromCode === toCode) return bad("互换的两只股票不能相同");
    if (![fromQuantity, toQuantity].every((n) => Number.isInteger(n) && n > 0)) return bad("互换数量必须大于 0");
    const target = await env.DB.prepare("SELECT id,is_banned FROM Users WHERE username=?").bind(toUsername).first();
    if (!target) return bad("找不到对方用户名", 404);
    if (target.id === uid) return bad("不能和自己互换");
    if (target.is_banned) return bad("对方账号已被拉黑");
    for (const code of [fromCode, toCode]) {
      const stock = await env.DB.prepare("SELECT 1 FROM Stocks WHERE code=?").bind(code).first();
      if (!stock) return bad("股票不存在", 404);
    }
    const holding = await env.DB.prepare("SELECT quantity FROM Holdings WHERE user_id=? AND code=?")
      .bind(uid, fromCode)
      .first();
    if (!holding || Number(holding.quantity) < fromQuantity) return bad("你持有的该股票数量不足");
    const message = cleanText(body.message || "", 0, 200);
    if (message === null) return bad("留言包含不支持的字符或过长");
    const result = await env.DB.prepare(
      "INSERT INTO StockSwaps(from_user_id,to_user_id,from_code,from_quantity,to_code,to_quantity,message) " +
        "VALUES(?,?,?,?,?,?,?)"
    )
      .bind(uid, target.id, fromCode, fromQuantity, toCode, toQuantity, message)
      .run();
    return json({ ok: true, id: result.meta.last_row_id });
  }

  if (path === "/api/swaps/accept" || path === "/api/swaps/decline") {
    const swapId = Number(body.id);
    if (!Number.isInteger(swapId)) return bad("互换请求不存在", 404);
    const swap = await env.DB.prepare("SELECT * FROM StockSwaps WHERE id=?").bind(swapId).first();
    if (!swap) return bad("互换请求不存在", 404);
    if (swap.from_user_id !== uid && swap.to_user_id !== uid) return bad("无权操作该互换请求", 403);
    if (swap.status !== "pending") return bad("该请求已经处理", 409);
    if (path.endsWith("/decline")) {
      const status = uid === swap.to_user_id ? "declined" : "cancelled";
      await env.DB.prepare("UPDATE StockSwaps SET status=?,responded_at=CURRENT_TIMESTAMP WHERE id=?")
        .bind(status, swapId)
        .run();
      return json({ ok: true, status });
    }
    if (uid !== swap.to_user_id) return bad("只有接收方可以接受互换", 403);
    const fromStock = await env.DB.prepare("SELECT price FROM Stocks WHERE code=?").bind(swap.from_code).first();
    const toStock = await env.DB.prepare("SELECT price FROM Stocks WHERE code=?").bind(swap.to_code).first();
    if (!fromStock || !toStock) return bad("股票数据不存在", 404);
    const moveA = await holdingMoveStatements(
      env, swap.from_user_id, uid, swap.from_code, Number(swap.from_quantity), Number(fromStock.price)
    );
    if (!moveA) return bad("发起方持仓不足，无法完成互换");
    const moveB = await holdingMoveStatements(
      env, uid, swap.from_user_id, swap.to_code, Number(swap.to_quantity), Number(toStock.price)
    );
    if (!moveB) return bad("你的持仓不足，无法完成互换");
    await env.DB.batch([
      ...moveA,
      ...moveB,
      env.DB.prepare("UPDATE StockSwaps SET status='accepted',responded_at=CURRENT_TIMESTAMP WHERE id=?")
        .bind(swapId),
    ]);
    return json({ ok: true, status: "accepted" });
  }

  if (path === "/api/trade" && method === "POST") {
    const code = String(body.code || "");
    const side = body.side;
    const quantity = body.quantity;
    if (!["买入", "卖出"].includes(side) || !Number.isInteger(quantity) || quantity < 100 || quantity % 100) {
      return bad("请输入 100 股起、100 股整数倍的数量");
    }
    const stock = await env.DB.prepare("SELECT price FROM Stocks WHERE code=?").bind(code).first();
    if (!stock) return bad("没有找到该模拟股票", 404);
    const account = await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first();
    if (!account) return bad("账户数据异常，请重置体验");
    const holding = await env.DB.prepare("SELECT quantity,avg_cost FROM Holdings WHERE user_id=? AND code=?")
      .bind(uid, code)
      .first();
    const price = Number(stock.price);
    const amount = money(price * quantity);
    if (side === "买入") {
      if (amount > Number(account.cash)) return bad("模拟现金不足。请留意仓位和风险，不要借钱交易。");
      const oldQuantity = holding?.quantity || 0;
      const avg = money((oldQuantity * (holding?.avg_cost || 0) + amount) / (oldQuantity + quantity));
      await env.DB.batch([
        env.DB.prepare("UPDATE Accounts SET cash=cash-? WHERE user_id=?").bind(amount, uid),
        env.DB.prepare(
          "INSERT INTO Holdings(user_id,code,quantity,avg_cost) VALUES(?,?,?,?) " +
            "ON CONFLICT(user_id,code) DO UPDATE SET quantity=quantity+excluded.quantity,avg_cost=?"
        ).bind(uid, code, quantity, price, avg),
        env.DB.prepare("INSERT INTO Orders(user_id,code,side,quantity,price) VALUES(?,?,?,?,?)").bind(uid, code, side, quantity, price),
      ]);
    } else {
      if (!holding || holding.quantity < quantity) return bad("持仓数量不足，不能卖出不存在的股票");
      await env.DB.batch([
        env.DB.prepare("UPDATE Accounts SET cash=cash+? WHERE user_id=?").bind(amount, uid),
        env.DB.prepare("UPDATE Holdings SET quantity=quantity-? WHERE user_id=? AND code=?").bind(quantity, uid, code),
        env.DB.prepare("INSERT INTO Orders(user_id,code,side,quantity,price) VALUES(?,?,?,?,?)").bind(uid, code, side, quantity, price),
      ]);
    }
    return json({ ok: true, price: money(price) });
  }

  if (path === "/api/reset" && method === "POST") {
    await env.DB.batch([
      env.DB.prepare("UPDATE Accounts SET cash=? WHERE user_id=?").bind(DEFAULT_CASH, uid),
      env.DB.prepare("DELETE FROM Holdings WHERE user_id=?").bind(uid),
      env.DB.prepare("DELETE FROM Orders WHERE user_id=?").bind(uid),
    ]);
    return json({ ok: true });
  }

  if (path === "/api/admin/stocks" && method === "POST") {
    const admin = await env.DB.prepare("SELECT is_admin FROM Users WHERE id=?").bind(uid).first();
    if (!admin?.is_admin) return bad("仅管理员可以添加模拟股票", 403);
    const code = String(body.code || "").trim().toUpperCase();
    const name = cleanText(body.name, 2, 100);
    const category = cleanText(body.category, 1, 50);
    const price = Number(body.price);
    if (!/^[A-Z0-9]{1,16}$/.test(code) || !name || !category || !Number.isFinite(price) || price <= 0 || price > 100000000) {
      return bad("请填写有效代码、名称、类别和大于 0 的模拟价格");
    }
    if (await env.DB.prepare("SELECT code FROM Stocks WHERE code=?").bind(code).first()) {
      return bad(`股票代码 ${code} 已存在，请换一个代码`, 409);
    }
    if (await env.DB.prepare("SELECT code FROM Stocks WHERE lower(name)=lower(?)").bind(name).first()) {
      return bad(`股票名称“${name}”已存在，请换一个名称`, 409);
    }
    try {
      await env.DB.batch([
        env.DB.prepare("INSERT INTO Stocks(code,name,price,change_pct,category,is_custom) VALUES(?,?,?,0,?,1)").bind(
          code,
          name,
          money(price),
          category
        ),
        env.DB.prepare("INSERT INTO MarketTicks(code,price) VALUES(?,?)").bind(code, money(price)),
      ]);
    } catch {
      return bad("该股票代码已经存在或无法保存", 409);
    }
    return json({ ok: true, stock: { code, name, price: money(price), category } });
  }

  if (path === "/api/admin/stocks/delete" && method === "POST") {
    const admin = await env.DB.prepare("SELECT is_admin FROM Users WHERE id=?").bind(uid).first();
    if (!admin?.is_admin) return bad("仅管理员可以删除模拟股票", 403);
    const code = String(body.code || "").trim().toUpperCase();
    const stock = await env.DB.prepare("SELECT code,is_custom FROM Stocks WHERE code=?").bind(code).first();
    if (!stock) return bad("没有找到该股票代码", 404);
    if (!stock.is_custom) return bad("系统预置股票不能删除");
    const used =
      (await env.DB.prepare("SELECT id FROM Orders WHERE code=? LIMIT 1").bind(code).first()) ||
      (await env.DB.prepare("SELECT user_id FROM Holdings WHERE code=? AND quantity>0 LIMIT 1").bind(code).first());
    if (used) return bad("该股票已有持仓或交易记录，暂不能删除", 409);
    await env.DB.batch([
      env.DB.prepare("DELETE FROM Holdings WHERE code=? AND quantity=0").bind(code),
      env.DB.prepare("DELETE FROM Stocks WHERE code=?").bind(code),
    ]);
    return json({ ok: true, code });
  }

  if (path === "/api/wallet/topup" && method === "POST") {
    const day = todayLocal();
    const admin = await isAdmin(env, uid);
    if (!admin) {
      const claimed = await env.DB.prepare("SELECT user_id FROM WalletTopupClaims WHERE user_id=? AND claim_day=?")
        .bind(uid, day)
        .first();
      if (claimed) return bad("今天的虚拟玮元补给已领取，请明天再来", 409);
    }
    const statements = [
      env.DB.prepare("UPDATE Wallets SET balance=balance+? WHERE user_id=?").bind(500, uid),
      env.DB.prepare(
        "INSERT INTO RechargeOrders(user_id,item_type,item_name,wei_amount,points_added,member_days) " +
          "VALUES(?,'wei','每日练习补给',500,0,0)"
      ).bind(uid),
    ];
    if (!admin) {
      statements.unshift(env.DB.prepare("INSERT INTO WalletTopupClaims(user_id,claim_day) VALUES(?,?)").bind(uid, day));
    }
    try {
      await env.DB.batch(statements);
    } catch (error) {
      if (String(error?.message || error).includes("UNIQUE constraint failed")) {
        return bad("今天的虚拟玮元补给已领取，请明天再来", 409);
      }
      throw error;
    }
    return json({ ok: true, item_name: "每日练习补给", amount: 500 });
  }

  if (path === "/api/recharge" && method === "POST") {
    const catalog = {
      points_100: ["points", "100 积分", 100, 100, 0],
      points_500: ["points", "500 积分", 500, 500, 0],
      points_1000: ["points", "1000 积分", 1000, 1000, 0],
      member_7: ["member", "体验会员 7 天", 300, 0, 7],
      member_30: ["member", "体验会员 30 天", 1000, 0, 30],
      member_90: ["member", "体验会员 90 天", 2000, 0, 90],
    };
    const item = catalog[body.package];
    if (!item) return bad("请选择有效的模拟套餐");
    const [kind, name, cost, points, days] = item;
    const wallet = await env.DB.prepare("SELECT balance FROM Wallets WHERE user_id=?").bind(uid).first();
    if (!wallet || Number(wallet.balance) < cost) return bad("玮元余额不足，模拟订单未扣款");
    const statements = [env.DB.prepare("UPDATE Wallets SET balance=balance-? WHERE user_id=?").bind(cost, uid)];
    if (points) statements.push(env.DB.prepare("UPDATE PointBalances SET points=points+? WHERE user_id=?").bind(points, uid));
    if (days) {
      statements.push(
        env.DB.prepare(
          "UPDATE Memberships SET expires_at=datetime(CASE WHEN expires_at>CURRENT_TIMESTAMP THEN expires_at ELSE CURRENT_TIMESTAMP END,?) " +
            "WHERE user_id=?"
        ).bind(`+${days} days`, uid)
      );
    }
    statements.push(
      env.DB.prepare(
        "INSERT INTO RechargeOrders(user_id,item_type,item_name,wei_amount,points_added,member_days) VALUES(?,?,?,?,?,?)"
      ).bind(uid, kind, name, cost, points, days)
    );
    await env.DB.batch(statements);
    return json({ ok: true, item_name: name });
  }

  return bad("Not found", 404);
}

export async function onRequest(context) {
  try {
    return await handle(context.request, context.env);
  } catch (error) {
    // 只把异常写进 Pages Functions 日志，避免把数据库细节泄露给访问者。
    console.error("Pages API error", {
      path: new URL(context.request.url).pathname,
      message: error?.message || String(error),
      stack: error?.stack || "",
    });
    return json({ error: "服务暂时不可用，请检查 Cloudflare D1 配置" }, 500);
  }
}
