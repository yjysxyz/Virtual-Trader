const json = (data, status = 200, headers = {}) => Response.json(data, { status, headers: { "Cache-Control": "no-store", ...headers } });
const bad = (message, status = 400) => json({ error: message }, status);
const money = n => Number(Number(n || 0).toFixed(2));
const today = () => new Date().toISOString().slice(0, 10);
async function passwordHash(password, salt = crypto.getRandomValues(new Uint8Array(16))) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", salt, iterations: 120000, hash: "SHA-256" }, key, 256);
  return `${Array.from(salt, x => x.toString(16).padStart(2, "0")).join("")}$${Array.from(new Uint8Array(bits), x => x.toString(16).padStart(2, "0")).join("")}`;
}
async function checkPassword(password, stored) {
  const [saltHex, expected] = (stored || "").split("$");
  if (!saltHex || !expected) return false;
  const salt = new Uint8Array(saltHex.match(/.{2}/g).map(x => parseInt(x, 16)));
  const actual = await passwordHash(password, salt);
  return actual.split("$")[1] === expected;
}
const seed = [
  ["600519", "贵州茅台", 1688, "消费"], ["000001", "平安银行", 11.38, "金融"],
  ["000858", "五粮液", 142.65, "消费"], ["300750", "宁德时代", 186.32, "新能源"],
  ["601318", "中国平安", 43.12, "金融"], ["002594", "比亚迪", 241.80, "新能源"]
];
async function handle(request, env) {
  const url = new URL(request.url), path = url.pathname;
  const token = request.headers.get("Authorization")?.replace(/^Bearer\s+/i, "") || url.searchParams.get("sid") || request.headers.get("Cookie")?.match(/(?:^|;\s*)sid=([^;]+)/)?.[1] || "";
  const session = token ? await env.DB.prepare("SELECT user_id FROM Sessions WHERE token=? AND expires_at>CURRENT_TIMESTAMP").bind(token).first() : null;
  const uid = session?.user_id;
  const body = request.method === "POST" ? await request.json().catch(() => ({})) : {};
  if (path === "/api/me" && request.method === "GET") {
    if (!uid) return json(null);
    const user = await env.DB.prepare("SELECT u.id,u.username,u.email,u.created_at,u.is_admin,a.cash FROM Users u JOIN Accounts a ON a.user_id=u.id WHERE u.id=?").bind(uid).first();
    return json(user);
  }
  if (path === "/api/register" || path === "/api/login") {
    const name = String(body.username || "").trim(), email = String(body.email || "").trim().toLowerCase(), pw = String(body.password || "");
    if (path.endsWith("register")) {
      if (name.length < 2 || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || pw.length < 6) return bad("请填写有效用户名、邮箱，密码至少 6 位");
      const existing=await env.DB.prepare("SELECT id FROM Users WHERE username=? OR lower(email)=lower(?) LIMIT 1").bind(name,email).first();
      if(existing)return bad("用户名或邮箱已注册",409);
      try {
        const result = await env.DB.prepare("INSERT INTO Users(username,email,password) VALUES(?,?,?)").bind(name,email,await passwordHash(pw)).run();
        const id = result.meta.last_row_id;
        await env.DB.batch([
          env.DB.prepare("INSERT INTO Accounts(user_id,cash) VALUES(?,100000)").bind(id), env.DB.prepare("INSERT INTO Wallets(user_id,balance) VALUES(?,2000)").bind(id),
          env.DB.prepare("INSERT INTO PointBalances(user_id,points) VALUES(?,0)").bind(id), env.DB.prepare("INSERT INTO Memberships(user_id) VALUES(?)").bind(id)
        ]);
        body.id = id;
      } catch (e) {
        if(String(e?.message||e).includes("UNIQUE constraint failed"))return bad("用户名或邮箱已注册",409);
        throw e;
      }
    } else {
      const row = await env.DB.prepare("SELECT id,password FROM Users WHERE username=? OR email=?").bind(name,name.toLowerCase()).first();
      if (!row || !(await checkPassword(pw,row.password))) return bad("账号或密码错误",401);
      body.id = row.id;
    }
    const sid = crypto.randomUUID() + crypto.randomUUID().replaceAll("-", "");
    await env.DB.prepare("INSERT INTO Sessions(token,user_id,expires_at) VALUES(?,?,datetime('now','+7 days'))").bind(sid,body.id).run();
    return json({ok:true,token:sid},200,{"Set-Cookie":`sid=${sid}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=604800`});
  }
  if (path === "/api/logout" && request.method === "POST") {
    if (token) await env.DB.prepare("DELETE FROM Sessions WHERE token=?").bind(token).run();
    return json({ok:true},200,{"Set-Cookie":"sid=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0"});
  }
  if (path === "/api/stocks" && request.method === "GET") {
    const items = await env.DB.prepare("SELECT code,price FROM Stocks").all();
    for (const s of items.results) {
      const row = await env.DB.prepare("SELECT MAX(created_at) t FROM MarketTicks WHERE code=?").bind(s.code).first();
      if (!row.t || Date.now() - Date.parse(row.t.replace(" ","T")+"Z") > 30000) {
        const pct = Math.random()*7-3.5, price = money(Math.max(.5,Number(s.price)*(1+pct/100)));
        await env.DB.batch([env.DB.prepare("UPDATE Stocks SET price=?,change_pct=? WHERE code=?").bind(price,pct,s.code),env.DB.prepare("INSERT INTO MarketTicks(code,price) VALUES(?,?)").bind(s.code,price)]);
      }
    }
    const q = `%${(url.searchParams.get("q") || "").toLowerCase()}%`;
    return json((await env.DB.prepare("SELECT * FROM Stocks WHERE code LIKE ? OR name LIKE ? ORDER BY code").bind(q,q).all()).results);
  }
  const history = path.match(/^\/api\/stocks\/([^/]+)\/history$/);
  if (history && request.method === "GET") return json((await env.DB.prepare("SELECT price,created_at FROM MarketTicks WHERE code=? ORDER BY id DESC LIMIT 80").bind(decodeURIComponent(history[1])).all()).results.reverse());
  if (!uid && ["/api/wallet","/api/portfolio","/api/orders","/api/fishing/status"].includes(path)) return bad("请先登录",401);
  if (path === "/api/wallet" && request.method === "GET") {
    const [wallet,points,member,history] = await Promise.all([
      env.DB.prepare("SELECT balance FROM Wallets WHERE user_id=?").bind(uid).first(),env.DB.prepare("SELECT points FROM PointBalances WHERE user_id=?").bind(uid).first(),
      env.DB.prepare("SELECT expires_at FROM Memberships WHERE user_id=?").bind(uid).first(),env.DB.prepare("SELECT item_type,item_name,wei_amount,points_added,member_days,created_at FROM RechargeOrders WHERE user_id=? ORDER BY id DESC LIMIT 10").bind(uid).all()
    ]);
    return json({balance:wallet.balance,points:points.points,member_expires_at:member.expires_at,history:history.results});
  }
  if (path === "/api/portfolio" && request.method === "GET") {
    const cash=(await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first()).cash;
    const rows=(await env.DB.prepare("SELECT h.code,s.name,h.quantity,h.avg_cost,s.price FROM Holdings h JOIN Stocks s ON s.code=h.code WHERE h.user_id=? AND h.quantity>0").bind(uid).all()).results;
    const positions=rows.map(r=>({...r,market_value:r.quantity*r.price,profit:(r.price-r.avg_cost)*r.quantity}));
    return json({cash,positions,market_value:positions.reduce((a,x)=>a+x.market_value,0),profit:positions.reduce((a,x)=>a+x.profit,0)});
  }
  if (path === "/api/orders" && request.method === "GET") return json((await env.DB.prepare("SELECT o.*,s.name FROM Orders o JOIN Stocks s ON s.code=o.code WHERE user_id=? ORDER BY id DESC LIMIT 30").bind(uid).all()).results);
  if (path === "/api/fishing/status" && request.method === "GET") {
    const cash=(await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first()).cash, row=await env.DB.prepare("SELECT attempts,earned FROM FishingDaily WHERE user_id=? AND day=?").bind(uid,today()).first();
    return json({unlocked:cash<10000,cash,attempts:row?.attempts||0,earned:row?.earned||0,daily_limit:null});
  }
  if (path === "/api/admin/stocks" && request.method === "POST") {
    if(!uid)return bad("请先登录",401);
    const admin=await env.DB.prepare("SELECT is_admin FROM Users WHERE id=?").bind(uid).first();
    if(!admin?.is_admin)return bad("仅管理员可以添加模拟股票",403);
    const code=String(body.code||"").trim().toUpperCase(),name=String(body.name||"").trim(),category=String(body.category||"").trim(),price=Number(body.price);
    if(!/^[A-Z0-9]{1,16}$/.test(code)||name.length<2||name.length>100||!category||category.length>50||!Number.isFinite(price)||price<=0)return bad("请填写有效代码、名称、类别和大于 0 的模拟价格");
    if(await env.DB.prepare("SELECT code FROM Stocks WHERE code=?").bind(code).first())return bad(`股票代码 ${code} 已存在，请换一个代码`,409);
    if(await env.DB.prepare("SELECT code FROM Stocks WHERE lower(name)=lower(?)").bind(name).first())return bad(`股票名称“${name}”已存在，请换一个名称`,409);
    try{
      await env.DB.batch([env.DB.prepare("INSERT INTO Stocks(code,name,price,change_pct,category,is_custom) VALUES(?,?,?,0,?,1)").bind(code,name,Number(price.toFixed(2)),category),env.DB.prepare("INSERT INTO MarketTicks(code,price) VALUES(?,?)").bind(code,Number(price.toFixed(2)))]);
    }catch{return bad("该股票代码已经存在或无法保存",409)}
    return json({ok:true,stock:{code,name,price:Number(price.toFixed(2)),category,is_custom:1}});
  }
  if (path === "/api/admin/stocks/delete" && request.method === "POST") {
    if(!uid)return bad("请先登录",401);
    const admin=await env.DB.prepare("SELECT is_admin FROM Users WHERE id=?").bind(uid).first();
    if(!admin?.is_admin)return bad("仅管理员可以删除模拟股票",403);
    const code=String(body.code||"").trim().toUpperCase();
    const stock=await env.DB.prepare("SELECT code,is_custom FROM Stocks WHERE code=?").bind(code).first();
    if(!stock)return bad("没有找到该股票代码",404);
    if(!stock.is_custom)return bad("系统预置股票不能删除",400);
    if(await env.DB.prepare("SELECT id FROM Orders WHERE code=? LIMIT 1").bind(code).first() || await env.DB.prepare("SELECT user_id FROM Holdings WHERE code=? AND quantity>0 LIMIT 1").bind(code).first())return bad("该股票已有持仓或交易记录，暂不能删除",409);
    await env.DB.batch([env.DB.prepare("DELETE FROM Holdings WHERE code=? AND quantity=0").bind(code),env.DB.prepare("DELETE FROM Stocks WHERE code=?").bind(code)]);
    return json({ok:true,code});
  }
  if (path === "/api/fishing/start" && request.method === "POST") {
    if(!uid)return bad("请先登录",401);
    const cash=(await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first()).cash;
    if(cash>=10000)return bad("虚拟现金需低于 ¥10,000 才能开始练习");
    const day=today();await env.DB.prepare("INSERT OR IGNORE INTO FishingDaily(user_id,day) VALUES(?,?)").bind(uid,day).run();
    await env.DB.prepare("UPDATE FishingDaily SET attempts=attempts+1 WHERE user_id=? AND day=?").bind(uid,day).run();
    const row=await env.DB.prepare("SELECT attempts,earned FROM FishingDaily WHERE user_id=? AND day=?").bind(uid,day).first();
    return json({ok:true,attempts:row.attempts,earned:row.earned});
  }
  if (path === "/api/trade" && request.method === "POST") {
    const {code,side,quantity:qty}=body;
    if (!["买入","卖出"].includes(side)||!Number.isInteger(qty)||qty<100||qty%100) return bad("请输入 100 股起、100 股整数倍的数量");
    const stock=await env.DB.prepare("SELECT price FROM Stocks WHERE code=?").bind(code).first(); if(!stock)return bad("没有找到该模拟股票",404);
    const account=await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first(),holding=await env.DB.prepare("SELECT quantity,avg_cost FROM Holdings WHERE user_id=? AND code=?").bind(uid,code).first(),price=Number(stock.price),cost=money(price*qty);
    if(side==="买入"){
      if(cost>account.cash)return bad("模拟现金不足。请留意仓位和风险，不要借钱交易。");
      const old=holding?.quantity||0,avg=money(((old*(holding?.avg_cost||0)+cost)/(old+qty)));
      await env.DB.batch([env.DB.prepare("UPDATE Accounts SET cash=cash-? WHERE user_id=?").bind(cost,uid),env.DB.prepare("INSERT INTO Holdings(user_id,code,quantity,avg_cost) VALUES(?,?,?,?) ON CONFLICT(user_id,code) DO UPDATE SET quantity=quantity+excluded.quantity,avg_cost=?").bind(uid,code,qty,price,avg),env.DB.prepare("INSERT INTO Orders(user_id,code,side,quantity,price) VALUES(?,?,?,?,?)").bind(uid,code,side,qty,price)]);
    }else{
      if(!holding||holding.quantity<qty)return bad("持仓数量不足，不能卖出不存在的股票");
      await env.DB.batch([env.DB.prepare("UPDATE Accounts SET cash=cash+? WHERE user_id=?").bind(cost,uid),env.DB.prepare("UPDATE Holdings SET quantity=quantity-? WHERE user_id=? AND code=?").bind(qty,uid,code),env.DB.prepare("INSERT INTO Orders(user_id,code,side,quantity,price) VALUES(?,?,?,?,?)").bind(uid,code,side,qty,price)]);
    }
    return json({ok:true,price});
  }
  if (path === "/api/fishing/catch" && request.method === "POST") {
    const score=Math.max(0,Math.min(100,Number(body.score)||0)),cash=(await env.DB.prepare("SELECT cash FROM Accounts WHERE user_id=?").bind(uid).first()).cash;
    if(cash>=10000)return bad("余额高于 ¥10,000，恢复小游戏暂未开放。请先暂停交易并复盘。");
    const day=today();await env.DB.prepare("INSERT OR IGNORE INTO FishingDaily(user_id,day) VALUES(?,?)").bind(uid,day).run();
    const daily=await env.DB.prepare("SELECT attempts FROM FishingDaily WHERE user_id=? AND day=?").bind(uid,day).first();
    const reward=score>=90?1000:score>=70?500:score>=45?200:50;
    await env.DB.batch([env.DB.prepare("UPDATE Accounts SET cash=cash+? WHERE user_id=?").bind(reward,uid),env.DB.prepare("UPDATE FishingDaily SET earned=earned+? WHERE user_id=? AND day=?").bind(reward,uid,day)]);
    return json({ok:true,reward,score,attempts:daily.attempts,daily_limit:null});
  }
  if (path === "/api/reset" && request.method === "POST") {
    await env.DB.batch([env.DB.prepare("UPDATE Accounts SET cash=100000 WHERE user_id=?").bind(uid),env.DB.prepare("DELETE FROM Holdings WHERE user_id=?").bind(uid),env.DB.prepare("DELETE FROM Orders WHERE user_id=?").bind(uid)]);return json({ok:true});
  }
  if (path === "/api/recharge" && request.method === "POST") {
    const catalog={points_100:["points","100 积分",100,100,0],points_500:["points","500 积分",500,500,0],points_1000:["points","1000 积分",1000,1000,0],member_7:["member","体验会员 7 天",300,0,7],member_30:["member","体验会员 30 天",1000,0,30],member_90:["member","体验会员 90 天",2000,0,90]},item=catalog[body.package];if(!item)return bad("请选择有效的模拟套餐");
    const [kind,name,cost,points,days]=item,w=await env.DB.prepare("SELECT balance FROM Wallets WHERE user_id=?").bind(uid).first();if(w.balance<cost)return bad("玮元余额不足，模拟订单未扣款");
    const stmts=[env.DB.prepare("UPDATE Wallets SET balance=balance-? WHERE user_id=?").bind(cost,uid)];if(points)stmts.push(env.DB.prepare("UPDATE PointBalances SET points=points+? WHERE user_id=?").bind(points,uid));if(days)stmts.push(env.DB.prepare("UPDATE Memberships SET expires_at=datetime(CASE WHEN expires_at>CURRENT_TIMESTAMP THEN expires_at ELSE CURRENT_TIMESTAMP END,?) WHERE user_id=?").bind(`+${days} days`,uid));stmts.push(env.DB.prepare("INSERT INTO RechargeOrders(user_id,item_type,item_name,wei_amount,points_added,member_days) VALUES(?,?,?,?,?,?)").bind(uid,kind,name,cost,points,days));await env.DB.batch(stmts);return json({ok:true,item_name:name});
  }
  return bad("Not found",404);
}
export async function onRequest(context) {
  try { return await handle(context.request,context.env); }
  catch (e) { return json({error:"服务暂时不可用，请检查 Cloudflare D1 配置"},500); }
}
