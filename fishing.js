const $=s=>document.querySelector(s);
const money=n=>'¥'+Number(n||0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
let me=null,state=null,active=false,holding=false,hasProgress=false,frame=0,lastFrame=0,elapsed=0,progress=0,barY=.38,barVelocity=0,fishY=.5;
const game=$('#gameCanvas'),ctx=game.getContext('2d'),scene=$('#sceneCanvas'),sceneCtx=scene.getContext('2d');
function rect(c,x,y,w,h,color){c.fillStyle=color;c.fillRect(Math.round(x),Math.round(y),Math.round(w),Math.round(h))}
function drawScene(){
  const c=sceneCtx;c.imageSmoothingEnabled=false;rect(c,0,0,640,160,'#a8ce91');rect(c,0,0,640,75,'#b9d9a7');
  rect(c,54,24,50,10,'#e4ecc0');rect(c,42,34,76,9,'#e4ecc0');rect(c,430,15,60,10,'#e4ecc0');rect(c,414,25,92,8,'#e4ecc0');
  rect(c,520,22,34,34,'#f2d985');rect(c,0,72,640,30,'#789d68');
  for(let i=0;i<12;i++){const x=i*58-5,top=56+(i%4)*8;rect(c,x,top,46,48,'#557c58');rect(c,x+8,top-12,30,14,'#557c58');rect(c,x+16,top-21,14,11,'#557c58');rect(c,x+20,top+12,6,16,'#826447')}
  rect(c,0,100,640,60,'#73aeb0');rect(c,0,105,640,8,'#8ac1b9');rect(c,0,126,640,5,'#9bcbbc');rect(c,0,148,640,5,'#5c9694');
  rect(c,0,94,640,10,'#b5b276');rect(c,28,84,9,22,'#628f59');rect(c,38,78,8,28,'#628f59');rect(c,590,81,8,25,'#628f59');rect(c,600,74,8,32,'#628f59');
  // A tiny original pixel angler on the bank.
  rect(c,145,72,12,16,'#d69c68');rect(c,143,68,16,7,'#503f39');rect(c,145,88,6,10,'#4e6958');rect(c,153,88,6,10,'#4e6958');rect(c,157,78,15,3,'#755940');
  c.strokeStyle='#55483b';c.lineWidth=2;c.beginPath();c.moveTo(170,78);c.lineTo(244,121);c.stroke();rect(c,242,118,3,8,'#eee2ad');
}
function drawFish(c,x,y,dir=1){const s=4,dx=dir>=0?0:1;rect(c,x,y,s*5,s*3,'#e1ad55');rect(c,x+((dx)?s*4:-s),y+s,s,s,'#e1ad55');rect(c,x+s,y-s,s*3,s,'#f3cf78');rect(c,x+(dx?s:s*3),y+s,s,s,'#463f37');rect(c,x+(dx?s*4:0),y+s*2,s,s,'#bd7950')}
function drawGame(){
  const c=ctx;c.imageSmoothingEnabled=false;rect(c,0,0,320,380,'#b2c98a');rect(c,10,10,300,360,'#516c55');rect(c,16,16,288,348,'#d3c28c');rect(c,25,25,252,330,'#75aeb0');
  for(let i=0;i<10;i++){const yy=34+i*31;rect(c,28,yy,245,3,i%2?'#8bc0b7':'#699fa3');rect(c,38+(i%3)*45,yy+11,22,3,'#9bcec0')}
  rect(c,36,37,3,300,'#548e91');rect(c,262,37,3,300,'#548e91');
  // Bubbles and bottom plants give the play area a hand-built pixel look.
  const t=elapsed;for(let i=0;i<5;i++){const bx=44+i*47,by=45+((t*25+i*59)%260);rect(c,bx,by,4,4,'#d0ece0');rect(c,bx+7,by-7,3,3,'#c4e3d6')}
  rect(c,33,327,240,20,'#547e59');for(let i=0;i<8;i++){rect(c,44+i*30,316-(i%3)*7,4,18,'#4b7957');rect(c,48+i*30,320-(i%2)*8,5,4,'#91b76a')}
  const gx=91,gy=38,gw=126,gh=285,zoneH=54,zoneSize=zoneH/gh,zoneY=gy+barY*gh,fy=gy+fishY*gh;
  // The catch bar is the controllable green window; the fish swims independently.
  rect(c,gx-4,zoneY-4,gw+8,zoneH+8,'#36544c');rect(c,gx,zoneY,gw,zoneH,'#83ad61');rect(c,gx+5,zoneY+5,gw-10,zoneH-10,'#a7cb75');
  for(let k=0;k<4;k++)rect(c,gx+9,zoneY+11+k*13,gw-18,2,'#b8d88a');
  drawFish(c,gx+54,fy,Math.sin(t*.004)>=0?1:-1);
  // Progress gauge.
  rect(c,235,gy-3,24,gh+6,'#40564c');rect(c,239,gy,16,gh,'#d8d0a7');let fillH=gh*progress;rect(c,239,gy+gh-fillH,16,fillH,'#91bd62');
  for(let i=1;i<5;i++)rect(c,239,gy+i*gh/5,16,2,'#eee3bb');
  rect(c,42,42,22,22,'#d4bc75');rect(c,48,48,10,10,'#f4dd91');
}
async function api(url,options={}){
  const headers={'Content-Type':'application/json',...(options.headers||{})},token=sessionStorage.getItem('marketSession');
  if(token)headers.Authorization='Bearer '+token;
  const response=await fetch(url,{...options,headers,credentials:'same-origin'}),data=await response.json().catch(()=>({}));
  if(!response.ok)throw Error(data.error||'请求失败');return data;
}
async function refreshStatus(){
  state=await api('/api/fishing/status');$('#fishingCash').textContent=money(state.cash);
  $('#dailyStatus').textContent=`已练习 ${state.attempts} 次 · 累计获得 ${money(state.earned)} 虚拟资金 · 次数不限`;
  const start=$('#startBtn'),hold=$('#holdBtn');
  if(!state.unlocked){start.disabled=true;start.textContent='余额低于 ¥10,000 后解锁';$('#gameMessage').textContent='当前余额尚未达到练习条件。请先暂停交易、复盘自己的决策。';return}
  start.disabled=false;start.textContent='开始钓鱼';hold.disabled=false;
}
function stopInput(){holding=false;$('#holdBtn').classList.remove('held')}
function finishGame(won){
  active=false;stopInput();cancelAnimationFrame(frame);$('#holdBtn').classList.add('hidden');$('#startBtn').classList.remove('hidden');$('#startBtn').disabled=true;$('#startBtn').textContent='正在结算…';
  const score=Math.max(0,Math.min(100,Math.round(progress*100)));
  api('/api/fishing/catch',{method:'POST',body:JSON.stringify({score})}).then(result=>{
    if(won){$('#victoryTitle').textContent=result.fish.name==='钻石鱼王'?'钓到钻石鱼王！':'钓到鱼啦！';$('.victory-fish').textContent=result.fish.emoji;$('#victoryReward').textContent=`${result.fish.name} · 专注得分 ${result.score} · 获得 ${money(result.reward)} 虚拟资金`;$('#victoryOverlay').classList.remove('hidden')}
    else $('#gameMessage').textContent=`钓到${result.fish.name}。本轮得分 ${result.score}，获得 ${money(result.reward)} 虚拟资金。`;
  }).catch(error=>{$('#gameMessage').textContent=error.message}).finally(async()=>{
    try{await refreshStatus()}catch(error){$('#gameMessage').textContent=error.message}
    $('#startBtn').classList.remove('hidden');
    if(state&&!state.unlocked){$('#startBtn').disabled=true;$('#startBtn').textContent='余额低于 ¥10,000 后解锁'}
    else{$('#startBtn').disabled=false;$('#startBtn').textContent='再钓一次'}
  });
}
function tick(now){
  if(!active)return;
  const dt=Math.min(.04,(now-lastFrame)/1000||0);lastFrame=now;elapsed+=(dt||0);
  // Hold raises the green bar; releasing lets it drift down with a little inertia.
  const zoneSize=54/285;
  barVelocity+=(holding?-3.1:1.75)*dt;barVelocity*=Math.pow(.12,dt);barVelocity=Math.max(-1.65,Math.min(1.15,barVelocity));
  barY=Math.max(0,Math.min(1-zoneSize,barY+barVelocity*dt));
  const sec=elapsed;fishY=.5+.29*Math.sin(sec*1.17)+.17*Math.sin(sec*2.63+.8)+.06*Math.sin(sec*4.1);fishY=Math.max(.035,Math.min(.965,fishY));
  const caught=fishY>=barY-.025&&fishY<=barY+zoneSize+.025;
  progress=Math.max(0,Math.min(1,progress+(caught?dt/10:-dt/16)));
  if(progress>=.02)hasProgress=true;
  $('#progressFill').style.width=`${Math.round(progress*100)}%`;$('#progressText').textContent=`${Math.round(progress*100)}%`;
  $('#timerText').textContent='不限时';
  drawGame();
  if(progress>=1||(hasProgress&&progress<=0)){finishGame(progress>=1);return}
  frame=requestAnimationFrame(tick);
}
$('#startBtn').addEventListener('click',async()=>{
  if(!state?.unlocked)return;
  const startButton=$('#startBtn');startButton.disabled=true;startButton.textContent='正在准备鱼竿…';
  try{await api('/api/fishing/start',{method:'POST',body:'{}'});await refreshStatus()}
  catch(error){$('#gameMessage').textContent=error.message;startButton.disabled=false;startButton.textContent='再试一次';return}
  active=true;holding=false;hasProgress=false;elapsed=0;progress=0;barY=.38;barVelocity=0;fishY=.5;lastFrame=performance.now();
  $('#progressFill').style.width='0%';$('#progressText').textContent='0%';$('#timerText').textContent='不限时';$('#gameMessage').textContent='不限时练习：进度填满即可成功；获得进度后若退回零，本轮结束。';
  $('#startBtn').classList.add('hidden');$('#holdBtn').classList.remove('hidden');$('.fishing-game').classList.add('fishing-running');
  frame=requestAnimationFrame(tick);
});
const holdButton=$('#holdBtn');
holdButton.addEventListener('pointerdown',event=>{if(!active)return;event.preventDefault();holding=true;holdButton.classList.add('held');holdButton.setPointerCapture?.(event.pointerId)});
for(const name of ['pointerup','pointercancel','lostpointercapture'])holdButton.addEventListener(name,stopInput);
window.addEventListener('keydown',event=>{if(event.code==='Space'&&active){event.preventDefault();holding=true;holdButton.classList.add('held')}});
window.addEventListener('keyup',event=>{if(event.code==='Space')stopInput()});
$('#victoryContinue').addEventListener('click',()=>$('#victoryOverlay').classList.add('hidden'));
drawScene();drawGame();
(async()=>{
  try{
    me=await api('/api/me');
    if(!me){$('#playerName').textContent='尚未登录';$('#fishingCash').textContent='—';$('#startBtn').disabled=true;$('#startBtn').textContent='请先登录';$('#gameMessage').textContent='请返回模拟市场登录后再进入钓鱼练习。';return}
    $('#playerName').textContent=`练习账户：${me.username}`;await refreshStatus();
  }catch(error){$('#playerName').textContent='连接失败';$('#fishingCash').textContent='—';$('#startBtn').disabled=true;$('#startBtn').textContent='暂时无法进入';$('#gameMessage').textContent=error.message}
})();
