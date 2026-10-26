const $ = (selector) => document.querySelector(selector);
const money = (value) => '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const HOLD_MS = 1600;
const W = 360;
const H = 360;
const FRONT = { x: 60, y: 110, w: 200, h: 200 };
const FRONT_POINTS = [[60, 110], [260, 110], [260, 310], [60, 310]];
const TOP_POINTS = [[60, 110], [120, 50], [320, 50], [260, 110]];
const SIDE_POINTS = [[260, 110], [320, 50], [320, 250], [260, 310]];

const PALETTES = {
  dirt: {
    front: ['#7c5437', '#8c5f3e', '#6b482f', '#9a704a', '#5c3e28'],
    top: ['#9b7148', '#aa8056', '#8b6440', '#b58b5c'],
    side: ['#604530', '#6e5037', '#533b29', '#7a5a3e'],
  },
};

const ORE_STYLES = {
  coal: { colors: ['#1f1f1f', '#343434', '#4b4b4b', '#101010'], sparkle: false },
  iron: { colors: ['#d9b28c', '#b98463', '#f0d2b0', '#9e6b48'], sparkle: false },
  gold: { colors: ['#ffd75e', '#e8b84a', '#fff4b0', '#b8862a'], sparkle: true },
  diamond: { colors: ['#9ff7f2', '#4fd8d8', '#d8fffc', '#2aa6a6'], sparkle: true },
};
const ORE_NAMES = { soil: '泥土', coal: '煤矿', iron: '铁矿', gold: '金矿', diamond: '钻石矿' };

const canvas = $('#blockCanvas');
const ctx = canvas.getContext('2d');
const ringCanvas = $('#ringCanvas');
const ringCtx = ringCanvas ? ringCanvas.getContext('2d') : null;
const view = $('#mineView');
const pickaxe = $('#pickaxe');
const startBtn = $('#startBtn');
const leaveBtn = $('#leaveBtn');
const alertBadge = $('#mineAlert');

let me = null;
let active = null;
let unlocked = false;
let busy = false;
let holding = false;
let progress = 0;
let holdStart = 0;
let holdRaf = 0;
let blockSeed = Math.floor(Math.random() * 1e9);
let blockType = 'dirt';
let blockVisible = true;
let particles = [];
let particleRaf = 0;
let particleLast = 0;

function mulberry32(seed) {
  return function random() {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function path(points) {
  ctx.beginPath();
  points.forEach(([x, y], index) => (index ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
}

function drawFace(points, palette, rand, pixel = 10) {
  ctx.save();
  path(points);
  ctx.clip();
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  points.forEach(([x, y]) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  });
  for (let y = Math.floor(minY / pixel) * pixel; y < maxY; y += pixel) {
    for (let x = Math.floor(minX / pixel) * pixel; x < maxX; x += pixel) {
      ctx.fillStyle = palette[Math.floor(rand() * palette.length)];
      ctx.fillRect(x, y, pixel, pixel);
    }
  }
  ctx.restore();
  ctx.strokeStyle = '#24170e';
  ctx.lineWidth = 3;
  path(points);
  ctx.stroke();
}

const BLOCK_SCALE = 0.82;

function drawRing(value) {
  if (!ringCtx) return;
  ringCtx.clearRect(0, 0, W, H);
  const cx = W / 2;
  const cy = H / 2;
  const radius = 168;
  ringCtx.save();
  ringCtx.lineWidth = 8;
  ringCtx.lineCap = 'round';
  ringCtx.strokeStyle = 'rgba(255,246,220,.16)';
  ringCtx.beginPath();
  ringCtx.arc(cx, cy, radius, 0, Math.PI * 2);
  ringCtx.stroke();
  if (value > 0) {
    const start = -Math.PI / 2;
    const end = start + Math.PI * 2 * Math.min(1, value);
    const gradient = ringCtx.createLinearGradient(0, 0, W, H);
    gradient.addColorStop(0, '#ffe29a');
    gradient.addColorStop(0.5, '#e8b84a');
    gradient.addColorStop(1, '#fff6c8');
    ringCtx.strokeStyle = gradient;
    ringCtx.shadowColor = '#ffd36e';
    ringCtx.shadowBlur = 14;
    ringCtx.beginPath();
    ringCtx.arc(cx, cy, radius, start, end);
    ringCtx.stroke();
    ringCtx.shadowBlur = 0;
    ringCtx.fillStyle = '#fffbe0';
    ringCtx.beginPath();
    ringCtx.arc(cx + Math.cos(end) * radius, cy + Math.sin(end) * radius, 6, 0, Math.PI * 2);
    ringCtx.fill();
  }
  ringCtx.restore();
}

function drawOre(type) {
  const ore = ORE_STYLES[type];
  if (!ore) return;
  const rand = mulberry32(blockSeed + 37);
  for (let i = 0; i < 16; i += 1) {
    const x = FRONT.x + 8 + rand() * (FRONT.w - 16);
    const y = FRONT.y + 8 + rand() * (FRONT.h - 16);
    const size = 3 + rand() * 7;
    ctx.fillStyle = ore.colors[i % ore.colors.length];
    ctx.fillRect(Math.round(x), Math.round(y), Math.round(size), Math.round(size));
  }
  if (ore.sparkle) {
    const sparkle = mulberry32(blockSeed + 71);
    ctx.fillStyle = type === 'diamond' ? '#eaffff' : '#fff6c0';
    for (let i = 0; i < 8; i += 1) {
      const x = FRONT.x + 10 + sparkle() * (FRONT.w - 20);
      const y = FRONT.y + 10 + sparkle() * (FRONT.h - 20);
      ctx.fillRect(Math.round(x), Math.round(y), 4, 4);
    }
  }
}

function drawBombUnder() {
  const x = 180;
  const y = 326;
  const radius = 40;
  ctx.save();
  ctx.shadowColor = '#ff6a3c';
  ctx.shadowBlur = 22;
  ctx.fillStyle = '#15110d';
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
  ctx.fillStyle = '#3b2b20';
  ctx.beginPath();
  ctx.arc(x - 12, y - 12, 10, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = '#8a6542';
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(x + 18, y - 32);
  ctx.quadraticCurveTo(x + 34, y - 54, x + 42, y - 70);
  ctx.stroke();
  ctx.fillStyle = '#ffcf4a';
  ctx.fillRect(x + 38, y - 76, 9, 9);
  ctx.fillStyle = '#ff8a3c';
  ctx.fillRect(x + 41, y - 73, 4, 4);
}

function drawBlock(value, type = blockType, lift = 0) {
  ctx.clearRect(0, 0, W, H);
  if (!blockVisible) return;
  const facePalette = PALETTES.dirt;
  ctx.save();
  ctx.translate(W / 2, H / 2);
  ctx.scale(BLOCK_SCALE, BLOCK_SCALE);
  ctx.translate(-W / 2, -H / 2);
  if (type === 'bomb') drawBombUnder();
  ctx.save();
  ctx.translate(0, -lift);
  const rand = mulberry32(blockSeed);
  drawFace(FRONT_POINTS, facePalette.front, rand);
  drawFace(TOP_POINTS, facePalette.top, rand);
  drawFace(SIDE_POINTS, facePalette.side, rand);
  if (ORE_STYLES[type]) drawOre(type);
  ctx.restore();
  ctx.restore();
}

function setProgress(value) {
  progress = value;
  const percent = Math.round(value * 100);
  $('#mineProgress').style.width = percent + '%';
  $('#mineProgressText').textContent = percent + '%';
  drawRing(value);
}

function setHint(text) {
  $('#holdHint').textContent = text;
}

function setMsg(text, tone = '') {
  const node = $('#msg');
  node.textContent = text;
  node.className = 'mine-message' + (tone ? ' ' + tone : '');
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function api(url, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  const token = sessionStorage.getItem('marketSession');
  if (token) headers.Authorization = 'Bearer ' + token;
  const response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.error || '请求失败');
  return data;
}

function spawnParticles(type) {
  if (particleRaf) cancelAnimationFrame(particleRaf);
  particles = [];
  const colors = type === 'gold'
    ? ['#ffe08a', '#e8b84a', '#b8862a', '#fff4b0']
    : type === 'diamond'
      ? ['#9ff7f2', '#4fd8d8', '#d8fffc', '#ffffff']
      : type === 'iron'
        ? ['#d9b28c', '#b98463', '#f0d2b0', '#9e6b48']
        : type === 'coal'
          ? ['#1f1f1f', '#343434', '#4b4b4b', '#101010']
          : type === 'bomb'
            ? ['#5b4130', '#a33b2c', '#24170e', '#ff8a3c']
            : ['#8c5f3e', '#6b482f', '#9a704a', '#5c3e28'];
  for (let i = 0; i < 30; i += 1) {
    const x = W / 2 + (FRONT.x + 30 + Math.random() * (FRONT.w - 60) - W / 2) * BLOCK_SCALE;
    const y = H / 2 + (FRONT.y + 30 + Math.random() * (FRONT.h - 60) - H / 2) * BLOCK_SCALE;
    particles.push({
      x,
      y,
      vx: (Math.random() - 0.5) * 260,
      vy: -90 - Math.random() * 260,
      size: 5 + Math.random() * 8,
      life: 0.65 + Math.random() * 0.35,
      color: colors[Math.floor(Math.random() * colors.length)],
    });
  }
  blockVisible = false;
  drawRing(0);
  particleLast = 0;
  particleRaf = requestAnimationFrame(tickParticles);
}

function tickParticles(now) {
  const dt = particleLast ? Math.min(0.05, (now - particleLast) / 1000) : 0;
  particleLast = now;
  ctx.clearRect(0, 0, W, H);
  for (const particle of particles) {
    particle.vy += 900 * dt;
    particle.x += particle.vx * dt;
    particle.y += particle.vy * dt;
    particle.life -= dt;
    ctx.fillStyle = particle.color;
    ctx.fillRect(Math.round(particle.x), Math.round(particle.y), particle.size, particle.size);
  }
  particles = particles.filter((particle) => particle.life > 0);
  if (particles.length) {
    particleRaf = requestAnimationFrame(tickParticles);
  } else {
    particleRaf = 0;
    particleLast = 0;
  }
}

function resetBlock() {
  if (particleRaf) {
    cancelAnimationFrame(particleRaf);
    particleRaf = 0;
  }
  particles = [];
  particleLast = 0;
  alertBadge?.classList.add('hidden');
  blockSeed = Math.floor(Math.random() * 1e9);
  blockType = 'dirt';
  blockVisible = true;
  setProgress(0);
  drawBlock(0, 'dirt');
  if (active) setHint('按住方块开始挖');
  else if (unlocked) setHint('先点“开始挖矿”');
  else setHint('余额低于 ¥10,000 后解锁');
}

function canMine() {
  return Boolean(active) && !busy && !holding && !particleRaf;
}

function stepHold(now) {
  if (!holding) return;
  progress = Math.min(1, (now - holdStart) / HOLD_MS);
  setProgress(progress);
  drawBlock(progress);
  if (progress >= 1) {
    holding = false;
    cancelAnimationFrame(holdRaf);
    finishDig();
    return;
  }
  holdRaf = requestAnimationFrame(stepHold);
}

function beginHold() {
  if (!canMine()) return;
  holding = true;
  holdStart = performance.now();
  progress = 0;
  setProgress(0);
  view.classList.add('mining');
  pickaxe.classList.add('swing');
  setHint('挖掘中…松手会重置');
  holdRaf = requestAnimationFrame(stepHold);
}

function endHold() {
  if (!holding) return;
  const had = progress;
  holding = false;
  cancelAnimationFrame(holdRaf);
  view.classList.remove('mining');
  pickaxe.classList.remove('swing');
  setProgress(0);
  drawBlock(0);
  setHint(active ? '按住方块开始挖' : '先点“开始挖矿”');
  if (had > 0.08) setMsg('松手早了一点，这一块的进度已经重置。', 'bad');
}

async function finishDig() {
  if (busy) return;
  busy = true;
  view.classList.remove('mining');
  pickaxe.classList.remove('swing');
  setProgress(1);
  drawBlock(1);
  try {
    const result = await api('/api/mining/dig', { method: 'POST', body: '{}' });
    if (result.result === 'bomb') {
      blockType = 'bomb';
      drawBlock(1, 'bomb', 22);
      alertBadge?.classList.remove('hidden');
      $('#pot').textContent = money(0);
      $('#digs').textContent = `${result.digs} 铲`;
      setMsg('炸弹！它就在泥土下面，本轮矿袋清空。', 'bad');
      await sleep(700);
      alertBadge?.classList.add('hidden');
      view.classList.remove('flash');
      void view.offsetWidth;
      view.classList.add('flash');
      spawnParticles('bomb');
      await sleep(550);
      if (particleRaf) {
        cancelAnimationFrame(particleRaf);
        particleRaf = 0;
      }
      await load();
      resetBlock();
      busy = false;
      return;
    }
    blockType = result.result;
    drawBlock(1, blockType);
    spawnParticles(result.result);
    $('#pot').textContent = money(result.pot);
    $('#digs').textContent = `${result.digs} 铲`;
    const label = ORE_NAMES[result.result] || '矿石';
    setMsg(
      `${label}！矿袋 +${money(result.amount)}。`,
      result.result === 'gold' || result.result === 'diamond' ? 'win' : ''
    );
    await sleep(700);
    resetBlock();
    busy = false;
  } catch (error) {
    busy = false;
    resetBlock();
    setMsg(error.message, 'bad');
  }
}

function render(status) {
  unlocked = Boolean(status.unlocked);
  active = status.active || null;
  $('#cash').textContent = money(status.cash);
  $('#stats').textContent = `已挖矿 ${Number(status.runs) || 0} 次 · 累计获得 ${money(status.earned)} 虚拟资金 · 次数不限`;
  if (active) {
    view.classList.remove('locked');
    startBtn.classList.add('hidden');
    leaveBtn.classList.remove('hidden');
    $('#pot').textContent = money(active.pot);
    $('#digs').textContent = `${Number(active.digs) || 0} 铲`;
    setHint('按住方块开始挖');
    if (!unlocked) setMsg('虚拟资金已回到 ¥10,000 以上；本轮仍可挖完并带走矿袋。');
  } else if (unlocked) {
    view.classList.add('locked');
    startBtn.classList.remove('hidden');
    leaveBtn.classList.add('hidden');
    $('#pot').textContent = money(0);
    $('#digs').textContent = '0 铲';
    setHint('先点“开始挖矿”');
  } else {
    view.classList.add('locked');
    startBtn.classList.add('hidden');
    leaveBtn.classList.add('hidden');
    setHint('余额低于 ¥10,000 后解锁');
    setMsg(`当前虚拟资金 ${money(status.cash)}，需低于 ¥10,000 才能进入矿场。`);
  }
  drawBlock(0, 'dirt');
}

async function load() {
  const profile = await api('/api/me');
  if (!profile) {
    $('#player').textContent = '尚未登录';
    view.classList.add('locked');
    startBtn.classList.add('hidden');
    leaveBtn.classList.add('hidden');
    setHint('请先登录');
    setMsg('请返回模拟市场登录后再进入矿场。');
    return;
  }
  me = profile;
  $('#player').textContent = '练习账户：' + profile.username;
  render(await api('/api/mining/status'));
}

async function start() {
  if (busy) return;
  try {
    await api('/api/mining/start', { method: 'POST', body: '{}' });
    await load();
    resetBlock();
    setMsg('矿场已开启。按住方块挖掘，挖到炸弹会清空矿袋，随时可以带着收益离开。');
  } catch (error) {
    setMsg(error.message, 'bad');
  }
}

async function leave() {
  if (busy) return;
  try {
    const result = await api('/api/mining/cashout', { method: 'POST', body: '{}' });
    await load();
    resetBlock();
    setMsg(`带着 ${money(result.amount)} 虚拟资金离开矿场。`, 'win');
  } catch (error) {
    setMsg(error.message, 'bad');
  }
}

canvas.addEventListener('pointerdown', (event) => {
  if (event.button !== undefined && event.button !== 0) return;
  if (!canMine()) return;
  event.preventDefault();
  try {
    canvas.setPointerCapture(event.pointerId);
  } catch (error) {
    // 部分浏览器不支持 pointer capture，忽略即可。
  }
  beginHold();
});
for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
  canvas.addEventListener(name, endHold);
}
window.addEventListener('pointerup', endHold);
canvas.addEventListener('contextmenu', (event) => event.preventDefault());
canvas.addEventListener('keydown', (event) => {
  if (event.code === 'Space') {
    event.preventDefault();
    beginHold();
  }
});
canvas.addEventListener('keyup', (event) => {
  if (event.code === 'Space') {
    event.preventDefault();
    endHold();
  }
});

startBtn.onclick = start;
leaveBtn.onclick = leave;

drawRing(0);
drawBlock(0, 'dirt');
load().catch((error) => {
  $('#player').textContent = '连接失败';
  view.classList.add('locked');
  startBtn.classList.add('hidden');
  leaveBtn.classList.add('hidden');
  setMsg(error.message, 'bad');
});

const beatSession=()=>{const token=sessionStorage.getItem('marketSession');const headers=token?{Authorization:'Bearer '+token}:{};fetch('/api/heartbeat',{method:'POST',headers,credentials:'same-origin'}).catch(()=>{})};
beatSession();setInterval(beatSession,45000);
