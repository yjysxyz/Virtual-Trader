"""虚拟交易 · 模拟市场 —— 本地 Python + SQLite 后端。

仅用于课程演示：行情、资金、玮元、积分与会员全部为虚构数据，
没有真实行情、真实支付或现金兑换。

启动：
    py server.py

可选环境变量（也可写在 .env 中）：
    SQLITE_PATH  数据库文件，默认 market_sim.db
    HOST         监听地址，默认 127.0.0.1（仅本机可访问）
    PORT         监听端口，默认 8000
"""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import html
import json
import math
import os
import random
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
IntegrityError = sqlite3.IntegrityError

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_CASH = Decimal('100000')
DEFAULT_WEI = Decimal('2000')
FISHING_UNLOCK_CASH = Decimal('10000')
GAME_UNLOCK_CASH = FISHING_UNLOCK_CASH
MINING_BOMB_CHANCE = 0.05
MINING_DIAMOND_CHANCE = 0.02
MINING_GOLD_CHANCE = 0.08
MINING_IRON_CHANCE = 0.15
MINING_COAL_CHANCE = 0.20
MINING_SOIL_REWARD = (10, 30)
MINING_COAL_REWARD = (40, 90)
MINING_IRON_REWARD = (90, 180)
MINING_GOLD_REWARD = (200, 600)
MINING_DIAMOND_REWARD = (600, 1200)
FORGE_BASE_SHATTER = 0.01
FORGE_MAX_SHATTER = 0.60
FORGE_RISK_TARGET_HITS = 70
FORGE_GRADES = ('平凡的剑', '优秀的剑', '精湛的剑', '史诗的剑', '传奇的剑')
FORGE_GRADE_THRESHOLDS = (11, 26, 46, 71)
FORGE_HIT_REWARD = (25, 60)
POINT_CASH_RATE = 10
ONLINE_WINDOW_SECONDS = 120
TICKS_KEPT = 300
SESSION_DAYS = 7
PBKDF2_ITERATIONS = 100_000       # 与 Cloudflare Workers 的上限保持一致
PBKDF2_LEGACY_ITERATIONS = (100_000, 120_000)
HASH_PREFIX = 'pbkdf2_sha256'

SEED_STOCKS = (
    ('600519', '贵州茅台', 1688.00, '消费'),
    ('000001', '平安银行', 11.38, '金融'),
    ('000858', '五粮液', 142.65, '消费'),
    ('300750', '宁德时代', 186.32, '新能源'),
    ('601318', '中国平安', 43.12, '金融'),
    ('002594', '比亚迪', 241.80, '新能源'),
)

# 只对外提供这些前端文件，其余路径一律 404。
# 这样可以避免把 .env、*.db、server.py、.git、.wrangler 等敏感文件当成静态资源下发。
STATIC_FILES = frozenset({
    'index.html', 'app.js', 'style.css', 'recharge.css',
    'mining.html', 'mining.js', 'mining.css',
    'forge.html', 'forge.js', 'forge.css',
    'fishing.html', 'fishing.js', 'fishing.css',
})

CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

USERNAME_RE = re.compile(r'^[^\s<>@]{2,32}$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
UNSAFE_TEXT_RE = re.compile(r'[<>\x00-\x1f\x7f]')

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS Users(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "username TEXT UNIQUE NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,"
    "is_admin INTEGER NOT NULL DEFAULT 0,is_banned INTEGER NOT NULL DEFAULT 0,"
    "last_seen_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS Stocks(code TEXT PRIMARY KEY,name TEXT NOT NULL,"
    "price NUMERIC NOT NULL,change_pct NUMERIC NOT NULL DEFAULT 0,category TEXT NOT NULL,"
    "is_custom INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS Accounts(user_id INTEGER PRIMARY KEY,"
    "cash NUMERIC NOT NULL DEFAULT 100000,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    "FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS Holdings(user_id INTEGER NOT NULL,code TEXT NOT NULL,"
    "quantity INTEGER NOT NULL DEFAULT 0,avg_cost NUMERIC NOT NULL DEFAULT 0,"
    "PRIMARY KEY(user_id,code),FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE,"
    "FOREIGN KEY(code) REFERENCES Stocks(code))",
    "CREATE TABLE IF NOT EXISTS Orders(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "user_id INTEGER NOT NULL,code TEXT NOT NULL,side TEXT NOT NULL,quantity INTEGER NOT NULL,"
    "price NUMERIC NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    "FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE,"
    "FOREIGN KEY(code) REFERENCES Stocks(code))",
    "CREATE TABLE IF NOT EXISTS Wallets(user_id INTEGER PRIMARY KEY,"
    "balance NUMERIC NOT NULL DEFAULT 2000,"
    "FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS PointBalances(user_id INTEGER PRIMARY KEY,"
    "points INTEGER NOT NULL DEFAULT 0,"
    "FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS Memberships(user_id INTEGER PRIMARY KEY,expires_at TEXT,"
    "FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS RechargeOrders(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "user_id INTEGER NOT NULL,item_type TEXT NOT NULL,item_name TEXT NOT NULL,"
    "wei_amount NUMERIC NOT NULL,points_added INTEGER NOT NULL DEFAULT 0,"
    "cash_amount NUMERIC NOT NULL DEFAULT 0,"
    "member_days INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    "FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS StockSwaps(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "from_user_id INTEGER NOT NULL,to_user_id INTEGER NOT NULL,"
    "from_code TEXT NOT NULL,from_quantity INTEGER NOT NULL,"
    "to_code TEXT NOT NULL,to_quantity INTEGER NOT NULL,"
    "message TEXT,status TEXT NOT NULL DEFAULT 'pending',"
    "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,responded_at TEXT,"
    "FOREIGN KEY(from_user_id) REFERENCES Users(id) ON DELETE CASCADE,"
    "FOREIGN KEY(to_user_id) REFERENCES Users(id) ON DELETE CASCADE,"
    "FOREIGN KEY(from_code) REFERENCES Stocks(code),"
    "FOREIGN KEY(to_code) REFERENCES Stocks(code))",
    "CREATE TABLE IF NOT EXISTS WalletTopupClaims(user_id INTEGER NOT NULL,"
    "claim_day TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    "PRIMARY KEY(user_id,claim_day),"
    "FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS MarketTicks(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "code TEXT NOT NULL,price NUMERIC NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    "FOREIGN KEY(code) REFERENCES Stocks(code) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS FishingDaily(user_id INTEGER NOT NULL,day TEXT NOT NULL,"
    "attempts INTEGER NOT NULL DEFAULT 0,earned NUMERIC NOT NULL DEFAULT 0,"
    "pending INTEGER NOT NULL DEFAULT 0,started_at TEXT,PRIMARY KEY(user_id,day),"
    "FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS MiningRuns(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "user_id INTEGER NOT NULL,pot NUMERIC NOT NULL DEFAULT 0,digs INTEGER NOT NULL DEFAULT 0,"
    "status TEXT NOT NULL DEFAULT 'active',started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    "ended_at TEXT,FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS ForgeRuns(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "user_id INTEGER NOT NULL,value NUMERIC NOT NULL DEFAULT 0,hits INTEGER NOT NULL DEFAULT 0,"
    "risk NUMERIC NOT NULL DEFAULT 0.01,"
    "status TEXT NOT NULL DEFAULT 'active',started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    "ended_at TEXT,FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS GameStats(user_id INTEGER NOT NULL,game TEXT NOT NULL,"
    "runs INTEGER NOT NULL DEFAULT 0,earned NUMERIC NOT NULL DEFAULT 0,"
    "PRIMARY KEY(user_id,game),FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS EmailCodes(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "email TEXT NOT NULL,code_hash TEXT NOT NULL,ip TEXT,"
    "attempts INTEGER NOT NULL DEFAULT 0,used INTEGER NOT NULL DEFAULT 0,"
    "expires_at TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS Sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,"
    "expires_at TEXT NOT NULL,"
    "FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE)",
)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def load_env():
    """把项目根目录 .env 中的键值对读入环境变量（不覆盖已有变量）。"""
    path = os.path.join(ROOT, '.env')
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def db_path():
    path = os.getenv('SQLITE_PATH', 'market_sim.db').strip() or 'market_sim.db'
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    return path


def utcnow():
    """当前 UTC 时间（naive）。数据库中统一保存 UTC。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcstamp(moment=None):
    return (moment or utcnow()).strftime('%Y-%m-%d %H:%M:%S')


def iso_z(value):
    """把数据库里的 UTC 字符串转成带 Z 的 ISO 8601，交给浏览器按本地时区显示。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return text
    text = text.replace(' ', 'T')
    if text.endswith('Z') or re.search(r'[+-]\d{2}:\d{2}$', text):
        return text
    return text + 'Z'


def parse_utc(value):
    """解析数据库中的 UTC 字符串（兼容空格/T 分隔与微秒）。"""
    text = str(value).strip().replace('Z', '')
    if not text:
        return utcnow()
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=None)


def today_local():
    """每日限制按本机日期计算（中国时区使用体验更自然）。"""
    return date.today().isoformat()


def money(value):
    return float(Decimal(str(value)).quantize(Decimal('0.01')))


def clean_text(value, minimum=1, maximum=100):
    """去除首尾空白，限制长度，并拒绝 HTML 尖括号与控制字符。"""
    text = str(value or '').strip()
    if not minimum <= len(text) <= maximum:
        return None
    if UNSAFE_TEXT_RE.search(text):
        return None
    return text


def normalized_path(path):
    """合并重复斜杠、去掉 ./ 并解析 ..，再交给静态白名单判断。"""
    if not path:
        return '/'
    segments = []
    for segment in path.split('/'):
        if segment in ('', '.'):
            continue
        if segment == '..':
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    return '/' + '/'.join(segments)


def hashpw(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), PBKDF2_ITERATIONS).hex()
    return f'{HASH_PREFIX}${PBKDF2_ITERATIONS}${salt}${digest}'


def validpw(password, stored):
    """校验密码。

    支持三种历史格式，保证本地 SQLite 与 Cloudflare D1 的密码可以互相迁移：
      pbkdf2_sha256$<迭代次数>$<salt>$<hash>   （新格式）
      <salt>$<hash> 且 120000 次              （旧版本地格式）
      <salt>$<hash> 且 100000 次              （旧版 D1 格式）
    """
    parts = str(stored or '').split('$')
    iterations = None
    if len(parts) == 4 and parts[0] == HASH_PREFIX:
        try:
            iterations = int(parts[1])
        except ValueError:
            return False
        salt, expected = parts[2], parts[3]
    elif len(parts) == 2:
        salt, expected = parts
    else:
        return False
    if not salt or not expected:
        return False
    candidates = [iterations] if iterations else list(PBKDF2_LEGACY_ITERATIONS)
    for rounds in candidates:
        try:
            digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), rounds).hex()
        except (ValueError, OverflowError):
            continue
        if secrets.compare_digest(digest, expected):
            return True
    return False


# ---------------------------------------------------------------------------
# 邮箱验证码（Resend）
# ---------------------------------------------------------------------------

EMAIL_CODE_TTL_SECONDS = 300
EMAIL_CODE_RESEND_SECONDS = 60
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODE_MAX_PER_HOUR = 5
EMAIL_CODE_MAX_PER_IP_HOUR = 20
_email_code_secret = None


def email_code_secret():
    global _email_code_secret
    if _email_code_secret is None:
        _email_code_secret = os.getenv('EMAIL_CODE_SECRET', '').strip() or secrets.token_hex(32)
    return _email_code_secret


def normalize_email(value):
    return str(value or '').strip().lower()


def hash_email_code(email, code):
    message = f'{normalize_email(email)}:{code}'.encode('utf-8')
    return hmac.new(email_code_secret().encode('utf-8'), message, hashlib.sha256).hexdigest()


def send_email_code(email, code):
    """通过 Resend 发送验证码；EMAIL_DRY_RUN=1 时只打印到终端，便于本地测试。"""
    if os.getenv('EMAIL_DRY_RUN', '0').strip() == '1':
        print(f'[邮件验证码·dry-run] {email} -> {code}', flush=True)
        return
    api_key = os.getenv('RESEND_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('未配置 RESEND_API_KEY，暂时无法发送验证码')
    sender = os.getenv('MAIL_FROM', '虚拟交易 <no-reply@virtualtrader123.xyz>').strip()
    payload = {
        'from': sender,
        'to': [email],
        'subject': '【虚拟交易】邮箱验证码',
        'text': f'你的验证码是 {code}，5 分钟内有效。\n如果不是你本人操作，请忽略这封邮件。',
    }
    request = urllib.request.Request(
        'https://api.resend.com/emails',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'dubosystem/1.0',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f'邮件服务返回异常状态 {response.status}')
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', 'replace')[:300]
        raise RuntimeError(f'邮件服务返回 {error.code}：{detail}')
    except urllib.error.URLError as error:
        raise RuntimeError(f'无法连接邮件服务：{error.reason}')


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------

class Database:
    """对 sqlite3 连接的薄封装，负责把 Decimal 参数转成 SQLite 可存储的浮点数。"""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        adapted = tuple(float(x) if isinstance(x, Decimal) else x for x in params)
        return self.connection.execute(sql, adapted)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        try:
            self.connection.close()
        except Exception:
            pass


def db():
    connection = sqlite3.connect(db_path(), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=15000')
    return Database(connection)


def ensure_column(c, table, column, ddl):
    columns = {row['name'] for row in c.execute(f'PRAGMA table_info({table})').fetchall()}
    if column not in columns:
        c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')


def forge_grade(hits):
    tier = 0
    for threshold in FORGE_GRADE_THRESHOLDS:
        if hits >= threshold:
            tier += 1
        else:
            break
    return tier, FORGE_GRADES[tier]


def forge_risk(hits):
    progress = min(1.0, max(0, int(hits)) / FORGE_RISK_TARGET_HITS)
    return FORGE_BASE_SHATTER + (FORGE_MAX_SHATTER - FORGE_BASE_SHATTER) * (progress ** 2)


def is_admin_user(c, uid):
    row = c.execute('SELECT is_admin FROM Users WHERE id=?', (uid,)).fetchone()
    return bool(row and row['is_admin'])


def is_online(value):
    if not value:
        return False
    try:
        return (utcnow() - parse_utc(value)).total_seconds() <= ONLINE_WINDOW_SECONDS
    except (TypeError, ValueError):
        return False


def is_member_active(value):
    if not value:
        return False
    try:
        return parse_utc(value) > utcnow()
    except (TypeError, ValueError):
        return False


def move_holding(c, from_uid, to_uid, code, quantity, price):
    """把股票从 from_uid 转到 to_uid；接收方按当前模拟价加权成本。"""
    source = c.execute(
        'SELECT quantity,avg_cost FROM Holdings WHERE user_id=? AND code=?',
        (from_uid, code),
    ).fetchone()
    if not source or source['quantity'] < quantity:
        raise ValueError('发起方持仓不足')
    remaining = source['quantity'] - quantity
    if remaining == 0:
        c.execute('DELETE FROM Holdings WHERE user_id=? AND code=?', (from_uid, code))
    else:
        c.execute(
            'UPDATE Holdings SET quantity=? WHERE user_id=? AND code=?',
            (remaining, from_uid, code),
        )
    target = c.execute(
        'SELECT quantity,avg_cost FROM Holdings WHERE user_id=? AND code=?',
        (to_uid, code),
    ).fetchone()
    if target:
        new_quantity = target['quantity'] + quantity
        total_cost = Decimal(str(target['avg_cost'])) * target['quantity'] + price * quantity
        c.execute(
            'UPDATE Holdings SET quantity=?,avg_cost=? WHERE user_id=? AND code=?',
            (new_quantity, total_cost / new_quantity, to_uid, code),
        )
    else:
        c.execute(
            'INSERT INTO Holdings(user_id,code,quantity,avg_cost) VALUES(?,?,?,?)',
            (to_uid, code, quantity, price),
        )


def init_db():
    """建表、补列、写入种子数据、清理过期会话与过长行情历史。"""
    c = db()
    try:
        for statement in SCHEMA:
            c.execute(statement)
        ensure_column(c, 'Users', 'is_admin', 'INTEGER NOT NULL DEFAULT 0')
        ensure_column(c, 'Users', 'is_banned', 'INTEGER NOT NULL DEFAULT 0')
        ensure_column(c, 'Users', 'last_seen_at', 'TEXT')
        ensure_column(c, 'RechargeOrders', 'cash_amount', 'NUMERIC NOT NULL DEFAULT 0')
        ensure_column(c, 'Stocks', 'is_custom', 'INTEGER NOT NULL DEFAULT 0')
        ensure_column(c, 'FishingDaily', 'pending', 'INTEGER NOT NULL DEFAULT 0')
        ensure_column(c, 'FishingDaily', 'started_at', 'TEXT')
        ensure_column(c, 'ForgeRuns', 'risk', 'REAL NOT NULL DEFAULT 0.01')
        c.execute('CREATE INDEX IF NOT EXISTS idx_market_ticks_code_id ON MarketTicks(code,id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_sessions_expires ON Sessions(expires_at)')

        for code, name, price, category in SEED_STOCKS:
            c.execute(
                'INSERT OR IGNORE INTO Stocks(code,name,price,change_pct,category) VALUES(?,?,?,0,?)',
                (code, name, price, category),
            )
        c.execute(
            'UPDATE Stocks SET is_custom=1 WHERE code NOT IN (?,?,?,?,?,?)',
            tuple(item[0] for item in SEED_STOCKS),
        )
        for code, _name, _price, _category in SEED_STOCKS:
            if not c.execute('SELECT id FROM MarketTicks WHERE code=? LIMIT 1', (code,)).fetchone():
                row = c.execute('SELECT price FROM Stocks WHERE code=?', (code,)).fetchone()
                c.execute('INSERT INTO MarketTicks(code,price) VALUES(?,?)', (code, row['price']))

        # 旧账户缺少钱包/积分/会员行时补一条默认记录（已有余额不会被覆盖）。
        c.execute('INSERT OR IGNORE INTO Accounts(user_id,cash) SELECT id,? FROM Users', (float(DEFAULT_CASH),))
        c.execute('INSERT OR IGNORE INTO Wallets(user_id,balance) SELECT id,? FROM Users', (float(DEFAULT_WEI),))
        c.execute('INSERT OR IGNORE INTO PointBalances(user_id,points) SELECT id,0 FROM Users')
        c.execute('INSERT OR IGNORE INTO Memberships(user_id,expires_at) SELECT id,NULL FROM Users')

        c.execute('DELETE FROM Sessions WHERE expires_at<=?', (utcstamp(),))
        c.execute('DELETE FROM EmailCodes WHERE created_at<?', (utcstamp(utcnow() - timedelta(days=2)),))
        for row in c.execute('SELECT code FROM Stocks').fetchall():
            c.execute(
                'DELETE FROM MarketTicks WHERE code=? AND id NOT IN '
                '(SELECT id FROM MarketTicks WHERE code=? ORDER BY id DESC LIMIT ?)',
                (row['code'], row['code'], TICKS_KEPT),
            )
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# 登录失败限流（进程内，重启即清空）
# ---------------------------------------------------------------------------

class LoginThrottle:
    def __init__(self, limit=10, window=600):
        self.limit = limit
        self.window = window
        self.failures = {}
        self.lock = threading.Lock()

    def _prune(self, now):
        for key in list(self.failures):
            kept = [stamp for stamp in self.failures[key] if now - stamp < self.window]
            if kept:
                self.failures[key] = kept
            else:
                del self.failures[key]

    def blocked(self, key):
        now = time.time()
        with self.lock:
            self._prune(now)
            return len(self.failures.get(key, ())) >= self.limit

    def fail(self, key):
        now = time.time()
        with self.lock:
            self._prune(now)
            self.failures.setdefault(key, []).append(now)

    def clear(self, key):
        with self.lock:
            self.failures.pop(key, None)


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

class App(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    throttle = LoginThrottle()
    _responded = False
    last_market_tick = 0.0
    market_lock = threading.Lock()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    # -- 基础响应 ---------------------------------------------------------

    def version_string(self):
        return 'dubosystem'

    def log_message(self, *args):
        pass

    def handle(self):
        # 浏览器或命令行客户端主动断开 keep-alive 连接时，不打印整段堆栈。
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
        self.send_header('Content-Security-Policy', CSP)
        # 静态页面与脚本每次重新验证，避免改完后浏览器继续用旧缓存。
        if not self._responded:
            self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def send_json(self, payload, status=200, extra_headers=None):
        def encode(value):
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, datetime):
                return value.isoformat(sep=' ')
            if isinstance(value, date):
                return value.isoformat()
            raise TypeError(f'Unsupported JSON type: {type(value).__name__}')

        body = json.dumps(payload, ensure_ascii=False, default=encode).encode('utf-8')
        self._responded = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def internal_error(self):
        traceback.print_exc()
        if self._responded:
            return
        try:
            self.send_json({'error': '服务器内部错误，请稍后重试'}, 500)
        except Exception:
            pass

    def body(self):
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b'{}'
            return json.loads(raw or b'{}')
        except Exception:
            return {}

    # -- 会话 -------------------------------------------------------------

    def session_token(self):
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth[7:].strip()
        for part in self.headers.get('Cookie', '').split(';'):
            key, separator, value = part.strip().partition('=')
            if separator and key == 'sid':
                return value
        return ''

    def user(self, c):
        """返回当前登录用户 id，会话保存在 Sessions 表中（重启服务后仍有效）。"""
        token = self.session_token()
        if not token:
            return None
        row = c.execute(
            'SELECT s.user_id,u.is_banned FROM Sessions s JOIN Users u ON u.id=s.user_id '
            'WHERE s.token=? AND s.expires_at>?',
            (token, utcstamp()),
        ).fetchone()
        if not row or row['is_banned']:
            return None
        return row['user_id']

    def create_session(self, c, uid):
        token = secrets.token_urlsafe(32)
        expires = utcstamp(utcnow() + timedelta(days=SESSION_DAYS))
        c.execute('DELETE FROM Sessions WHERE expires_at<=?', (utcstamp(),))
        c.execute('INSERT INTO Sessions(token,user_id,expires_at) VALUES(?,?,?)', (token, uid, expires))
        return token

    def session_headers(self, token=None):
        if token:
            cookie = f'sid={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_DAYS * 86400}'
        else:
            cookie = 'sid=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax'
        return {'Set-Cookie': cookie}

    # -- 静态文件 ---------------------------------------------------------

    def redirect_to(self, location):
        self._responded = True
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_static_miss(self, path):
        try:
            print(f'[404] {datetime.now().strftime("%H:%M:%S")} {unquote(path)}', file=sys.stderr, flush=True)
        except Exception:
            pass

    def not_found_page(self, path):
        """静态 404 时给出可读提示，并回显请求路径，方便排查错误地址。"""
        safe_path = html.escape(unquote(path))
        body = (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>页面不存在 · 虚拟交易</title></head>'
            '<body style="font-family:system-ui,-apple-system,\'Segoe UI\',sans-serif;'
            'max-width:640px;margin:60px auto;padding:0 20px;line-height:1.8;color:#222d29">'
            '<h1 style="font-size:22px">这个地址不是站点页面</h1>'
            f'<p>请求路径：<code>{safe_path}</code></p>'
            '<p>请从下面的入口访问：</p><ul>'
            '<li><a href="/">模拟市场首页</a></li>'
            '<li><a href="/fishing.html">钓鱼练习</a></li>'
            '<li><a href="/mining.html">矿场挖掘</a></li>'
            '<li><a href="/forge.html">锻剑工坊</a></li>'
            '</ul>'
            '<p style="color:#7f8a84;font-size:13px">说明：本站只提供前端页面与脚本文件。'
            'README、使用说明书、数据库、server.py 等文件不会通过网站提供，'
            '请在本机文件管理器中打开。</p>'
            '</body></html>'
        )
        data = body.encode('utf-8')
        self._responded = True
        self.send_response(404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)
        self.log_static_miss(path)

    def serve_static(self, path):
        if path == '/':
            return super().do_GET()
        if path in ('/index', '/index.htm'):
            self.redirect_to('/')
            return None
        name = path.lstrip('/')
        if name in STATIC_FILES:
            return super().do_GET()
        if path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return None
        self.not_found_page(path)
        return None

    def serve_static_head(self, path):
        if path == '/':
            return super().do_HEAD()
        if path in ('/index', '/index.htm'):
            self.redirect_to('/')
            return None
        name = path.lstrip('/')
        if name in STATIC_FILES:
            return super().do_HEAD()
        if path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return None
        self.not_found_page(path)
        return None

    # -- 路由 -------------------------------------------------------------

    def do_GET(self):
        self._responded = False
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith('/api/'):
            normalized = normalized_path(path)
            if normalized != path:
                self.path = normalized + (('?' + parsed.query) if parsed.query else '')
                path = normalized
            try:
                return self.serve_static(path)
            except Exception:
                self.internal_error()
                return None
        try:
            c = db()
            try:
                self.api_get(path, c)
            finally:
                c.close()
        except Exception:
            self.internal_error()

    def do_HEAD(self):
        self._responded = False
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith('/api/'):
            self.send_error(405)
            return
        normalized = normalized_path(path)
        if normalized != path:
            self.path = normalized + (('?' + parsed.query) if parsed.query else '')
            path = normalized
        try:
            return self.serve_static_head(path)
        except Exception:
            self.internal_error()

    def do_POST(self):
        self._responded = False
        path = urlparse(self.path).path
        try:
            c = db()
            try:
                self.api_post(path, c)
            finally:
                c.close()
        except Exception:
            self.internal_error()

    # -- GET 接口 ---------------------------------------------------------

    def api_get(self, path, c):
        uid = self.user(c)

        if path == '/api/me':
            if not uid:
                self.send_json(None)
                return
            row = c.execute(
                'SELECT id,username,email,created_at,is_admin FROM Users WHERE id=?', (uid,)
            ).fetchone()
            if not row:
                self.send_json(None)
                return
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            payload = dict(row)
            payload['created_at'] = iso_z(payload['created_at'])
            payload['cash'] = money(account['cash']) if account else 0
            self.send_json(payload)
            return

        if path == '/api/wallet':
            if not uid:
                self.send_json({'error': '请先登录'}, 401)
                return
            wallet = c.execute('SELECT balance FROM Wallets WHERE user_id=?', (uid,)).fetchone()
            points = c.execute('SELECT points FROM PointBalances WHERE user_id=?', (uid,)).fetchone()
            member = c.execute('SELECT expires_at FROM Memberships WHERE user_id=?', (uid,)).fetchone()
            history = c.execute(
                'SELECT item_type,item_name,wei_amount,points_added,cash_amount,member_days,created_at '
                'FROM RechargeOrders WHERE user_id=? ORDER BY id DESC LIMIT 10',
                (uid,),
            ).fetchall()
            claimed = c.execute(
                'SELECT 1 FROM WalletTopupClaims WHERE user_id=? AND claim_day=?',
                (uid, today_local()),
            ).fetchone()
            admin = is_admin_user(c, uid)
            member_active = is_member_active(member['expires_at'] if member else None)
            points_rate = Decimal(POINT_CASH_RATE) * (Decimal('1.1') if member_active else Decimal('1'))
            self.send_json({
                'balance': money(wallet['balance']) if wallet else 0,
                'points': points['points'] if points else 0,
                'member_expires_at': iso_z(member['expires_at']) if member else None,
                'topup_available': admin or not bool(claimed),
                'points_cash_rate': float(points_rate),
                'member_cash_bonus': 0.1 if member_active else 0,
                'history': [
                    {**dict(item), 'created_at': iso_z(item['created_at'])} for item in history
                ],
            })
            return

        if path == '/api/admin/users':
            if not uid:
                self.send_json({'error': '请先登录'}, 401)
                return
            if not is_admin_user(c, uid):
                self.send_json({'error': '仅管理员可以查看用户'}, 403)
                return
            rows = c.execute(
                'SELECT id,username,email,is_admin,is_banned,created_at,last_seen_at FROM Users ORDER BY id'
            ).fetchall()
            users = []
            for row in rows:
                item = dict(row)
                item['online'] = is_online(item['last_seen_at'])
                item['last_seen_at'] = iso_z(item['last_seen_at']) if item['last_seen_at'] else None
                item['created_at'] = iso_z(item['created_at'])
                users.append(item)
            self.send_json(users)
            return

        if path == '/api/admin/swaps':
            if not uid:
                self.send_json({'error': '请先登录'}, 401)
                return
            if not is_admin_user(c, uid):
                self.send_json({'error': '仅管理员可以查看股票互换'}, 403)
                return
            rows = c.execute(
                'SELECT s.*,fu.username AS from_username,tu.username AS to_username,'
                'f.name AS from_name,t.name AS to_name '
                'FROM StockSwaps s JOIN Users fu ON fu.id=s.from_user_id '
                'JOIN Users tu ON tu.id=s.to_user_id '
                'JOIN Stocks f ON f.code=s.from_code JOIN Stocks t ON t.code=s.to_code '
                'ORDER BY s.id DESC LIMIT 100'
            ).fetchall()
            self.send_json([
                {**dict(row), 'created_at': iso_z(row['created_at']),
                 'responded_at': iso_z(row['responded_at']) if row['responded_at'] else None}
                for row in rows
            ])
            return

        if path == '/api/swaps':
            if not uid:
                self.send_json({'error': '请先登录'}, 401)
                return
            rows = c.execute(
                'SELECT s.*,fu.username AS from_username,tu.username AS to_username,'
                'f.name AS from_name,t.name AS to_name '
                'FROM StockSwaps s JOIN Users fu ON fu.id=s.from_user_id '
                'JOIN Users tu ON tu.id=s.to_user_id '
                'JOIN Stocks f ON f.code=s.from_code JOIN Stocks t ON t.code=s.to_code '
                'WHERE s.from_user_id=? OR s.to_user_id=? ORDER BY s.id DESC LIMIT 50',
                (uid, uid),
            ).fetchall()
            items = [
                {**dict(row), 'created_at': iso_z(row['created_at']),
                 'responded_at': iso_z(row['responded_at']) if row['responded_at'] else None}
                for row in rows
            ]
            self.send_json({
                'incoming': [x for x in items if x['to_user_id'] == uid],
                'outgoing': [x for x in items if x['from_user_id'] == uid],
            })
            return

        if path == '/api/stocks':
            self.refresh_market(c)
            term = parse_qs(urlparse(self.path).query).get('q', [''])[0].lower()
            rows = c.execute(
                'SELECT * FROM Stocks WHERE code LIKE ? OR name LIKE ? ORDER BY code',
                ('%' + term + '%', '%' + term + '%'),
            ).fetchall()
            self.send_json([dict(row) for row in rows])
            return

        if path.startswith('/api/stocks/') and path.endswith('/history'):
            code = path.split('/')[-2]
            rows = c.execute(
                'SELECT price,created_at FROM MarketTicks WHERE code=? ORDER BY id DESC LIMIT 80',
                (code,),
            ).fetchall()
            self.send_json([
                {'price': money(item['price']), 'created_at': iso_z(item['created_at'])}
                for item in reversed(rows)
            ])
            return

        if path == '/api/fishing/status':
            if not uid:
                self.send_json({'error': '请先登录'}, 401)
                return
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            cash = Decimal(str(account['cash'])) if account else Decimal('0')
            admin = is_admin_user(c, uid)
            row = c.execute(
                'SELECT attempts,earned FROM FishingDaily WHERE user_id=? AND day=?',
                (uid, today_local()),
            ).fetchone()
            self.send_json({
                'unlocked': admin or cash < FISHING_UNLOCK_CASH,
                'cash': money(cash),
                'attempts': row['attempts'] if row else 0,
                'earned': money(row['earned']) if row else 0,
                'daily_limit': None,
            })
            return

        if path in ('/api/mining/status', '/api/forge/status'):
            if not uid:
                self.send_json({'error': '请先登录'}, 401)
                return
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            cash = Decimal(str(account['cash'])) if account else Decimal('0')
            admin = is_admin_user(c, uid)
            game = 'mining' if path.startswith('/api/mining') else 'forge'
            stats = c.execute(
                'SELECT runs,earned FROM GameStats WHERE user_id=? AND game=?', (uid, game)
            ).fetchone()
            payload = {
                'unlocked': admin or cash < GAME_UNLOCK_CASH,
                'cash': money(cash),
                'runs': stats['runs'] if stats else 0,
                'earned': money(stats['earned']) if stats else 0,
                'active': None,
            }
            if game == 'mining':
                run = c.execute(
                    "SELECT id,pot,digs FROM MiningRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                    (uid,),
                ).fetchone()
            else:
                run = c.execute(
                    "SELECT id,value,hits,risk FROM ForgeRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                    (uid,),
                ).fetchone()
            if run:
                active = dict(run)
                if game == 'mining':
                    active['pot'] = money(active['pot'])
                else:
                    active['value'] = money(active['value'])
                    active['risk'] = forge_risk(active['hits'])
                    tier, grade = forge_grade(active['hits'])
                    active['quality'] = tier
                    active['quality_name'] = grade
                payload['active'] = active
            self.send_json(payload)
            return

        if path == '/api/portfolio':
            if not uid:
                self.send_json({'error': '请先登录'}, 401)
                return
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            cash = Decimal(str(account['cash'])) if account else Decimal('0')
            rows = c.execute(
                'SELECT h.code,s.name,h.quantity,h.avg_cost,s.price FROM Holdings h '
                'JOIN Stocks s ON s.code=h.code WHERE h.user_id=? AND h.quantity>0',
                (uid,),
            ).fetchall()
            positions = []
            market_value = Decimal('0')
            profit = Decimal('0')
            for row in rows:
                quantity = Decimal(row['quantity'])
                price = Decimal(str(row['price']))
                avg_cost = Decimal(str(row['avg_cost']))
                value = (quantity * price).quantize(Decimal('0.01'))
                gain = ((price - avg_cost) * quantity).quantize(Decimal('0.01'))
                market_value += value
                profit += gain
                positions.append({
                    **dict(row),
                    'avg_cost': money(avg_cost),
                    'price': money(price),
                    'market_value': float(value),
                    'profit': float(gain),
                })
            self.send_json({
                'cash': money(cash),
                'positions': positions,
                'market_value': float(market_value.quantize(Decimal('0.01'))),
                'profit': float(profit.quantize(Decimal('0.01'))),
            })
            return

        if path == '/api/orders':
            if not uid:
                self.send_json({'error': '请先登录'}, 401)
                return
            rows = c.execute(
                'SELECT o.*,s.name FROM Orders o JOIN Stocks s ON s.code=o.code '
                'WHERE user_id=? ORDER BY id DESC LIMIT 30',
                (uid,),
            ).fetchall()
            self.send_json([
                {**dict(row), 'created_at': iso_z(row['created_at'])} for row in rows
            ])
            return

        self.send_json({'error': 'Not found'}, 404)

    # -- 行情刷新 ---------------------------------------------------------

    def refresh_market(self, c):
        now = time.time()
        with App.market_lock:
            if now - App.last_market_tick <= 30:
                return
            App.last_market_tick = now
        for item in c.execute('SELECT code,price FROM Stocks').fetchall():
            pct = Decimal(str(random.uniform(-3.5, 3.5))).quantize(Decimal('0.0001'))
            current = Decimal(str(item['price']))
            updated = max(Decimal('0.50'), (current * (Decimal('1') + pct / Decimal('100'))).quantize(Decimal('0.01')))
            c.execute('UPDATE Stocks SET price=?,change_pct=? WHERE code=?', (updated, pct, item['code']))
            c.execute('INSERT INTO MarketTicks(code,price) VALUES(?,?)', (item['code'], updated))
            c.execute(
                'DELETE FROM MarketTicks WHERE code=? AND id NOT IN '
                '(SELECT id FROM MarketTicks WHERE code=? ORDER BY id DESC LIMIT ?)',
                (item['code'], item['code'], TICKS_KEPT),
            )
        c.commit()

    # -- POST 接口 --------------------------------------------------------

    def api_post(self, path, c):
        data = self.body()
        uid = self.user(c)
        client_ip = self.client_address[0] if self.client_address else 'unknown'

        # ---- 注册 / 登录 / 退出 ----

        if path == '/api/email/send-code':
            email = normalize_email(data.get('email'))
            if not EMAIL_RE.fullmatch(email):
                self.send_json({'error': '请输入有效邮箱'}, 400)
                return
            if c.execute('SELECT 1 FROM Users WHERE email=? LIMIT 1', (email,)).fetchone():
                self.send_json({'error': '该邮箱已注册，请直接登录'}, 409)
                return
            now = utcnow()
            latest = c.execute(
                'SELECT created_at FROM EmailCodes WHERE email=? ORDER BY id DESC LIMIT 1', (email,)
            ).fetchone()
            if latest:
                elapsed = (now - parse_utc(latest['created_at'])).total_seconds()
                if elapsed < EMAIL_CODE_RESEND_SECONDS:
                    wait = int(EMAIL_CODE_RESEND_SECONDS - elapsed) + 1
                    self.send_json({'error': f'请 {wait} 秒后再试', 'retry_after': wait}, 429)
                    return
            hour_ago = utcstamp(now - timedelta(hours=1))
            per_email = c.execute(
                'SELECT COUNT(*) AS n FROM EmailCodes WHERE email=? AND created_at>?', (email, hour_ago)
            ).fetchone()['n']
            if per_email >= EMAIL_CODE_MAX_PER_HOUR:
                self.send_json({'error': '该邮箱发送次数过多，请稍后再试'}, 429)
                return
            per_ip = c.execute(
                'SELECT COUNT(*) AS n FROM EmailCodes WHERE ip=? AND created_at>?', (client_ip, hour_ago)
            ).fetchone()['n']
            if per_ip >= EMAIL_CODE_MAX_PER_IP_HOUR:
                self.send_json({'error': '发送过于频繁，请稍后再试'}, 429)
                return
            code = f'{secrets.randbelow(900000) + 100000}'
            expires = utcstamp(now + timedelta(seconds=EMAIL_CODE_TTL_SECONDS))
            cursor = c.execute(
                'INSERT INTO EmailCodes(email,code_hash,ip,expires_at) VALUES(?,?,?,?)',
                (email, hash_email_code(email, code), client_ip, expires),
            )
            c.execute('DELETE FROM EmailCodes WHERE created_at<?', (utcstamp(now - timedelta(days=2)),))
            c.commit()
            try:
                send_email_code(email, code)
            except RuntimeError as error:
                c.execute('DELETE FROM EmailCodes WHERE id=?', (cursor.lastrowid,))
                c.commit()
                self.send_json({'error': str(error)}, 502)
                return
            payload = {'ok': True, 'retry_after': EMAIL_CODE_RESEND_SECONDS}
            if os.getenv('EMAIL_DRY_RUN', '0').strip() == '1':
                payload['dev_code'] = code
            self.send_json(payload)
            return

        if path in ('/api/register', '/api/login'):
            key = ('auth', client_ip)
            if App.throttle.blocked(key):
                self.send_json({'error': '尝试次数过多，请 10 分钟后再试'}, 429)
                return
            name = str(data.get('username', '')).strip()
            email = str(data.get('email', '')).strip().lower()
            password = str(data.get('password', ''))

            if path.endswith('register'):
                if not USERNAME_RE.fullmatch(name) or not EMAIL_RE.fullmatch(email) or not 6 <= len(password) <= 128:
                    self.send_json({'error': '请填写有效用户名、邮箱，密码至少 6 位'}, 400)
                    return
                if c.execute(
                    'SELECT 1 FROM Users WHERE username=? OR email=? LIMIT 1', (name, email)
                ).fetchone():
                    self.send_json({'error': '用户名或邮箱已注册'}, 409)
                    return
                code = str(data.get('code', '')).strip()
                if not re.fullmatch(r'\d{6}', code):
                    self.send_json({'error': '请输入 6 位邮箱验证码'}, 400)
                    return
                record = c.execute(
                    'SELECT id,code_hash,expires_at,attempts FROM EmailCodes '
                    'WHERE email=? AND used=0 ORDER BY id DESC LIMIT 1',
                    (email,),
                ).fetchone()
                if not record:
                    self.send_json({'error': '请先获取邮箱验证码'}, 400)
                    return
                if parse_utc(record['expires_at']) < utcnow():
                    self.send_json({'error': '验证码已过期，请重新获取'}, 400)
                    return
                if record['attempts'] >= EMAIL_CODE_MAX_ATTEMPTS:
                    self.send_json({'error': '验证码错误次数过多，请重新获取'}, 400)
                    return
                if not hmac.compare_digest(hash_email_code(email, code), record['code_hash']):
                    c.execute('UPDATE EmailCodes SET attempts=attempts+1 WHERE id=?', (record['id'],))
                    c.commit()
                    left = max(0, EMAIL_CODE_MAX_ATTEMPTS - record['attempts'] - 1)
                    self.send_json({'error': f'验证码不正确，还可尝试 {left} 次'}, 400)
                    return
                c.execute('UPDATE EmailCodes SET used=1 WHERE id=?', (record['id'],))
                try:
                    cursor = c.execute(
                        'INSERT INTO Users(username,email,password) VALUES(?,?,?)',
                        (name, email, hashpw(password)),
                    )
                    new_uid = cursor.lastrowid
                    c.execute('INSERT INTO Accounts(user_id,cash) VALUES(?,?)', (new_uid, float(DEFAULT_CASH)))
                    c.execute('INSERT INTO Wallets(user_id,balance) VALUES(?,?)', (new_uid, float(DEFAULT_WEI)))
                    c.execute('INSERT INTO PointBalances(user_id,points) VALUES(?,0)', (new_uid,))
                    c.execute('INSERT INTO Memberships(user_id,expires_at) VALUES(?,NULL)', (new_uid,))
                    c.commit()
                except IntegrityError:
                    c.rollback()
                    self.send_json({'error': '用户名或邮箱已注册'}, 409)
                    return
            else:
                row = c.execute(
                    'SELECT id,password,is_banned FROM Users WHERE username=? OR email=?', (name, email or name.lower())
                ).fetchone()
                if not row or not validpw(password, row['password']):
                    App.throttle.fail(key)
                    self.send_json({'error': '账号或密码错误'}, 401)
                    return
                if row['is_banned']:
                    App.throttle.fail(key)
                    self.send_json({'error': '该账号已被管理员拉黑，无法登录'}, 403)
                    return
                new_uid = row['id']
            App.throttle.clear(key)
            token = self.create_session(c, new_uid)
            c.execute('UPDATE Users SET last_seen_at=? WHERE id=?', (utcstamp(), new_uid))
            c.commit()
            self.send_json({'ok': True, 'token': token}, 200, self.session_headers(token))
            return

        if path == '/api/logout':
            token = self.session_token()
            if token:
                c.execute('DELETE FROM Sessions WHERE token=?', (token,))
                c.commit()
            self.send_json({'ok': True}, 200, self.session_headers())
            return

        # ---- 以下接口全部需要登录 ----

        if not uid:
            self.send_json({'error': '请先登录'}, 401)
            return

        if path == '/api/heartbeat':
            c.execute('UPDATE Users SET last_seen_at=? WHERE id=?', (utcstamp(), uid))
            c.commit()
            self.send_json({'ok': True, 'is_admin': is_admin_user(c, uid)})
            return

        if path == '/api/wallet/topup':
            admin = is_admin_user(c, uid)
            try:
                if not admin:
                    c.execute(
                        'INSERT INTO WalletTopupClaims(user_id,claim_day) VALUES(?,?)',
                        (uid, today_local()),
                    )
                c.execute('UPDATE Wallets SET balance=balance+500 WHERE user_id=?', (uid,))
                c.execute(
                    "INSERT INTO RechargeOrders(user_id,item_type,item_name,wei_amount,points_added,member_days) "
                    "VALUES(?,'wei','每日练习补给',500,0,0)",
                    (uid,),
                )
                c.commit()
            except IntegrityError:
                c.rollback()
                self.send_json({'error': '今天的虚拟玮元补给已领取，请明天再来'}, 409)
                return
            self.send_json({'ok': True, 'item_name': '每日练习补给', 'amount': 500})
            return

        if path == '/api/points/exchange':
            try:
                points = int(data.get('points', 0))
            except (TypeError, ValueError):
                points = 0
            if points <= 0 or points > 1_000_000:
                self.send_json({'error': '请输入有效的积分数量'}, 400)
                return
            balance = c.execute('SELECT points FROM PointBalances WHERE user_id=?', (uid,)).fetchone()
            if not balance or balance['points'] < points:
                self.send_json({'error': '积分不足'}, 400)
                return
            member = c.execute('SELECT expires_at FROM Memberships WHERE user_id=?', (uid,)).fetchone()
            member_active = is_member_active(member['expires_at'] if member else None)
            rate = Decimal(POINT_CASH_RATE) * (Decimal('1.1') if member_active else Decimal('1'))
            amount = Decimal(points) * rate
            c.execute('UPDATE PointBalances SET points=points-? WHERE user_id=?', (points, uid))
            c.execute('UPDATE Accounts SET cash=cash+? WHERE user_id=?', (amount, uid))
            c.execute(
                "INSERT INTO RechargeOrders(user_id,item_type,item_name,wei_amount,points_added,cash_amount,member_days) "
                "VALUES(?,'points_cash','积分兑换虚拟余额',0,?,?,0)",
                (uid, -points, amount),
            )
            c.commit()
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            left = c.execute('SELECT points FROM PointBalances WHERE user_id=?', (uid,)).fetchone()
            self.send_json({
                'ok': True, 'points': left['points'], 'amount': money(amount),
                'cash': money(account['cash']), 'rate': float(rate),
                'member_bonus': 0.1 if member_active else 0,
            })
            return

        if path == '/api/recharge':
            catalog = {
                'points_100': ('points', '100 积分', 10, 100, 0),
                'points_500': ('points', '500 积分', 50, 500, 0),
                'points_1000': ('points', '1000 积分', 100, 1000, 0),
                'member_7': ('member', '体验会员 7 天', 300, 0, 7),
                'member_30': ('member', '体验会员 30 天', 1000, 0, 30),
                'member_90': ('member', '体验会员 90 天', 2000, 0, 90),
            }
            item = catalog.get(data.get('package'))
            if not item:
                self.send_json({'error': '请选择有效的模拟套餐'}, 400)
                return
            item_type, item_name, cost, points_added, days = item
            wallet = c.execute('SELECT balance FROM Wallets WHERE user_id=?', (uid,)).fetchone()
            if not wallet or Decimal(str(wallet['balance'])) < cost:
                self.send_json({'error': '玮元余额不足，模拟订单未扣款'}, 400)
                return
            try:
                c.execute('UPDATE Wallets SET balance=balance-? WHERE user_id=?', (cost, uid))
                if points_added:
                    c.execute('UPDATE PointBalances SET points=points+? WHERE user_id=?', (points_added, uid))
                if days:
                    member = c.execute('SELECT expires_at FROM Memberships WHERE user_id=?', (uid,)).fetchone()
                    expires = parse_utc(member['expires_at']) if member and member['expires_at'] else None
                    start = expires if expires and expires > utcnow() else utcnow()
                    c.execute(
                        'UPDATE Memberships SET expires_at=? WHERE user_id=?',
                        (utcstamp(start + timedelta(days=days)), uid),
                    )
                c.execute(
                    'INSERT INTO RechargeOrders(user_id,item_type,item_name,wei_amount,points_added,member_days) '
                    'VALUES(?,?,?,?,?,?)',
                    (uid, item_type, item_name, cost, points_added, days),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
            self.send_json({'ok': True, 'item_name': item_name})
            return

        if path in ('/api/admin/users/kick', '/api/admin/users/ban'):
            if not is_admin_user(c, uid):
                self.send_json({'error': '仅管理员可以管理用户'}, 403)
                return
            try:
                target_id = int(data.get('id', 0))
            except (TypeError, ValueError):
                target_id = 0
            if not target_id or target_id == uid:
                self.send_json({'error': '不能操作当前登录账号'}, 400)
                return
            target = c.execute(
                'SELECT id,is_admin,is_banned FROM Users WHERE id=?', (target_id,)
            ).fetchone()
            if not target:
                self.send_json({'error': '用户不存在'}, 404)
                return
            if target['is_admin']:
                self.send_json({'error': '不能操作其他管理员'}, 400)
                return
            if path.endswith('/kick'):
                c.execute('DELETE FROM Sessions WHERE user_id=?', (target_id,))
                c.execute('UPDATE Users SET last_seen_at=NULL WHERE id=?', (target_id,))
                c.commit()
                self.send_json({'ok': True, 'action': 'kick'})
                return
            banned = bool(data.get('banned', True))
            c.execute('UPDATE Users SET is_banned=? WHERE id=?', (1 if banned else 0, target_id))
            if banned:
                c.execute('DELETE FROM Sessions WHERE user_id=?', (target_id,))
                c.execute('UPDATE Users SET last_seen_at=NULL WHERE id=?', (target_id,))
            c.commit()
            self.send_json({'ok': True, 'action': 'ban' if banned else 'unban', 'banned': banned})
            return

        if path == '/api/swaps':
            to_username = str(data.get('to_username', '')).strip()
            from_code = str(data.get('from_code', '')).strip()
            to_code = str(data.get('to_code', '')).strip()
            try:
                from_quantity = int(data.get('from_quantity', 0))
                to_quantity = int(data.get('to_quantity', 0))
            except (TypeError, ValueError):
                from_quantity = to_quantity = 0
            if from_code == to_code:
                self.send_json({'error': '互换的两只股票不能相同'}, 400)
                return
            if from_quantity <= 0 or to_quantity <= 0:
                self.send_json({'error': '互换数量必须大于 0'}, 400)
                return
            target = c.execute(
                'SELECT id,is_banned FROM Users WHERE username=?', (to_username,)
            ).fetchone()
            if not target:
                self.send_json({'error': '找不到对方用户名'}, 404)
                return
            if target['id'] == uid:
                self.send_json({'error': '不能和自己互换'}, 400)
                return
            if target['is_banned']:
                self.send_json({'error': '对方账号已被拉黑'}, 400)
                return
            for code in (from_code, to_code):
                if not c.execute('SELECT 1 FROM Stocks WHERE code=?', (code,)).fetchone():
                    self.send_json({'error': '股票不存在'}, 404)
                    return
            holding = c.execute(
                'SELECT quantity FROM Holdings WHERE user_id=? AND code=?', (uid, from_code)
            ).fetchone()
            if not holding or holding['quantity'] < from_quantity:
                self.send_json({'error': '你持有的该股票数量不足'}, 400)
                return
            message = clean_text(data.get('message', ''), 0, 200)
            if message is None:
                self.send_json({'error': '留言包含不支持的字符或过长'}, 400)
                return
            cursor = c.execute(
                'INSERT INTO StockSwaps(from_user_id,to_user_id,from_code,from_quantity,'
                'to_code,to_quantity,message) VALUES(?,?,?,?,?,?,?)',
                (uid, target['id'], from_code, from_quantity, to_code, to_quantity, message),
            )
            c.commit()
            self.send_json({'ok': True, 'id': cursor.lastrowid})
            return

        if path in ('/api/swaps/accept', '/api/swaps/decline'):
            try:
                swap_id = int(data.get('id', 0))
            except (TypeError, ValueError):
                swap_id = 0
            swap = c.execute('SELECT * FROM StockSwaps WHERE id=?', (swap_id,)).fetchone()
            if not swap:
                self.send_json({'error': '互换请求不存在'}, 404)
                return
            if uid not in (swap['from_user_id'], swap['to_user_id']):
                self.send_json({'error': '无权操作该互换请求'}, 403)
                return
            if swap['status'] != 'pending':
                self.send_json({'error': '该请求已经处理'}, 409)
                return
            if path.endswith('/decline'):
                status = 'declined' if uid == swap['to_user_id'] else 'cancelled'
                c.execute(
                    "UPDATE StockSwaps SET status=?,responded_at=? WHERE id=?",
                    (status, utcstamp(), swap_id),
                )
                c.commit()
                self.send_json({'ok': True, 'status': status})
                return
            if uid != swap['to_user_id']:
                self.send_json({'error': '只有接收方可以接受互换'}, 403)
                return
            from_stock = c.execute('SELECT price FROM Stocks WHERE code=?', (swap['from_code'],)).fetchone()
            to_stock = c.execute('SELECT price FROM Stocks WHERE code=?', (swap['to_code'],)).fetchone()
            if not from_stock or not to_stock:
                self.send_json({'error': '股票数据不存在'}, 404)
                return
            from_holding = c.execute(
                'SELECT quantity FROM Holdings WHERE user_id=? AND code=?',
                (swap['from_user_id'], swap['from_code']),
            ).fetchone()
            to_holding = c.execute(
                'SELECT quantity FROM Holdings WHERE user_id=? AND code=?',
                (uid, swap['to_code']),
            ).fetchone()
            if not from_holding or from_holding['quantity'] < swap['from_quantity']:
                self.send_json({'error': '发起方持仓不足，无法完成互换'}, 400)
                return
            if not to_holding or to_holding['quantity'] < swap['to_quantity']:
                self.send_json({'error': '你的持仓不足，无法完成互换'}, 400)
                return
            try:
                move_holding(
                    c, swap['from_user_id'], uid, swap['from_code'],
                    swap['from_quantity'], Decimal(str(from_stock['price'])),
                )
                move_holding(
                    c, uid, swap['from_user_id'], swap['to_code'],
                    swap['to_quantity'], Decimal(str(to_stock['price'])),
                )
                c.execute(
                    "UPDATE StockSwaps SET status='accepted',responded_at=? WHERE id=?",
                    (utcstamp(), swap_id),
                )
                c.commit()
            except ValueError as error:
                c.rollback()
                self.send_json({'error': str(error)}, 400)
                return
            self.send_json({'ok': True, 'status': 'accepted'})
            return

        if path == '/api/trade':
            code = str(data.get('code', ''))
            side = data.get('side')
            quantity = data.get('quantity')
            if side not in ('买入', '卖出') or not isinstance(quantity, int) or quantity < 100 or quantity % 100:
                self.send_json({'error': '请输入 100 股起、100 股整数倍的数量'}, 400)
                return
            stock = c.execute('SELECT price FROM Stocks WHERE code=?', (code,)).fetchone()
            if not stock:
                self.send_json({'error': '没有找到该模拟股票'}, 404)
                return
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            if not account:
                self.send_json({'error': '账户数据异常，请重置体验'}, 400)
                return
            price = Decimal(str(stock['price']))
            cash = Decimal(str(account['cash']))
            holding = c.execute(
                'SELECT quantity,avg_cost FROM Holdings WHERE user_id=? AND code=?', (uid, code)
            ).fetchone()
            amount = (price * quantity).quantize(Decimal('0.01'))
            if side == '买入':
                if amount > cash:
                    self.send_json({'error': '模拟现金不足。请留意仓位和风险，不要借钱交易。'}, 400)
                    return
                old_quantity = holding['quantity'] if holding else 0
                old_avg = Decimal(str(holding['avg_cost'])) if holding else Decimal('0')
                new_avg = ((Decimal(old_quantity) * old_avg + amount) / Decimal(old_quantity + quantity)).quantize(Decimal('0.01'))
                c.execute('UPDATE Accounts SET cash=cash-? WHERE user_id=?', (amount, uid))
                c.execute(
                    'INSERT INTO Holdings(user_id,code,quantity,avg_cost) VALUES(?,?,?,?) '
                    'ON CONFLICT(user_id,code) DO UPDATE SET quantity=quantity+excluded.quantity,avg_cost=?',
                    (uid, code, quantity, price, new_avg),
                )
            else:
                if not holding or holding['quantity'] < quantity:
                    self.send_json({'error': '持仓数量不足，不能卖出不存在的股票'}, 400)
                    return
                c.execute('UPDATE Accounts SET cash=cash+? WHERE user_id=?', (amount, uid))
                c.execute(
                    'UPDATE Holdings SET quantity=quantity-? WHERE user_id=? AND code=?',
                    (quantity, uid, code),
                )
            c.execute(
                'INSERT INTO Orders(user_id,code,side,quantity,price) VALUES(?,?,?,?,?)',
                (uid, code, side, quantity, price),
            )
            c.commit()
            self.send_json({'ok': True, 'price': money(price)})
            return

        if path == '/api/reset':
            c.execute('UPDATE Accounts SET cash=? WHERE user_id=?', (float(DEFAULT_CASH), uid))
            c.execute('DELETE FROM Holdings WHERE user_id=?', (uid,))
            c.execute('DELETE FROM Orders WHERE user_id=?', (uid,))
            c.commit()
            self.send_json({'ok': True})
            return

        if path == '/api/admin/stocks':
            admin = c.execute('SELECT is_admin FROM Users WHERE id=?', (uid,)).fetchone()
            if not admin or not admin['is_admin']:
                self.send_json({'error': '仅管理员可以添加模拟股票'}, 403)
                return
            code = str(data.get('code', '')).strip().upper()
            name = clean_text(data.get('name'), 2, 100)
            category = clean_text(data.get('category'), 1, 50)
            try:
                price = Decimal(str(data.get('price', ''))).quantize(Decimal('0.01'))
            except Exception:
                price = Decimal('0')
            if not re.fullmatch(r'[A-Z0-9]{1,16}', code) or not name or not category or not Decimal('0') < price <= Decimal('100000000'):
                self.send_json({'error': '请填写有效代码、名称、类别和大于 0 的模拟价格'}, 400)
                return
            if c.execute('SELECT code FROM Stocks WHERE code=?', (code,)).fetchone():
                self.send_json({'error': f'股票代码 {code} 已存在，请换一个代码'}, 409)
                return
            if c.execute('SELECT code FROM Stocks WHERE LOWER(name)=LOWER(?)', (name,)).fetchone():
                self.send_json({'error': f'股票名称“{name}”已存在，请换一个名称'}, 409)
                return
            try:
                c.execute(
                    'INSERT INTO Stocks(code,name,price,change_pct,category,is_custom) VALUES(?,?,?,0,?,1)',
                    (code, name, price, category),
                )
                c.execute('INSERT INTO MarketTicks(code,price) VALUES(?,?)', (code, price))
                c.commit()
            except IntegrityError:
                c.rollback()
                self.send_json({'error': '该股票代码已经存在'}, 409)
                return
            self.send_json({'ok': True, 'stock': {'code': code, 'name': name, 'price': money(price), 'category': category}})
            return

        if path == '/api/admin/stocks/delete':
            admin = c.execute('SELECT is_admin FROM Users WHERE id=?', (uid,)).fetchone()
            if not admin or not admin['is_admin']:
                self.send_json({'error': '仅管理员可以删除模拟股票'}, 403)
                return
            code = str(data.get('code', '')).strip().upper()
            stock = c.execute('SELECT code,is_custom FROM Stocks WHERE code=?', (code,)).fetchone()
            if not stock:
                self.send_json({'error': '没有找到该股票代码'}, 404)
                return
            if not stock['is_custom']:
                self.send_json({'error': '系统预置股票不能删除'}, 400)
                return
            if c.execute('SELECT id FROM Orders WHERE code=? LIMIT 1', (code,)).fetchone() or \
                    c.execute('SELECT user_id FROM Holdings WHERE code=? AND quantity>0 LIMIT 1', (code,)).fetchone():
                self.send_json({'error': '该股票已有持仓或交易记录，暂不能删除'}, 409)
                return
            c.execute('DELETE FROM Holdings WHERE code=? AND quantity=0', (code,))
            c.execute('DELETE FROM Stocks WHERE code=?', (code,))
            c.commit()
            self.send_json({'ok': True, 'code': code})
            return

        # ---- 钓鱼练习 ----

        if path == '/api/fishing/start':
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            cash = Decimal(str(account['cash'])) if account else Decimal('0')
            if not is_admin_user(c, uid) and cash >= FISHING_UNLOCK_CASH:
                self.send_json({'error': '虚拟现金需低于 ¥10,000 才能开始练习'}, 400)
                return
            c.execute('INSERT OR IGNORE INTO FishingDaily(user_id,day) VALUES(?,?)', (uid, today_local()))
            c.execute(
                'UPDATE FishingDaily SET attempts=attempts+1,pending=1,started_at=? WHERE user_id=? AND day=?',
                (utcstamp(), uid, today_local()),
            )
            c.commit()
            row = c.execute(
                'SELECT attempts,earned FROM FishingDaily WHERE user_id=? AND day=?',
                (uid, today_local()),
            ).fetchone()
            self.send_json({'ok': True, 'attempts': row['attempts'], 'earned': money(row['earned'])})
            return

        if path == '/api/fishing/catch':
            try:
                score = max(0, min(100, int(data.get('score', 0))))
            except (TypeError, ValueError):
                score = 0
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            cash = Decimal(str(account['cash'])) if account else Decimal('0')
            if not is_admin_user(c, uid) and cash >= FISHING_UNLOCK_CASH:
                self.send_json({'error': '余额高于 ¥10,000，恢复小游戏暂未开放。请先暂停交易并复盘。'}, 400)
                return
            row = c.execute(
                'SELECT attempts,earned,pending,started_at FROM FishingDaily WHERE user_id=? AND day=?',
                (uid, today_local()),
            ).fetchone()
            if not row or not row['pending']:
                self.send_json({'error': '请先开始本轮钓鱼练习'}, 400)
                return
            elapsed = (utcnow() - parse_utc(row['started_at'])).total_seconds() if row['started_at'] else 0
            # 进度最快每秒上涨 10%，据此给出服务端上限；只有填满 100% 才算钓到鱼。
            max_score = min(100, int(elapsed * 10) + 1)
            score = min(score, max_score)
            c.execute('UPDATE FishingDaily SET pending=0 WHERE user_id=? AND day=?', (uid, today_local()))
            if score < 100:
                # 进度条没有填满：本轮结束，没有鱼也没有奖励。
                c.commit()
                self.send_json({
                    'ok': True,
                    'won': False,
                    'reward': 0,
                    'score': score,
                    'fish': None,
                    'attempts': row['attempts'],
                    'daily_limit': None,
                })
                return
            fish = random.choices(
                [('溪流鲦', '🐟'), ('银鳞鲑', '🐠'), ('锦鲤', '🎏'), ('金色锦鲤', '🐡'), ('钻石鱼王', '💎🐟')],
                [60, 23, 12, 4, 1],
            )[0]
            multiplier = 5 if fish[0] == '钻石鱼王' else 2 if fish[0] == '金色锦鲤' else 1
            reward = 1000 * multiplier
            c.execute('UPDATE Accounts SET cash=cash+? WHERE user_id=?', (reward, uid))
            c.execute(
                'UPDATE FishingDaily SET earned=earned+? WHERE user_id=? AND day=?',
                (reward, uid, today_local()),
            )
            c.commit()
            self.send_json({
                'ok': True,
                'won': True,
                'reward': reward,
                'score': score,
                'fish': {'name': fish[0], 'emoji': fish[1]},
                'attempts': row['attempts'],
                'daily_limit': None,
            })
            return

        # ---- 挖矿小游戏 ----

        if path == '/api/mining/start':
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            cash = Decimal(str(account['cash'])) if account else Decimal('0')
            if not is_admin_user(c, uid) and cash >= GAME_UNLOCK_CASH:
                self.send_json({'error': '虚拟资金需低于 ¥10,000 才能进入矿场'}, 403)
                return
            c.execute(
                "UPDATE MiningRuns SET status='abandoned',ended_at=? WHERE user_id=? AND status='active'",
                (utcstamp(), uid),
            )
            cursor = c.execute('INSERT INTO MiningRuns(user_id,pot,digs) VALUES(?,0,0)', (uid,))
            c.execute(
                "INSERT INTO GameStats(user_id,game,runs) VALUES(?,'mining',1) "
                'ON CONFLICT(user_id,game) DO UPDATE SET runs=runs+1',
                (uid,),
            )
            c.commit()
            self.send_json({'ok': True, 'run': {'id': cursor.lastrowid, 'pot': 0, 'digs': 0}})
            return

        if path == '/api/mining/dig':
            run = c.execute(
                "SELECT id,pot,digs FROM MiningRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                (uid,),
            ).fetchone()
            if not run:
                self.send_json({'error': '请先进入矿场'}, 400)
                return
            roll = random.random()
            if roll < MINING_BOMB_CHANCE:
                c.execute(
                    "UPDATE MiningRuns SET status='bombed',pot=0,digs=digs+1,ended_at=? WHERE id=?",
                    (utcstamp(), run['id']),
                )
                c.commit()
                self.send_json({
                    'ok': True, 'result': 'bomb', 'amount': 0, 'pot': 0,
                    'digs': run['digs'] + 1, 'status': 'bombed',
                })
                return
            bands = (
                ('diamond', MINING_DIAMOND_CHANCE),
                ('gold', MINING_GOLD_CHANCE),
                ('iron', MINING_IRON_CHANCE),
                ('coal', MINING_COAL_CHANCE),
            )
            rewards = {
                'soil': MINING_SOIL_REWARD,
                'coal': MINING_COAL_REWARD,
                'iron': MINING_IRON_REWARD,
                'gold': MINING_GOLD_REWARD,
                'diamond': MINING_DIAMOND_REWARD,
            }
            cursor = MINING_BOMB_CHANCE
            result = 'soil'
            for name, chance in bands:
                cursor += chance
                if roll < cursor:
                    result = name
                    break
            amount = random.randint(*rewards[result])
            pot = Decimal(str(run['pot'])) + amount
            c.execute('UPDATE MiningRuns SET pot=?,digs=digs+1 WHERE id=?', (pot, run['id']))
            c.commit()
            self.send_json({
                'ok': True, 'result': result, 'amount': amount, 'pot': money(pot),
                'digs': run['digs'] + 1, 'status': 'active',
            })
            return

        if path == '/api/mining/cashout':
            run = c.execute(
                "SELECT id,pot FROM MiningRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                (uid,),
            ).fetchone()
            if not run:
                self.send_json({'error': '没有可结算的矿场'}, 400)
                return
            amount = Decimal(str(run['pot']))
            if amount > 0:
                c.execute('UPDATE Accounts SET cash=cash+? WHERE user_id=?', (amount, uid))
            c.execute("UPDATE MiningRuns SET status='cashed',ended_at=? WHERE id=?", (utcstamp(), run['id']))
            c.execute(
                "INSERT INTO GameStats(user_id,game,runs,earned) VALUES(?,'mining',0,?) "
                'ON CONFLICT(user_id,game) DO UPDATE SET earned=earned+excluded.earned',
                (uid, amount),
            )
            c.commit()
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            self.send_json({
                'ok': True, 'amount': money(amount),
                'cash': money(Decimal(str(account['cash'])) if account else Decimal('0')),
                'status': 'cashed',
            })
            return

        # ---- 锻剑小游戏 ----

        if path == '/api/forge/start':
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            cash = Decimal(str(account['cash'])) if account else Decimal('0')
            if not is_admin_user(c, uid) and cash >= GAME_UNLOCK_CASH:
                self.send_json({'error': '虚拟资金需低于 ¥10,000 才能进入锻剑工坊'}, 403)
                return
            c.execute(
                "UPDATE ForgeRuns SET status='abandoned',ended_at=? WHERE user_id=? AND status='active'",
                (utcstamp(), uid),
            )
            cursor = c.execute(
                'INSERT INTO ForgeRuns(user_id,value,hits,risk) VALUES(?,0,0,?)',
                (uid, FORGE_BASE_SHATTER),
            )
            c.execute(
                "INSERT INTO GameStats(user_id,game,runs) VALUES(?,'forge',1) "
                'ON CONFLICT(user_id,game) DO UPDATE SET runs=runs+1',
                (uid,),
            )
            c.commit()
            self.send_json({
                'ok': True,
                'run': {
                    'id': cursor.lastrowid, 'value': 0, 'hits': 0,
                    'risk': FORGE_BASE_SHATTER, 'quality': 0, 'quality_name': FORGE_GRADES[0],
                },
            })
            return

        if path == '/api/forge/hit':
            run = c.execute(
                "SELECT id,value,hits,risk FROM ForgeRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                (uid,),
            ).fetchone()
            if not run:
                self.send_json({'error': '请先开始锻剑'}, 400)
                return
            risk = forge_risk(run['hits'])
            chance = min(FORGE_MAX_SHATTER, risk)
            if random.random() < chance:
                tier, grade = forge_grade(run['hits'])
                c.execute(
                    "UPDATE ForgeRuns SET status='shattered',value=0,ended_at=? WHERE id=?",
                    (utcstamp(), run['id']),
                )
                c.commit()
                self.send_json({
                    'ok': True, 'shattered': True, 'gain': 0, 'value': 0,
                    'hits': run['hits'], 'status': 'shattered',
                    'risk': risk, 'quality': tier, 'quality_name': grade,
                })
                return
            gain = random.randint(*FORGE_HIT_REWARD)
            value = Decimal(str(run['value'])) + gain
            hits = run['hits'] + 1
            tier, grade = forge_grade(hits)
            upgraded = hits in FORGE_GRADE_THRESHOLDS
            if upgraded:
                value = value * 2
            new_risk = forge_risk(hits)
            c.execute(
                'UPDATE ForgeRuns SET value=?,hits=?,risk=? WHERE id=?',
                (value, hits, new_risk, run['id']),
            )
            c.commit()
            self.send_json({
                'ok': True, 'shattered': False, 'gain': gain, 'value': money(value),
                'hits': hits, 'status': 'active', 'risk': new_risk,
                'quality': tier, 'quality_name': grade, 'upgraded': upgraded,
            })
            return

        if path == '/api/forge/sell':
            run = c.execute(
                "SELECT id,value,hits FROM ForgeRuns WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                (uid,),
            ).fetchone()
            if not run:
                self.send_json({'error': '没有可出售的剑'}, 400)
                return
            amount = Decimal(str(run['value']))
            if amount > 0:
                c.execute('UPDATE Accounts SET cash=cash+? WHERE user_id=?', (amount, uid))
            c.execute("UPDATE ForgeRuns SET status='sold',ended_at=? WHERE id=?", (utcstamp(), run['id']))
            c.execute(
                "INSERT INTO GameStats(user_id,game,runs,earned) VALUES(?,'forge',0,?) "
                'ON CONFLICT(user_id,game) DO UPDATE SET earned=earned+excluded.earned',
                (uid, amount),
            )
            c.commit()
            account = c.execute('SELECT cash FROM Accounts WHERE user_id=?', (uid,)).fetchone()
            self.send_json({
                'ok': True, 'amount': money(amount),
                'cash': money(Decimal(str(account['cash'])) if account else Decimal('0')),
                'status': 'sold',
            })
            return

        self.send_json({'error': 'Not found'}, 404)


def main():
    load_env()
    init_db()
    host = os.getenv('HOST', '127.0.0.1').strip() or '127.0.0.1'
    try:
        port = int(os.getenv('PORT', '8000'))
    except ValueError:
        port = 8000
    server = ThreadingHTTPServer((host, port), App)
    server.daemon_threads = True
    print(f'虚拟交易 · 模拟市场已启动：http://{host}:{port}', flush=True)
    print(f'数据库：{db_path()}', flush=True)
    print('入口：/（模拟市场）、/fishing.html（钓鱼）、/mining.html（挖矿）、/forge.html（锻剑）', flush=True)
    if host not in ('127.0.0.1', 'localhost', '::1'):
        print('提示：当前监听非本机地址，同一网络的其他人也能访问，请仅在可信环境使用。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止本地服务。')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
