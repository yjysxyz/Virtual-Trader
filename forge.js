const $ = (selector) => document.querySelector(selector);
const money = (value) => '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
let me = null;
let active = null;
let unlocked = false;

async function api(url, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  const token = sessionStorage.getItem('marketSession');
  if (token) headers.Authorization = 'Bearer ' + token;
  const response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.error || '请求失败');
  return data;
}

const SWORD_GRADES = ['平凡的剑', '优秀的剑', '精湛的剑', '史诗的剑', '传奇的剑'];
const SWORD_THRESHOLDS = [11, 26, 46, 71];
function qualityOf(hits) {
  let tier = 0;
  for (const threshold of SWORD_THRESHOLDS) {
    if (Number(hits || 0) >= threshold) tier += 1;
    else break;
  }
  return tier;
}

function setSwordName(name, tier = 0) {
  const node = $('#swordName');
  if (!node) return;
  node.textContent = name || SWORD_GRADES[0];
  node.className = 'grade-' + (Number(tier) || 0);
}

function renderRisk(riskValue) {
  const risk = Math.max(0, Number(riskValue || 0.01)) * 100;
  $('#riskFill').style.width = risk + '%';
  $('#riskText').textContent = risk.toFixed(1) + '%';
}

function sparkBurst(kind = 'steel') {
  const box = $('#sparks');
  if (!box) return;
  box.innerHTML = '';
  const count = kind === 'break' ? 18 : 10;
  for (let i = 0; i < count; i += 1) {
    const spark = document.createElement('i');
    spark.className = 'spark' + (kind === 'break' ? ' break' : '');
    const angle = Math.random() * Math.PI * 2;
    const distance = 26 + Math.random() * 74;
    spark.style.setProperty('--dx', `${Math.cos(angle) * distance}px`);
    spark.style.setProperty('--dy', `${Math.sin(angle) * distance - 18}px`);
    spark.style.left = `${40 + Math.random() * 8}%`;
    spark.style.top = `${60 + Math.random() * 8}%`;
    spark.style.animationDelay = `${Math.random() * 0.06}s`;
    box.appendChild(spark);
  }
  setTimeout(() => {
    box.innerHTML = '';
  }, 700);
}

function resetStage() {
  const stage = $('#stage');
  if (stage) stage.classList.remove('impact', 'shattered');
  const sparks = $('#sparks');
  if (sparks) sparks.innerHTML = '';
}

function setMsg(text, tone = '') {
  const node = $('#msg');
  node.textContent = text;
  node.className = 'forge-message' + (tone ? ' ' + tone : '');
}

function setSword(style) {
  const sword = $('#sword');
  sword.className = 'forge-sword' + (style ? ' ' + style : '');
}

function showStart() {
  $('#startBtn').classList.remove('hidden');
  $('#hitBtn').classList.add('hidden');
  $('#sellBtn').classList.add('hidden');
}

function showPlay() {
  $('#startBtn').classList.add('hidden');
  $('#hitBtn').classList.remove('hidden');
  $('#sellBtn').classList.remove('hidden');
}

function hideActions() {
  $('#startBtn').classList.add('hidden');
  $('#hitBtn').classList.add('hidden');
  $('#sellBtn').classList.add('hidden');
}

function render(s) {
  unlocked = !!s.unlocked;
  $('#cash').textContent = money(s.cash);
  $('#stats').textContent = `已锻剑 ${Number(s.runs) || 0} 次 · 累计获得 ${money(s.earned)} 虚拟资金 · 次数不限`;
  active = s.active || null;
  if (active) {
    showPlay();
    $('#value').textContent = money(active.value);
    $('#hits').textContent = `${Number(active.hits) || 0} 锤`;
    const tier = Number.isInteger(active.quality) ? active.quality : qualityOf(active.hits);
    setSwordName(active.quality_name || SWORD_GRADES[tier], tier);
    renderRisk(active.risk ?? 0.01);
    setSword('');
    if (!unlocked) setMsg('虚拟资金已回到 ¥10,000 以上；这把剑仍可继续敲完并出售。');
  } else if (unlocked) {
    showStart();
    $('#value').textContent = money(0);
    $('#hits').textContent = '0 锤';
    setSwordName(SWORD_GRADES[0], 0);
    renderRisk(0.01);
    setSword('');
  } else {
    hideActions();
    setSwordName(SWORD_GRADES[0], 0);
    renderRisk(0.01);
    setMsg(`当前虚拟资金 ${money(s.cash)}，需低于 ¥10,000 才能进入锻剑工坊。`);
  }
}

async function load() {
  const profile = await api('/api/me');
  if (!profile) {
    $('#player').textContent = '尚未登录';
    hideActions();
    setMsg('请返回模拟市场登录后再进入锻剑工坊。');
    return;
  }
  me = profile;
  $('#player').textContent = '练习账户：' + profile.username;
  render(await api('/api/forge/status'));
}

async function start() {
  try {
    await api('/api/forge/start', { method: 'POST', body: '{}' });
    resetStage();
    setSword('');
    setMsg('新剑已放到铁砧上。11 / 26 / 46 / 71 锤时提升品质，价值翻倍；碎裂概率会随敲击逐步上升。');
    await load();
  } catch (error) {
    setMsg(error.message, 'bad');
  }
}

async function hit() {
  const hitBtn = $('#hitBtn');
  const sellBtn = $('#sellBtn');
  hitBtn.disabled = true;
  sellBtn.disabled = true;
  const hammer = $('#hammer');
  hammer.classList.remove('swing');
  void hammer.offsetWidth;
  hammer.classList.add('swing');
  try {
    const result = await api('/api/forge/hit', { method: 'POST', body: '{}' });
    if (result.shattered) {
      await load();
      setSword('broken');
      setSwordName(result.quality_name || SWORD_GRADES[0], result.quality ?? 0);
      renderRisk(result.risk ?? 0.01);
      const stage = $('#stage');
      if (stage) {
        stage.classList.remove('impact');
        void stage.offsetWidth;
        stage.classList.add('impact', 'shattered');
      }
      sparkBurst('break');
      setMsg('剑被敲碎了，这把剑无法出售，之前积累的价值归零。', 'bad');
      return;
    }
    $('#value').textContent = money(result.value);
    $('#hits').textContent = `${result.hits} 锤`;
    renderRisk(result.risk);
    const tier = Number.isInteger(result.quality) ? result.quality : qualityOf(result.hits);
    setSwordName(result.quality_name || SWORD_GRADES[tier], tier);
    const sword = $('#sword');
    sword.classList.remove('hit');
    void sword.offsetWidth;
    sword.classList.add('hit');
    const stage = $('#stage');
    if (stage) {
      stage.classList.remove('impact');
      void stage.offsetWidth;
      stage.classList.add('impact');
    }
    sparkBurst('steel');
    setMsg(
      result.upgraded
        ? `品质提升为「${result.quality_name}」，剑的价值翻倍！`
        : `锤击成功，剑的价值 +${money(result.gain)}。`,
      'win'
    );
  } catch (error) {
    setMsg(error.message, 'bad');
  } finally {
    hitBtn.disabled = false;
    sellBtn.disabled = false;
  }
}

async function sell() {
  const sellBtn = $('#sellBtn');
  sellBtn.disabled = true;
  try {
    const result = await api('/api/forge/sell', { method: 'POST', body: '{}' });
    resetStage();
    setSword('');
    await load();
    setMsg(`剑已出售，获得 ${money(result.amount)} 虚拟资金。`, 'win');
  } catch (error) {
    setMsg(error.message, 'bad');
  } finally {
    sellBtn.disabled = false;
  }
}

$('#startBtn').onclick = start;
$('#hitBtn').onclick = hit;
$('#sellBtn').onclick = sell;

load().catch((error) => {
  $('#player').textContent = '连接失败';
  hideActions();
  setMsg(error.message, 'bad');
});
