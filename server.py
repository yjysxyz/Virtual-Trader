from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import sqlite3, json, secrets, hashlib, os, re, random, time
from decimal import Decimal
from datetime import date, datetime, timedelta
IntegrityError=sqlite3.IntegrityError

ROOT=os.path.dirname(os.path.abspath(__file__)); sessions={};last_market_tick=0
for line in open(os.path.join(ROOT,'.env'),encoding='utf-8') if os.path.exists(os.path.join(ROOT,'.env')) else []:
 line=line.strip()
 if line and not line.startswith('#') and '=' in line:
  key,value=line.split('=',1);os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))
SEED=[('600519','贵州茅台',1688.00,'消费'),('000001','平安银行',11.38,'金融'),('000858','五粮液',142.65,'消费'),('300750','宁德时代',186.32,'新能源'),('601318','中国平安',43.12,'金融'),('002594','比亚迪',241.80,'新能源')]
class Database:
 def __init__(self,connection):self.connection=connection
 def execute(self,sql,params=()):
  adapted=tuple(float(x) if isinstance(x,Decimal) else x for x in params)
  return self.connection.execute(sql,adapted)
 def commit(self):self.connection.commit()
 def rollback(self):self.connection.rollback()
 def __del__(self):
  try:self.connection.close()
  except:pass
def db():
 path=os.getenv('SQLITE_PATH',os.path.join(ROOT,'market_sim.db'))
 if not os.path.isabs(path):path=os.path.join(ROOT,path)
 conn=sqlite3.connect(path,timeout=15)
 conn.row_factory=sqlite3.Row
 conn.execute('PRAGMA foreign_keys=ON')
 conn.execute('PRAGMA busy_timeout=15000')
 return Database(conn)
def init_db():
 c=db()
 for statement in [
  "CREATE TABLE IF NOT EXISTS Users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,is_admin INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
  "CREATE TABLE IF NOT EXISTS Stocks(code TEXT PRIMARY KEY,name TEXT NOT NULL,price NUMERIC NOT NULL,change_pct NUMERIC NOT NULL DEFAULT 0,category TEXT NOT NULL,is_custom INTEGER NOT NULL DEFAULT 0)",
  "CREATE TABLE IF NOT EXISTS Accounts(user_id INTEGER PRIMARY KEY,cash NUMERIC NOT NULL DEFAULT 100000,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
  "CREATE TABLE IF NOT EXISTS Holdings(user_id INTEGER NOT NULL,code TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,avg_cost NUMERIC NOT NULL DEFAULT 0,PRIMARY KEY(user_id,code),FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE,FOREIGN KEY(code) REFERENCES Stocks(code))",
  "CREATE TABLE IF NOT EXISTS Orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,code TEXT NOT NULL,side TEXT NOT NULL,quantity INTEGER NOT NULL,price NUMERIC NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE,FOREIGN KEY(code) REFERENCES Stocks(code))",
  "CREATE TABLE IF NOT EXISTS Wallets(user_id INTEGER PRIMARY KEY,balance NUMERIC NOT NULL DEFAULT 2000,FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
  "CREATE TABLE IF NOT EXISTS PointBalances(user_id INTEGER PRIMARY KEY,points INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
  "CREATE TABLE IF NOT EXISTS Memberships(user_id INTEGER PRIMARY KEY,expires_at TEXT,FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
  "CREATE TABLE IF NOT EXISTS RechargeOrders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_type TEXT NOT NULL,item_name TEXT NOT NULL,wei_amount NUMERIC NOT NULL,points_added INTEGER NOT NULL DEFAULT 0,member_days INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
  "CREATE TABLE IF NOT EXISTS WalletTopupClaims(user_id INTEGER NOT NULL,claim_day TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(user_id,claim_day),FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
  "CREATE TABLE IF NOT EXISTS MarketTicks(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT NOT NULL,price NUMERIC NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(code) REFERENCES Stocks(code) ON DELETE CASCADE)",
  "CREATE TABLE IF NOT EXISTS FishingDaily(user_id INTEGER NOT NULL,day TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,earned NUMERIC NOT NULL DEFAULT 0,PRIMARY KEY(user_id,day),FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
  "CREATE TABLE IF NOT EXISTS FarmPlots(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,crop INTEGER NOT NULL,planted_at TEXT NOT NULL,watered INTEGER NOT NULL DEFAULT 0,fertilized INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)"]:
  c.execute(statement)
 user_columns={r['name'] for r in c.execute('PRAGMA table_info(Users)').fetchall()}
 if 'is_admin' not in user_columns:c.execute('ALTER TABLE Users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')
 stock_columns={r['name'] for r in c.execute('PRAGMA table_info(Stocks)').fetchall()}
 if 'is_custom' not in stock_columns:c.execute('ALTER TABLE Stocks ADD COLUMN is_custom INTEGER NOT NULL DEFAULT 0')
 c.execute('CREATE INDEX IF NOT EXISTS idx_market_ticks_code_id ON MarketTicks(code,id)')
 for code,name,price,cat in SEED:c.execute('INSERT OR IGNORE INTO Stocks(code,name,price,change_pct,category) VALUES(?,?,?,0,?)',(code,name,price,cat))
 c.execute('UPDATE Stocks SET is_custom=1 WHERE code NOT IN (?,?,?,?,?,?)',tuple(x[0] for x in SEED))
 for code,_,_,_ in SEED:
  if not c.execute('SELECT id FROM MarketTicks WHERE code=? LIMIT 1',(code,)).fetchone():
   stock=c.execute('SELECT price FROM Stocks WHERE code=?',(code,)).fetchone();c.execute('INSERT INTO MarketTicks(code,price) VALUES(?,?)',(code,stock['price']))
 c.execute('INSERT OR IGNORE INTO Accounts(user_id,cash) SELECT id,100000 FROM Users')
 c.execute('INSERT OR IGNORE INTO Wallets(user_id,balance) SELECT id,2000 FROM Users')
 c.execute('INSERT OR IGNORE INTO PointBalances(user_id,points) SELECT id,0 FROM Users')
 c.execute('INSERT OR IGNORE INTO Memberships(user_id,expires_at) SELECT id,NULL FROM Users')
 c.commit();return c
def hashpw(p):
 s=secrets.token_hex(16);return s+'$'+hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),120000).hex()
def validpw(p,h):
 try:s,v=h.split('$',1);return secrets.compare_digest(hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),120000).hex(),v)
 except ValueError:return False
class App(SimpleHTTPRequestHandler):
 def __init__(self,*a,**k):super().__init__(*a,directory=ROOT,**k)
 def log_message(self,*a):pass
 def json(self,d,status=200):
  def encode(value):
   if isinstance(value,Decimal):return float(value)
   if isinstance(value,datetime):return value.isoformat(sep=' ')
   if isinstance(value,date):return value.isoformat()
   raise TypeError(f'Unsupported JSON type: {type(value).__name__}')
  b=json.dumps(d,ensure_ascii=False,default=encode).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def body(self):
  try:return json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
  except:return {}
 def session_token(self):
  auth=self.headers.get('Authorization','')
  if auth.startswith('Bearer '):return auth[7:].strip()
  for part in self.headers.get('Cookie','').split(';'):
   key,sep,value=part.strip().partition('=')
   if sep and key=='sid':return value
  return ''
 def user(self):return sessions.get(self.session_token())
 def do_GET(self):
  path=urlparse(self.path).path;c=db();uid=self.user()
  if not path.startswith('/api/'):
   return super().do_GET()
  if path=='/api/me':
   u=c.execute('SELECT id,username,email,created_at,is_admin FROM Users WHERE id=?',(uid,)).fetchone() if uid else None
   if not u:self.json(None);return
   a=c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone();self.json({**dict(u),'cash':a['cash']});return
  if path=='/api/wallet':
   if not uid:self.json({'error':'请先登录'},401);return
   wallet=c.execute('SELECT balance FROM Wallets WHERE user_id=?',(uid,)).fetchone();points=c.execute('SELECT points FROM PointBalances WHERE user_id=?',(uid,)).fetchone();member=c.execute('SELECT expires_at FROM Memberships WHERE user_id=?',(uid,)).fetchone();history=c.execute('SELECT item_type,item_name,wei_amount,points_added,member_days,created_at FROM RechargeOrders WHERE user_id=? ORDER BY id DESC LIMIT 10',(uid,)).fetchall();claimed=c.execute('SELECT 1 FROM WalletTopupClaims WHERE user_id=? AND claim_day=?',(uid,date.today())).fetchone()
   self.json({'balance':wallet['balance'],'points':points['points'],'member_expires_at':member['expires_at'],'topup_available':not bool(claimed),'history':[dict(x) for x in history]});return
  if path=='/api/stocks':
   global last_market_tick
   now=time.time()
   if now-last_market_tick>30:
    for item in c.execute('SELECT code,price FROM Stocks').fetchall():
     pct=Decimal(str(random.uniform(-3.5,3.5))).quantize(Decimal('0.0001'));current_price=Decimal(str(item['price']));new_price=(current_price*(Decimal('1')+pct/Decimal('100'))).quantize(Decimal('0.01'));c.execute('UPDATE Stocks SET price=?,change_pct=? WHERE code=?',(max(Decimal('0.50'),new_price),pct,item['code']))
     c.execute('INSERT INTO MarketTicks(code,price) VALUES(?,?)',(item['code'],max(Decimal('0.50'),new_price)))
    c.commit();last_market_tick=now
   term=parse_qs(urlparse(self.path).query).get('q',[''])[0].lower();rows=c.execute('SELECT * FROM Stocks WHERE code LIKE ? OR name LIKE ? ORDER BY code',('%'+term+'%','%'+term+'%')).fetchall();self.json([dict(r) for r in rows]);return
  if path.startswith('/api/stocks/') and path.endswith('/history'):
   code=path.split('/')[-2];rows=c.execute('SELECT price,created_at FROM MarketTicks WHERE code=? ORDER BY id DESC LIMIT 80',(code,)).fetchall();self.json([dict(r) for r in reversed(rows)]);return
  if path=='/api/fishing/status':
   if not uid:self.json({'error':'请先登录'},401);return
   cash=c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone()['cash'];today=date.today();row=c.execute('SELECT attempts,earned FROM FishingDaily WHERE user_id=? AND day=?',(uid,today)).fetchone();self.json({'unlocked':Decimal(str(cash))<Decimal('10000'),'cash':cash,'attempts':row['attempts'] if row else 0,'earned':row['earned'] if row else 0,'daily_limit':None});return
  if path=='/api/farm/status':
   if not uid:self.json({'error':'请先登录'},401);return
   cash=c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone()['cash'];self.json({'unlocked':Decimal(str(cash))<Decimal('10000'),'cash':cash});return
  if path=='/api/farm':
   if not uid:self.json({'error':'请先登录'},401);return
   cash=c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone()['cash']
   if Decimal(str(cash))>=Decimal('10000'):self.json({'error':'虚拟资金需低于 ¥10,000 才能进入方块农场'},403);return
   plots=[]
   for p in c.execute('SELECT * FROM FarmPlots WHERE user_id=? ORDER BY id',(uid,)).fetchall():
    grow=60-(20 if p['watered'] else 0);elapsed=(datetime.now()-datetime.fromisoformat(p['planted_at'])).total_seconds();ready=elapsed>=grow;names=['小麦','胡萝卜','南瓜'];plots.append({'id':p['id'],'name':names[p['crop']],'kind':'wheat' if p['crop']==0 else 'carrot','emoji':['🌾','🥕','🎃'][p['crop']],'ready':ready,'rare':False,'watered':p['watered'],'fertilized':p['fertilized'],'remaining':max(0,int(grow-elapsed))})
   u=c.execute('SELECT username FROM Users WHERE id=?',(uid,)).fetchone();self.json({'cash':cash,'username':u['username'],'plots':plots});return
  if path=='/api/portfolio':
   if not uid:self.json({'error':'请先登录'},401);return
   cash=c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone()['cash'];rows=c.execute('SELECT h.code,s.name,h.quantity,h.avg_cost,s.price FROM Holdings h JOIN Stocks s ON s.code=h.code WHERE h.user_id=? AND h.quantity>0',(uid,)).fetchall();positions=[{**dict(r),'market_value':r['quantity']*r['price'],'profit':(r['price']-r['avg_cost'])*r['quantity']} for r in rows];self.json({'cash':cash,'positions':positions,'market_value':sum(x['market_value'] for x in positions),'profit':sum(x['profit'] for x in positions)});return
  if path=='/api/orders':
   if not uid:self.json({'error':'请先登录'},401);return
   self.json([dict(r) for r in c.execute('SELECT o.*,s.name FROM Orders o JOIN Stocks s ON s.code=o.code WHERE user_id=? ORDER BY id DESC LIMIT 30',(uid,)).fetchall()]);return
  self.json({'error':'Not found'},404)
 def do_POST(self):
  path=urlparse(self.path).path;d=self.body();c=db()
  if path in ('/api/register','/api/login'):
   name=str(d.get('username','')).strip();email=str(d.get('email','')).strip().lower();pw=str(d.get('password',''))
   if path.endswith('register'):
    if len(name)<2 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or len(pw)<6:self.json({'error':'请填写有效用户名、邮箱，密码至少 6 位'},400);return
    try:cur=c.execute('INSERT INTO Users(username,email,password) VALUES(?,?,?)',(name,email,hashpw(pw)));uid=cur.lastrowid;c.execute('INSERT INTO Accounts(user_id,cash) VALUES(?,100000)',(uid,));c.execute('INSERT INTO Wallets(user_id,balance) VALUES(?,2000)',(uid,));c.execute('INSERT INTO PointBalances(user_id,points) VALUES(?,0)',(uid,));c.execute('INSERT INTO Memberships(user_id,expires_at) VALUES(?,NULL)',(uid,));c.commit()
    except IntegrityError:self.json({'error':'用户名或邮箱已注册'},409);return
   else:
    row=c.execute('SELECT id,password FROM Users WHERE username=? OR email=?',(name,name.lower())).fetchone()
    if not row or not validpw(pw,row['password']):self.json({'error':'账号或密码错误'},401);return
    uid=row['id']
   t=secrets.token_urlsafe(32);sessions[t]=uid;payload=json.dumps({'ok':True,'token':t}).encode();self.send_response(200);self.send_header('Set-Cookie',f'sid={t}; HttpOnly; SameSite=Lax; Path=/');self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
  if path=='/api/logout':
   sessions.pop(self.session_token(),None);self.send_response(200);self.send_header('Set-Cookie','sid=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax');self.end_headers();return
  if path.startswith('/api/farm/'):
   uid=self.user()
   if not uid:self.json({'error':'请先登录'},401);return
   cash=c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone()['cash']
   if Decimal(str(cash))>=Decimal('10000'):self.json({'error':'虚拟资金需低于 ¥10,000 才能使用农场'},403);return
   action=path.rsplit('/',1)[-1];pid=d.get('id')
   if action=='plant':
    crop=d.get('crop');prices=[80,120,180]
    if not isinstance(crop,int) or crop not in range(3):self.json({'error':'种子无效'},400);return
    cost=prices[crop];cash=c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone()['cash']
    if cash<cost:self.json({'error':'虚拟资金不足'},400);return
    c.execute('UPDATE Accounts SET cash=cash-? WHERE user_id=?',(cost,uid));c.execute('INSERT INTO FarmPlots(user_id,crop,planted_at) VALUES(?,?,?)',(uid,crop,datetime.now().isoformat()));c.commit();self.json({'ok':True});return
   if action in ('water','fertilize'):
    col='watered' if action=='water' else 'fertilized';c.execute(f'UPDATE FarmPlots SET {col}=1 WHERE id=? AND user_id=?',(pid,uid));c.commit();self.json({'ok':True});return
   if action=='harvest':
    p=c.execute('SELECT * FROM FarmPlots WHERE id=? AND user_id=?',(pid,uid)).fetchone()
    if not p:self.json({'error':'地块不存在'},404);return
    grow=60-(20 if p['watered'] else 0)
    if (datetime.now()-datetime.fromisoformat(p['planted_at'])).total_seconds()<grow:self.json({'error':'作物尚未成熟，请稍候'},400);return
    crop=random.choices([0,1,2],[45,35,20])[0];rare=crop<2 and random.random()<(0.35 if p['fertilized'] else 0.08);reward=[160,240,360][crop]*(5 if rare else 1);name=('金小麦' if crop==0 else '金胡萝卜') if rare else ['小麦','胡萝卜','南瓜'][crop];c.execute('UPDATE Accounts SET cash=cash+? WHERE user_id=?',(reward,uid));c.execute('DELETE FROM FarmPlots WHERE id=?',(pid,));c.commit();self.json({'ok':True,'reward':reward,'rare':rare,'name':name});return
  if path=='/api/wallet/topup':
   uid=self.user()
   if not uid:self.json({'error':'请先登录后领取模拟玮元'},401);return
   today=date.today()
   try:
    c.execute('INSERT INTO WalletTopupClaims(user_id,claim_day) VALUES(?,?)',(uid,today))
    c.execute('UPDATE Wallets SET balance=balance+500 WHERE user_id=?',(uid,))
    c.execute("INSERT INTO RechargeOrders(user_id,item_type,item_name,wei_amount,points_added,member_days) VALUES(?,'wei','每日练习补给',500,0,0)",(uid,))
    c.commit()
   except IntegrityError:
    c.connection.rollback();self.json({'error':'今天的虚拟玮元补给已领取，请明天再来'},409);return
   self.json({'ok':True,'item_name':'每日练习补给','amount':500});return
  if path=='/api/recharge':
   uid=self.user()
   if not uid:self.json({'error':'请先登录后使用玮元'},401);return
   catalog={'points_100':('points','100 积分',100,100,0),'points_500':('points','500 积分',500,500,0),'points_1000':('points','1000 积分',1000,1000,0),'member_7':('member','体验会员 7 天',300,0,7),'member_30':('member','体验会员 30 天',1000,0,30),'member_90':('member','体验会员 90 天',2000,0,90)}
   item=catalog.get(d.get('package'))
   if not item:self.json({'error':'请选择有效的模拟套餐'},400);return
   item_type,item_name,cost,points_added,days=item
   try:
    wallet=c.execute('SELECT balance FROM Wallets WHERE user_id=?',(uid,)).fetchone()
    if not wallet or wallet['balance']<cost:c.connection.rollback();self.json({'error':'玮元余额不足，模拟订单未扣款'},400);return
    c.execute('UPDATE Wallets SET balance=balance-? WHERE user_id=?',(cost,uid))
    if points_added:c.execute('UPDATE PointBalances SET points=points+? WHERE user_id=?',(points_added,uid))
    if days:
     member=c.execute('SELECT expires_at FROM Memberships WHERE user_id=?',(uid,)).fetchone();expires_at=datetime.fromisoformat(member['expires_at']) if member and member['expires_at'] else None;start=expires_at if expires_at and expires_at>datetime.now() else datetime.now();expires=start+timedelta(days=days);c.execute('UPDATE Memberships SET expires_at=? WHERE user_id=?',(expires,uid))
    c.execute('INSERT INTO RechargeOrders(user_id,item_type,item_name,wei_amount,points_added,member_days) VALUES(?,?,?,?,?,?)',(uid,item_type,item_name,cost,points_added,days));c.commit()
   except Exception:
    c.connection.rollback();raise
   self.json({'ok':True,'item_name':item_name});return
  if path=='/api/trade':
   uid=self.user()
   if not uid:self.json({'error':'请先登录后开始模拟交易'},401);return
   code=str(d.get('code',''));side=d.get('side');qty=d.get('quantity')
   if side not in ('买入','卖出') or not isinstance(qty,int) or qty<100 or qty%100:self.json({'error':'请输入 100 股起、100 股整数倍的数量'},400);return
   stock=c.execute('SELECT price FROM Stocks WHERE code=?',(code,)).fetchone()
   if not stock:self.json({'error':'没有找到该模拟股票'},404);return
   price=Decimal(str(stock['price']));cash=Decimal(str(c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone()['cash']));h=c.execute('SELECT quantity,avg_cost FROM Holdings WHERE user_id=? AND code=?',(uid,code)).fetchone()
   if side=='买入':
    cost=price*qty
    if cost>cash:self.json({'error':'模拟现金不足。请留意仓位和风险，不要借钱交易。'},400);return
    old=h['quantity'] if h else 0;avg=Decimal(str(h['avg_cost'])) if h else Decimal('0.00');new_avg=((Decimal(old)*avg+cost)/Decimal(old+qty)).quantize(Decimal('0.01'));c.execute('UPDATE Accounts SET cash=cash-? WHERE user_id=?',(cost,uid));c.execute('INSERT INTO Holdings(user_id,code,quantity,avg_cost) VALUES(?,?,?,?) ON CONFLICT(user_id,code) DO UPDATE SET quantity=quantity+excluded.quantity,avg_cost=?',(uid,code,qty,price,new_avg))
   else:
    if not h or h['quantity']<qty:self.json({'error':'持仓数量不足，不能卖出不存在的股票'},400);return
    c.execute('UPDATE Accounts SET cash=cash+? WHERE user_id=?',(price*qty,uid));c.execute('UPDATE Holdings SET quantity=quantity-? WHERE user_id=? AND code=?',(qty,uid,code))
   c.execute('INSERT INTO Orders(user_id,code,side,quantity,price) VALUES(?,?,?,?,?)',(uid,code,side,qty,price));c.commit();self.json({'ok':True,'price':price});return
  if path=='/api/reset':
   uid=self.user()
   if not uid:self.json({'error':'请先登录'},401);return
   c.execute('UPDATE Accounts SET cash=100000 WHERE user_id=?',(uid,));c.execute('DELETE FROM Holdings WHERE user_id=?',(uid,));c.execute('DELETE FROM Orders WHERE user_id=?',(uid,));c.commit();self.json({'ok':True});return
  if path=='/api/admin/stocks':
   uid=self.user();admin=c.execute('SELECT is_admin FROM Users WHERE id=?',(uid,)).fetchone() if uid else None
   if not admin or not admin['is_admin']:self.json({'error':'仅管理员可以添加模拟股票'},403);return
   code=str(d.get('code','')).strip().upper();name=str(d.get('name','')).strip();category=str(d.get('category','')).strip();
   try:price=Decimal(str(d.get('price',''))).quantize(Decimal('0.01'))
   except Exception:price=Decimal('0')
   if not re.fullmatch(r'[A-Z0-9]{1,16}',code) or len(name)<2 or len(name)>100 or not category or len(category)>50 or price<=0:self.json({'error':'请填写有效代码、名称、类别和大于 0 的模拟价格'},400);return
   if c.execute('SELECT code FROM Stocks WHERE code=?',(code,)).fetchone():self.json({'error':f'股票代码 {code} 已存在，请换一个代码'},409);return
   if c.execute('SELECT code FROM Stocks WHERE LOWER(name)=LOWER(?)',(name,)).fetchone():self.json({'error':f'股票名称“{name}”已存在，请换一个名称'},409);return
   try:c.execute('INSERT INTO Stocks(code,name,price,change_pct,category,is_custom) VALUES(?,?,?,0,?,1)',(code,name,price,category));c.execute('INSERT INTO MarketTicks(code,price) VALUES(?,?)',(code,price));c.commit()
   except IntegrityError:c.connection.rollback();self.json({'error':'该股票代码已经存在'},409);return
   self.json({'ok':True,'stock':{'code':code,'name':name,'price':price,'category':category}});return
  if path=='/api/admin/stocks/delete':
   uid=self.user();admin=c.execute('SELECT is_admin FROM Users WHERE id=?',(uid,)).fetchone() if uid else None
   if not admin or not admin['is_admin']:self.json({'error':'仅管理员可以删除模拟股票'},403);return
   code=str(d.get('code','')).strip().upper();stock=c.execute('SELECT code,is_custom FROM Stocks WHERE code=?',(code,)).fetchone()
   if not stock:self.json({'error':'没有找到该股票代码'},404);return
   if not stock['is_custom']:self.json({'error':'系统预置股票不能删除'},400);return
   if c.execute('SELECT id FROM Orders WHERE code=? LIMIT 1',(code,)).fetchone() or c.execute('SELECT user_id FROM Holdings WHERE code=? AND quantity>0 LIMIT 1',(code,)).fetchone():self.json({'error':'该股票已有持仓或交易记录，暂不能删除'},409);return
   c.execute('DELETE FROM Holdings WHERE code=? AND quantity=0',(code,));c.execute('DELETE FROM Stocks WHERE code=?',(code,));c.commit();self.json({'ok':True,'code':code});return
  if path=='/api/fishing/start':
   uid=self.user()
   if not uid:self.json({'error':'请先登录'},401);return
   cash=Decimal(str(c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone()['cash']))
   if cash>=Decimal('10000'):self.json({'error':'虚拟现金需低于 ¥10,000 才能开始练习'},400);return
   today=date.today();c.execute('INSERT OR IGNORE INTO FishingDaily(user_id,day) VALUES(?,?)',(uid,today));c.execute('UPDATE FishingDaily SET attempts=attempts+1 WHERE user_id=? AND day=?',(uid,today));c.commit()
   row=c.execute('SELECT attempts,earned FROM FishingDaily WHERE user_id=? AND day=?',(uid,today)).fetchone();self.json({'ok':True,'attempts':row['attempts'],'earned':row['earned']});return
  if path=='/api/fishing/catch':
   uid=self.user()
   if not uid:self.json({'error':'请先登录'},401);return
   try:score=max(0,min(100,int(d.get('score',0))))
   except (TypeError,ValueError):score=0
   cash=c.execute('SELECT cash FROM Accounts WHERE user_id=?',(uid,)).fetchone()['cash']
   if Decimal(str(cash))>=Decimal('10000'):self.json({'error':'余额高于 ¥10,000，恢复小游戏暂未开放。请先暂停交易并复盘。'},400);return
   today=date.today();c.execute('INSERT OR IGNORE INTO FishingDaily(user_id,day) VALUES(?,?)',(uid,today));daily=c.execute('SELECT attempts,earned FROM FishingDaily WHERE user_id=? AND day=?',(uid,today)).fetchone()
   fish=random.choices([('溪流鲦','🐟'),('银鳞鲑','🐠'),('锦鲤','🎏'),('金色锦鲤','🐡'),('钻石鱼王','💎🐟')],[60,23,12,4,1])[0];reward=(1000 if score>=90 else 500 if score>=70 else 200 if score>=45 else 50)*(5 if fish[0]=='钻石鱼王' else 2 if fish[0]=='金色锦鲤' else 1)
   c.execute('UPDATE Accounts SET cash=cash+? WHERE user_id=?',(reward,uid));c.execute('UPDATE FishingDaily SET earned=earned+? WHERE user_id=? AND day=?',(reward,uid,today));c.commit();self.json({'ok':True,'reward':reward,'score':score,'fish':{'name':fish[0],'emoji':fish[1]},'attempts':daily['attempts'],'daily_limit':None});return
  self.json({'error':'Not found'},404)
if __name__=='__main__':
 init_db();print('模拟股市风险教育站运行于 http://localhost:8000');ThreadingHTTPServer(('127.0.0.1',8000),App).serve_forever()
