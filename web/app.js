"use strict";
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const f=(n,d=2)=>n==null||isNaN(n)?"\u2014":Number(n).toLocaleString("en-IN",{minimumFractionDigits:d,maximumFractionDigits:d});
const pc=n=>n==null?"\u2014":(n>=0?"+":"")+n.toFixed(2)+"%";
const sg=n=>n==null?"":n>=0?"up":"dn";
const clamp=v=>Math.max(0,Math.min(100,Math.round(v)));
const store={get(k,d){try{return JSON.parse(localStorage.getItem(k))||d}catch(e){return d}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
async function api(path,opt){const r=await fetch(path,opt);if(!r.ok){let m=r.status;try{m=(await r.json()).detail||m}catch(e){}throw new Error(typeof m==="string"?m:JSON.stringify(m))}return r.json()}
const IST={timeZone:"Asia/Kolkata"};
const S={wl:store.get("wl2",[["RELIANCE","NSE"],["TCS","NSE"],["TATASTEEL","NSE"],["ITC","NSE"],["HDFCBANK","NSE"]]),cur:null,quotes:{},cfg:null,tab:"overview",range:"1d",
  runId:null,run:null,msgs:[],rendered:0,pollT:null,market:"closed",lastPx:{},quant:{}};
const key=(s,e)=>s+":"+e;

/* ---------- markdown (escaped, tables + lists) ---------- */
function md(src){
  const L=String(src||"").replace(/\r/g,"").split("\n"),o=[];let i=0;
  const inl=s=>esc(s).replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>").replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g,"$1<i>$2</i>");
  const isRow=l=>/^\s*\|.*\|\s*$/.test(l),cells=r=>r.trim().replace(/^\||\|$/g,"").split("|").map(c=>c.trim());
  const block=/^(#{1,4}\s|\s*[-*]\s|\s*\d+[.)]\s|\s*\|)/;
  while(i<L.length){const l=L[i];let m;
    if(isRow(l)&&i+1<L.length&&/^\s*\|[\s:|-]+\|\s*$/.test(L[i+1])){const head=cells(l),rows=[];i+=2;while(i<L.length&&isRow(L[i]))rows.push(cells(L[i++]));
      o.push("<table><thead><tr>"+head.map(c=>`<th>${inl(c)}</th>`).join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+r.map(c=>`<td>${inl(c)}</td>`).join("")+"</tr>").join("")+"</tbody></table>");continue}
    if(m=l.match(/^(#{1,4})\s+(.*)/)){o.push(`<h3>${inl(m[2])}</h3>`);i++;continue}
    if(/^\s*[-*]\s+/.test(l)){const it=[];while(i<L.length&&/^\s*[-*]\s+/.test(L[i]))it.push(`<li>${inl(L[i++].replace(/^\s*[-*]\s+/,""))}</li>`);o.push("<ul>"+it.join("")+"</ul>");continue}
    if(/^\s*\d+[.)]\s+/.test(l)){const it=[];while(i<L.length&&/^\s*\d+[.)]\s+/.test(L[i]))it.push(`<li>${inl(L[i++].replace(/^\s*\d+[.)]\s+/,""))}</li>`);o.push("<ol>"+it.join("")+"</ol>");continue}
    if(/^\s*(-{3,}|\*{3,})\s*$/.test(l)){o.push("<hr>");i++;continue}
    if(!l.trim()){i++;continue}
    const p=[];while(i<L.length&&L[i].trim()&&!block.test(L[i]))p.push(inl(L[i++]));
    if(p.length)o.push("<p>"+p.join("<br>")+"</p>");else{o.push(`<p>${inl(l)}</p>`);i++}}
  return o.join("")}

/* ---------- header, strip, watchlist ---------- */
function renderMkt(m){$("mkt").innerHTML=`<span class="dot ${esc(m.state)}"></span><span>${esc(m.label)}</span><span>${esc(m.now_ist)}</span>`}
function renderStrip(d){const cell=r=>r.error?`<div><span>${esc(r.name)}</span><b>n/a</b></div>`:
  `<div title="${esc(r.source)} \u00b7 ${esc(r.as_of_label)}${r.stale?" \u00b7 STALE":""}"><span>${esc(r.name)}${r.stale?" \u00b7 stale":""}</span><b>${f(r.price)}</b><i class="${sg(r.change_pct)}">${pc(r.change_pct)}</i></div>`;
  $("strip").innerHTML=d.indices.map(cell).join("")+d.macro.map(cell).join("")}
async function tickIndices(){try{const d=await api("/api/indices");S.market=d.market.state;renderStrip(d);renderMkt(d.market)}catch(e){$("strip").innerHTML=`<div><span>Rates unavailable</span><b>${esc(e.message).slice(0,80)}</b></div>`}
  setTimeout(tickIndices,S.market==="closed"?30000:5000)}
function renderWL(){$("wl").innerHTML=S.wl.map(([s,e],i)=>{const k=key(s,e),q=S.quotes[k];
  const right=!q?"<small>\u2026</small>":q.error?`<span class="err" title="${esc(q.error)}">n/a</span>`:`<b>${f(q.price)}</b><small class="${sg(q.change_pct)}">${pc(q.change_pct)}${q.stale?" \u00b7 stale":""}</small>`;
  return `<div class="row"><button class="sel" data-k="${esc(k)}" aria-pressed="${S.cur===k}"><span>${esc(s)}<small>${e}</small></span><span class="r">${right}</span></button><button class="rm" data-i="${i}" aria-label="Remove ${esc(s)}">\u00d7</button></div>`}).join("");renderSaveBtn()}
async function tickQuotes(){const ks=new Set(S.wl.map(w=>key(...w)));if(S.cur)ks.add(S.cur);
  try{(await api("/api/quotes?items="+encodeURIComponent([...ks].join(",")))).forEach(r=>S.quotes[key(r.symbol,r.exchange)]=r);renderWL();renderHead()}catch(e){}
  setTimeout(tickQuotes,S.market==="closed"?30000:5000)}
$("wl").onclick=e=>{const rm=e.target.closest(".rm");if(rm){S.wl.splice(+rm.dataset.i,1);store.set("wl2",S.wl);renderWL();return}
  const b=e.target.closest(".sel");if(b){const [s,x]=b.dataset.k.split(":");selectSym(s,x)}};

/* ---------- search ---------- */
let sugT,sugMap={},sugQ=null;$("q").addEventListener("input",e=>{clearTimeout(sugT);const v=e.target.value.trim();if(v.length<2){sugMap={};sugQ=null;$("sug").innerHTML="";return}
  sugT=setTimeout(async()=>{try{const r=await api("/api/search?q="+encodeURIComponent(v)),seen=new Set();
    const list=r.filter(x=>!seen.has(x.symbol)&&seen.add(x.symbol));sugMap={};list.forEach(x=>sugMap[x.symbol.toUpperCase()]=x.exchange);sugQ=v;
    $("sug").innerHTML=list.map(x=>`<option value="${esc(x.symbol)}">${esc(x.name)} \u00b7 ${x.exchange}${x.type==="INDEX"?" \u00b7 index":""}</option>`).join("")}catch(_){}},250)});
$("q").addEventListener("keydown",e=>{if(e.key!=="Enter")return;const raw=e.target.value.trim();if(!raw)return;
  let sym=raw.toUpperCase(),ex=sugMap[sym];
  if(!ex&&sugQ&&sugQ.toLowerCase()===raw.toLowerCase()){const o=document.querySelector("#sug option");if(o){sym=o.value.toUpperCase();ex=sugMap[sym]}}
  selectSym(sym,ex||$("ex").value);e.target.value="";sugQ=null});

/* ---------- overview ---------- */
function buildOverview(){$("p-overview").innerHTML=`
<section class="pn"><div class="hd"><div><h1 id="ov-nm">Loading\u2026</h1><div class="chips" id="ov-chips"></div><button class="btn ghost" id="ov-save" type="button" style="margin-top:8px">\u2606 Save to watchlist</button></div>
<div style="text-align:right"><div class="px" id="ov-px">\u2014</div><div id="ov-chg"></div><div class="sub" id="ov-src"></div></div></div>
<div id="ov-err"></div>
<div class="rg" id="rg">${["1d","5d","1mo","6mo","1y","5y"].map(r=>`<button data-r="${r}" aria-pressed="${r===S.range}">${r.toUpperCase()}</button>`).join("")}</div>
<div class="cw" id="chart"></div><div class="leg" id="leg"></div><div class="grid4" id="ov-ind"></div></section>
<section class="pn"><h2>Quant screen (rule-based, no LLM)</h2><div id="quant"><span class="note">Loading daily bars\u2026</span></div></section>`;
  $("rg").onclick=e=>{const b=e.target.closest("button");if(!b)return;S.range=b.dataset.r;[...$("rg").children].forEach(x=>x.setAttribute("aria-pressed",x===b));loadChart()};
  $("ov-save").onclick=toggleSave;renderSaveBtn()}
function toggleSave(){if(!S.cur)return;const [s,x]=S.cur.split(":");const i=S.wl.findIndex(w=>key(...w)===S.cur);
  if(i>=0)S.wl.splice(i,1);else S.wl.unshift([s,x]);
  store.set("wl2",S.wl);renderWL();renderSaveBtn()}
function renderSaveBtn(){const b=$("ov-save");if(!b||!S.cur)return;const inWl=S.wl.some(w=>key(...w)===S.cur);
  b.innerHTML=inWl?"\u2605 In watchlist":"\u2606 Save to watchlist";b.setAttribute("aria-pressed",String(inWl))}
function renderHead(){const q=S.quotes[S.cur];if(!q||!$("ov-px"))return;
  if(q.error){$("ov-err").innerHTML=`<div class="errbox" style="margin-top:10px">${esc(q.error)}</div>`;return}
  $("ov-err").innerHTML="";$("ov-nm").textContent=q.name||q.symbol;
  $("ov-chips").innerHTML=[q.exchange+":"+q.symbol,q.source].map(t=>`<span class="chip">${esc(t)}</span>`).join("")+(q.stale?`<span class="chip warn">No fresh tick while market is open</span>`:"");
  const old=S.lastPx[S.cur],el=$("ov-px");el.textContent="\u20b9"+f(q.price);
  if(old!=null&&old!==q.price){el.classList.remove("flash-up","flash-dn");void el.offsetWidth;el.classList.add(q.price>old?"flash-up":"flash-dn")}S.lastPx[S.cur]=q.price;
  $("ov-chg").innerHTML=`<span class="${sg(q.change_pct)}">${q.change==null?"":(q.change>=0?"+":"")+f(q.change)} (${pc(q.change_pct)})</span>`;
  $("ov-src").textContent=`${q.source} \u00b7 as of ${q.as_of_label}`}
async function selectSym(s,x){s=s.toUpperCase().replace(/\.(NS|BO)$/,"");const k=key(s,x);
  if(!S.quotes[k]){try{const q=await api(`/api/quote/${encodeURIComponent(s)}?ex=${x}`);S.quotes[k]={...q}}catch(e){showTab("overview");$("ov-err").innerHTML=`<div class="errbox" style="margin-top:10px">${esc(e.message)}</div>`;return}}
  S.cur=k;$("ex").value=x;renderWL();$("ov-nm").textContent=s;renderHead();loadChart();loadQuant();syncForm();renderSaveBtn()}
async function loadChart(){if(!S.cur)return;const [s,x]=S.cur.split(":"),mine=S.cur+S.range;
  try{const d=await api(`/api/chart/${encodeURIComponent(s)}?ex=${x}&range=${S.range}`);if(S.cur+S.range!==mine)return;drawChart(d)}
  catch(e){$("chart").innerHTML=`<p class="note">Chart unavailable: ${esc(e.message)}</p>`}}
const ema=(a,p)=>{const k=2/(p+1),o=[a[0]];for(let i=1;i<a.length;i++)o.push(a[i]*k+o[i-1]*(1-k));return o};
function drawChart(d){const c=d.c;if(c.length<2){$("chart").innerHTML='<p class="note">Not enough data for this range.</p>';return}
  const intra=d.interval.endsWith("m"),W=760,H=240,R=58,pw=W-R,e20=!intra&&c.length>=60?ema(c,20):null,e50=!intra&&c.length>=120?ema(c,50):null;
  const all=[...c,...(e50||[]),...(d.prev_close&&intra?[d.prev_close]:[])],lo=Math.min(...all),hi=Math.max(...all),X=i=>i/(c.length-1)*pw,Y=v=>H-18-(v-lo)/(hi-lo||1)*(H-30);
  const P=z=>z.map((v,j)=>(j?"L":"M")+X(j).toFixed(1)+","+Y(v).toFixed(1)).join("");
  const base=intra&&d.prev_close?d.prev_close:c[0],col=c.at(-1)>=base?"var(--bull)":"var(--bear)";
  let g="";for(let q=0;q<4;q++){const v=lo+(hi-lo)*q/3,y=Y(v);g+=`<line x1="0" x2="${pw}" y1="${y}" y2="${y}" stroke="var(--ln)" stroke-dasharray="3 4"/><text x="${pw+6}" y="${y+4}" fill="var(--mu)" font-size="11">${f(v)}</text>`}
  const tl=t=>new Date(t*1000).toLocaleString("en-IN",intra?{...IST,hour:"2-digit",minute:"2-digit",day:d.range==="5d"?"2-digit":undefined,month:d.range==="5d"?"short":undefined}:{...IST,day:"2-digit",month:"short",year:"2-digit"});
  $("chart").innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price chart ${esc(d.range)}">${g}
${intra&&d.prev_close?`<line x1="0" x2="${pw}" y1="${Y(d.prev_close)}" y2="${Y(d.prev_close)}" stroke="var(--mu)" stroke-width="1" stroke-dasharray="6 4"/>`:""}
${e50?`<path d="${P(e50)}" fill="none" stroke="var(--av)" stroke-width="1.5"/>`:""}${e20?`<path d="${P(e20)}" fill="none" stroke="var(--ac)" stroke-width="1.5"/>`:""}
<path d="${P(c)}" fill="none" stroke="${col}" stroke-width="2"/><text x="0" y="${H-2}" fill="var(--mu)" font-size="11">${tl(d.t[0])}</text><text x="${pw}" y="${H-2}" fill="var(--mu)" font-size="11" text-anchor="end">${tl(d.t.at(-1))}</text>
<line id="xh" y1="4" y2="${H-18}" stroke="var(--mu)" opacity="0"/></svg><div class="tip" id="tip" hidden></div>`;
  $("leg").innerHTML=`<span><i style="background:${col}"></i>Price</span>${intra&&d.prev_close?'<span>dashed = previous close</span>':""}${e20?'<span><i style="background:var(--ac)"></i>EMA 20</span>':""}${e50?'<span><i style="background:var(--av)"></i>EMA 50</span>':""}`;
  const svg=$("chart").firstElementChild,tip=$("tip"),xh=$("xh");
  svg.onmousemove=ev=>{const r=svg.getBoundingClientRect(),px=(ev.clientX-r.left)/r.width*W;if(px>pw||px<0)return;const i=Math.round(px/pw*(c.length-1));
    xh.setAttribute("x1",X(i));xh.setAttribute("x2",X(i));xh.setAttribute("opacity",.6);tip.hidden=false;tip.textContent=`${tl(d.t[i])}  \u20b9${f(c[i])}`;tip.style.left=Math.min(r.width-170,Math.max(0,X(i)/W*r.width-60))+"px"};
  svg.onmouseleave=()=>{tip.hidden=true;xh.setAttribute("opacity",0)}}

/* ---------- quant screen (kept from v1) ---------- */
function analyse(d){const c=d.close,e20=ema(c,20),e50=ema(c,50),last=c.at(-1),n=14;
  let g=0,l=0;for(let i=1;i<=n;i++){const x=c[i]-c[i-1];x>0?g+=x:l-=x}g/=n;l/=n;for(let i=n+1;i<c.length;i++){const x=c[i]-c[i-1];g=(g*(n-1)+Math.max(x,0))/n;l=(l*(n-1)+Math.max(-x,0))/n}
  const R=100-100/(1+g/(l||1e-9)),m12=ema(c,12),m26=ema(c,26),mac=m12.map((v,i)=>v-m26[i]),hist=mac.at(-1)-ema(mac,9).at(-1);
  let tr=0;for(let i=c.length-14;i<c.length;i++)tr+=Math.max(d.high[i]-d.low[i],Math.abs(d.high[i]-c[i-1]),Math.abs(d.low[i]-c[i-1]));
  const atr=tr/14,atrPct=atr/last*100,ret3m=c.length>63?last/c[c.length-64]-1:0,F=d.fundamentals,gs=[F.rev_growth,F.earn_growth].filter(x=>x!=null),gr=gs.length?gs.reduce((s,x)=>s+x,0)/gs.length:null;
  const sc={Value:F.pe==null||F.pe<=0?50:clamp(110-F.pe*2.4),Quality:F.roe==null?50:clamp(20+F.roe*250-(F.de||0)/10),Growth:gr==null?50:clamp(50+gr*200),
    Momentum:clamp(50+(e20.at(-1)>e50.at(-1)?15:-15)+(last>e50.at(-1)?10:-10)+Math.max(-15,Math.min(15,ret3m*100))+(R-50)*.3),Volatility:clamp(100-atrPct*20)};
  const w={Momentum:.3,Quality:.25,Growth:.15,Value:.15,Volatility:.15},comp=Object.keys(w).reduce((s,k)=>s+sc[k]*w[k],0);
  const v=comp>=60&&sc.Momentum>=55?"LONG":comp<=42&&sc.Momentum<=40?"SHORT":"AVOID",bull=[],bear=[],up=e20.at(-1)>e50.at(-1);
  (up?bull:bear).push(up?"EMA 20 above EMA 50 (uptrend)":"EMA 20 below EMA 50 (downtrend)");(last>e50.at(-1)?bull:bear).push(`Price \u20b9${f(last)} ${last>e50.at(-1)?"above":"below"} EMA 50`);
  if(R>70)bear.push(`RSI ${R.toFixed(0)} overbought`);else if(R<30)bull.push(`RSI ${R.toFixed(0)} oversold`);
  if(F.pe>0)(sc.Value>=60?bull:sc.Value<=35?bear:[]).push(`P/E ${F.pe.toFixed(1)} is ${sc.Value>=60?"reasonable":"rich"}`);
  if(F.roe!=null)(F.roe>=.15?bull:F.roe<.08?bear:[]).push(`ROE ${(F.roe*100).toFixed(1)}%`);if(F.de>150)bear.push(`Debt/equity ${F.de.toFixed(0)}% is high`);
  if(gr!=null)(gr>.1?bull:gr<0?bear:[]).push(`Growth ${(gr*100).toFixed(0)}% (revenue/earnings avg)`);if(atrPct>3.5)bear.push(`High volatility: ATR ${atrPct.toFixed(1)}% of price`);
  return {R,hist,atr,atrPct,e20:e20.at(-1),e50:e50.at(-1),last,sc,comp,v,bull:bull.length?bull:["No strong bullish signal"],bear:bear.length?bear:["No strong bearish signal"]}}
async function loadQuant(){if(!S.cur)return;const [s,x]=S.cur.split(":"),k=S.cur;$("quant").innerHTML='<span class="note">Loading daily bars\u2026</span>';
  try{const d=await api(`/api/stock/${encodeURIComponent(s)}?ex=${x}`);if(S.cur!==k)return;const a=analyse(d);S.quant[k]=a;
    $("ov-ind").innerHTML=[["RSI 14",a.R.toFixed(1),a.R>70?"Overbought":a.R<30?"Oversold":"Neutral"],["MACD hist",a.hist.toFixed(2),a.hist>0?"Bullish":"Bearish"],["EMA stack",a.e20>a.e50?"20 > 50":"20 < 50",a.last>a.e20?"Price above 20":"Price below 20"],["ATR 14","\u20b9"+f(a.atr),a.atrPct.toFixed(1)+"% of price"]].map(r=>`<div><span>${r[0]}</span><b>${r[1]}</b><span>${r[2]}</span></div>`).join("");
    $("quant").innerHTML=`<div class="verdict"><div><span class="tag ${a.v}">${a.v}</span></div><span class="note">Composite ${a.comp.toFixed(0)}/100 \u00b7 momentum 30%, quality 25%, growth/value/volatility 15% each \u00b7 last bar ${esc(d.last_bar)}</span></div>
${Object.entries(a.sc).map(([n,v])=>{const dd=v-50;return `<div class="fx"><span>${n}</span><div class="rail"><em style="${dd>=0?"left:50%":"right:50%"};width:${Math.abs(dd)}%;background:var(${dd>=0?"--bull":"--bear"})"></em></div><b>${v}</b></div>`}).join("")}
<div class="cols"><div><h3 style="color:var(--bull)">Bull signals</h3><ul>${a.bull.map(t=>`<li>${esc(t)}</li>`).join("")}</ul></div><div><h3 style="color:var(--bear)">Bear signals</h3><ul>${a.bear.map(t=>`<li>${esc(t)}</li>`).join("")}</ul></div></div>
<p class="note">Fast rule-based screen. For the full multi-agent debate with a final call, use the AI Research tab.</p>`}
  catch(e){$("quant").innerHTML=`<span class="note">Quant screen unavailable: ${esc(e.message)}</span>`}}

/* ---------- tabs ---------- */
function showTab(t){S.tab=t;document.querySelectorAll(".tabs button").forEach(b=>b.setAttribute("aria-selected",b.dataset.tab===t));$("p-overview").hidden=t!=="overview";$("p-research").hidden=t!=="research"}
document.querySelector(".tabs").onclick=e=>{const b=e.target.closest("button");if(b)showTab(b.dataset.tab)};

/* ---------- AI research ---------- */
const AGN={market:"Market",sentiment:"Sentiment",news:"News",fundamentals:"Fundamentals",bull:"Bull researcher",bear:"Bear researcher",research_manager:"Research manager",trader:"Trader",aggressive:"Aggressive",conservative:"Conservative",neutral:"Neutral",portfolio_manager:"Portfolio manager"};
const LANES=[["Analyst team",["market","sentiment","news","fundamentals"]],["Research team",["bull","bear","research_manager"]],["Trading",["trader"]],["Risk management",["aggressive","conservative","neutral"]],["Portfolio",["portfolio_manager"]]];
const SEC=["I. Analyst team reports","II. Research team decision","III. Trading team plan","IV. Risk management team","V. Portfolio manager decision"];
const SECI={market:0,sentiment:0,news:0,fundamentals:0,bull:1,bear:1,research_manager:1,trader:2,aggressive:3,conservative:3,neutral:3,portfolio_manager:4};
const LANGS=["English","Hindi","Hinglish","Marathi","Gujarati","Tamil","Bengali","Telugu"];
function buildResearch(){const c=S.cfg,d=c.defaults;
  $("p-research").innerHTML=`<section class="pn"><h2>Research filters</h2><div class="form">
<div><label for="r-sym">Symbol</label><input id="r-sym" type="text" autocomplete="off" placeholder="e.g. INFY"></div>
<div><label for="r-ex">Exchange</label><select id="r-ex"><option>NSE</option><option>BSE</option></select></div>
<div><label for="r-date">Analysis date</label><input id="r-date" type="date" max="${c.today}" value="${c.today}"></div>
<div><label for="r-lang">Output language</label><select id="r-lang">${LANGS.map(l=>`<option ${l===d.output_language?"selected":""}>${l}</option>`).join("")}</select></div>
<div class="full"><span class="lab">Analysts (each one fetches its own data)</span><div class="opts" id="r-an">${c.analysts.map(a=>`<label><input type="checkbox" value="${a}" checked> ${AGN[a]}</label>`).join("")}</div></div>
<div class="full"><span class="lab">Research depth (debate rounds for both bull/bear and the risk team)</span><div class="opts" id="r-depth">${Object.entries(c.depths).map(([k,v])=>`<label><input type="radio" name="dp" value="${k}" ${k==="shallow"?"checked":""}> ${k[0].toUpperCase()+k.slice(1)} (${v} round${v>1?"s":""})</label>`).join("")}</div><div class="sub" id="r-est"></div></div>
<div><label for="r-prov">LLM provider</label><select id="r-prov">${c.providers.map(p=>`<option value="${p.id}" ${p.id===d.llm_provider?"selected":""}>${esc(p.label)}${p.has_key?"":" (no key)"}</option>`).join("")}</select></div>
<div><label for="r-deep">Deep-thinking model (manager, trader, PM)</label><input id="r-deep" type="text" autocomplete="off"></div>
<div><label for="r-quick">Quick-thinking model (analysts, debaters)</label><input id="r-quick" type="text" autocomplete="off"></div>
<div class="full" id="r-msg"></div>
<div class="full toolbar"><button class="btn" id="r-go">Run analysis</button><button class="btn ghost" id="r-stop" hidden>Cancel run</button></div></div></section>
<section class="pn" id="r-deskp" hidden><h2 id="r-title">Trading desk</h2><div class="desk" id="r-desk"></div><div class="stats" id="r-stats"></div></section>
<section class="pn" id="r-decp" hidden><h2>Final decision</h2><div id="r-dec"></div></section>
<section class="pn rep" id="r-repp" hidden><div class="hd"><h2>Report</h2><div class="toolbar" id="r-tools"></div></div><div id="r-rep"></div></section>`;
  $("r-prov").onchange=provChanged;$("r-depth").onchange=est;$("r-an").onchange=est;$("r-go").onclick=startRun;$("r-stop").onclick=cancelRun;provChanged(true);est();syncForm()}
function provChanged(init){const p=S.cfg.providers.find(x=>x.id===$("r-prov").value),d=S.cfg.defaults;
  $("r-deep").value=p.id===d.llm_provider?d.deep_think_llm:p.deep;$("r-quick").value=p.id===d.llm_provider?d.quick_think_llm:p.quick;
  $("r-deep").placeholder=$("r-quick").placeholder="type the model id";
  $("r-msg").innerHTML=p.has_key?"":`<div class="warnbox">${esc(p.key_env)} is not set. Add it to the .env file next to server.py and restart the server, or pick another provider.</div>`;
  $("r-go").disabled=!p.has_key}
function est(){const n=[...$("r-an").querySelectorAll("input:checked")].length,r=S.cfg.depths[document.querySelector("input[name=dp]:checked").value];
  $("r-est").textContent=`About ${n+2*r+2+3*r+1} LLM calls (${n} analysts, ${2*r} debate turns, ${3*r} risk turns, manager, trader, portfolio manager). Deeper means slower and costlier.`}
function syncForm(){if(!$("r-sym")||(S.run&&["running","queued"].includes(S.run.status)))return;if(S.cur){const [s,x]=S.cur.split(":");$("r-sym").value=s;$("r-ex").value=x}}
async function startRun(){const body={symbol:$("r-sym").value.trim(),exchange:$("r-ex").value,trade_date:$("r-date").value,language:$("r-lang").value,provider:$("r-prov").value,
    deep_model:$("r-deep").value.trim(),quick_model:$("r-quick").value.trim(),depth:document.querySelector("input[name=dp]:checked").value,
    analysts:[...$("r-an").querySelectorAll("input:checked")].map(i=>i.value)};
  if(!body.symbol)return $("r-msg").innerHTML='<div class="errbox">Enter a symbol first.</div>';
  if(!body.analysts.length)return $("r-msg").innerHTML='<div class="errbox">Select at least one analyst.</div>';
  $("r-msg").innerHTML="";$("r-go").disabled=true;
  try{const {id}=await api("/api/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});resetRun(id);poll()}
  catch(e){$("r-msg").innerHTML=`<div class="errbox">${esc(e.message)}</div>`;$("r-go").disabled=false}}
async function cancelRun(){if(S.runId)await api(`/api/runs/${S.runId}/cancel`,{method:"POST"}).catch(()=>{})}
function resetRun(id){clearTimeout(S.pollT);S.runId=id;S.run=null;S.msgs=[];S.rendered=0;$("r-rep").innerHTML="";$("r-repp").hidden=true;$("r-decp").hidden=true}
async function poll(){clearTimeout(S.pollT);if(!S.runId)return;
  try{const s=await api(`/api/runs/${S.runId}?since=${S.msgs.length}`);S.msgs.push(...s.messages);S.run=s;renderRun();
    if(["queued","running"].includes(s.status))S.pollT=setTimeout(poll,1500);else{loadRuns();loadDlog()}}
  catch(e){$("r-msg").innerHTML=`<div class="errbox">Lost contact with the server: ${esc(e.message)}</div>`}}
function renderRun(){const s=S.run;if(!s)return;const active=["queued","running"].includes(s.status);
  $("r-deskp").hidden=false;$("r-title").textContent=`${s.name||s.symbol} \u00b7 ${s.exchange} \u00b7 ${s.trade_date}`;
  $("r-desk").innerHTML=LANES.map(([t,ids])=>{const rows=ids.filter(a=>a in s.agents);return rows.length?`<div class="lane"><h3>${t}</h3>${rows.map(a=>`<div class="ag ${s.agents[a]}"><i></i>${AGN[a]}</div>`).join("")}</div>`:""}).join("");
  const u=s.usage||{},el=Math.max(0,((s.finished?new Date(s.finished):new Date())-new Date(s.started))/1000);
  $("r-stats").innerHTML=`<span><span class="pill ${s.status}">${esc(s.status)}</span> ${esc(s.phase)}</span><span>${Math.floor(el/60)}m ${Math.floor(el%60)}s</span><span>${u.calls||0} LLM calls</span><span>${(u.tokens_in||0).toLocaleString("en-IN")} in / ${(u.tokens_out||0).toLocaleString("en-IN")} out tokens</span><span>${esc(s.params.llm_provider)}: ${esc(s.params.deep_think_llm)} / ${esc(s.params.quick_think_llm)}</span>`
    +(s.error?`<div class="errbox full" style="flex-basis:100%">${esc(s.error)}</div>`:"");
  $("r-stop").hidden=!active;$("r-go").disabled=active||!S.cfg.providers.find(p=>p.id===$("r-prov").value).has_key;
  renderDecision(s);renderReportNodes(s);$("prov").innerHTML=(s.provenance||[]).map(p=>`<tr><td>${esc(p.item)}</td><td>${esc(p.source)}</td><td class="${p.status==="ok"?"up":"dn"}">${esc(p.status)}</td></tr>`).join("")||'<tr><td colspan="3" class="note">Starts when a run begins</td></tr>'}
function renderDecision(s){const r=s.result;$("r-decp").hidden=!r;if(!r)return;const t=(l,v)=>`<div><span>${l}</span><b>${v==null?"\u2014":"\u20b9"+f(v)}</b></div>`,z=r.sizing;
  $("r-dec").innerHTML=`<div class="dec"><div><span class="tag r-${esc(r.rating)}">${esc(r.rating)}</span><div class="sub">Portfolio manager \u00b7 research manager: ${esc(r.research_rating)} \u00b7 trader: ${esc(r.action)}</div></div></div>
<div class="grid4">${t("Price when analysed",r.price)}${t("Entry",r.entry)}${t("Stop-loss",r.stop)}${t("Target",r.target)}</div>
${z?`<p class="note">Sizing check: risking \u20b9${f(z.risk_amt,0)} per \u20b9${f(z.capital,0)} of capital gives about ${z.qty} shares${z.rr?`, reward:risk ${z.rr.toFixed(2)}`:""}. Computed by code from the trader's levels, not by the LLM.</p>`:""}
${r.warnings.length?`<div class="warnbox">${r.warnings.map(esc).join("<br>")}</div>`:""}<p class="note">Educational output, not investment advice. Verify levels against the live price before acting.</p>`}
function renderReportNodes(s){$("r-repp").hidden=!S.msgs.length;if(!S.msgs.length)return;
  if(!$("r-rep").children.length)$("r-rep").innerHTML=SEC.map((t,i)=>`<details open data-s="${i}" hidden><summary>${t}</summary></details>`).join("");
  for(;S.rendered<S.msgs.length;S.rendered++){const m=S.msgs[S.rendered],box=$("r-rep").children[SECI[m.agent]??0],d=document.createElement("div");
    d.className=`msg ${m.agent} ${m.kind==="error"?"error":""}`;d.innerHTML=`<h4>${esc(m.label)}<small>${esc(m.at)}</small></h4><div class="md">${md(m.text)}</div>`;box.appendChild(d);box.hidden=false}
  if(s.has_report&&!$("r-tools").children.length){$("r-tools").innerHTML=`<a class="btn ghost" href="/api/runs/${encodeURIComponent(s.id)}/report?download=true">Download .md</a><button class="btn ghost" id="r-copy">Copy report</button>`;
    $("r-copy").onclick=async()=>{try{await navigator.clipboard.writeText(await (await fetch(`/api/runs/${encodeURIComponent(s.id)}/report`)).text());$("r-copy").textContent="Copied"}catch(e){$("r-copy").textContent="Copy failed"}}}
  if(!s.has_report)$("r-tools").innerHTML=""}

/* ---------- history ---------- */
async function loadRuns(){try{const r=await api("/api/runs");$("runs").innerHTML=r.length?r.slice(0,8).map(x=>`<button class="run" data-id="${esc(x.id)}"><div>${esc(x.symbol)}<small>${x.exchange} \u00b7 ${esc(x.trade_date)} \u00b7 ${esc(x.started.slice(11,16))}</small></div>${x.rating?`<span class="pill r-${esc(x.rating)}">${esc(x.rating)}</span>`:`<span class="pill ${esc(x.status)}">${esc(x.status)}</span>`}</button>`).join(""):'<span class="note">No runs yet. Open AI Research and run one.</span>'}catch(e){}}
$("runs").onclick=async e=>{const b=e.target.closest(".run");if(!b)return;resetRun(b.dataset.id);showTab("research");await poll()};
async function loadDlog(){try{const r=await api("/api/decisions");$("dlog").innerHTML=r.length?r.slice(0,8).map(x=>`<div class="run" style="cursor:default"><div>${esc(x.ysym)}<small>${esc(x.trade_date)} \u00b7 ${x.status==="resolved"?`${pc(x.raw_return*100)} (alpha ${pc(x.alpha*100)})`:"outcome pending"}</small></div><span class="pill r-${esc(x.rating)}">${esc(x.rating)}</span></div>`).join(""):'<span class="note">Finished runs are scored against Nifty/Sensex after the holding window and fed back into later calls.</span>'}catch(e){}}

/* ---------- boot ---------- */
(async()=>{buildOverview();
  try{S.cfg=await api("/api/config");buildResearch();renderMkt(S.cfg.market);S.market=S.cfg.market.state}catch(e){$("p-research").innerHTML=`<div class="errbox">Cannot reach the server: ${esc(e.message)}</div>`}
  renderWL();tickIndices();loadRuns();loadDlog();
  const [s,x]=S.wl[0]||["RELIANCE","NSE"];S.cur=key(s,x);$("ex").value=x;tickQuotes();selectSym(s,x);
  setInterval(()=>{if(S.tab==="overview"&&(S.range==="1d"||S.range==="5d")&&S.market!=="closed")loadChart()},20000)})();
