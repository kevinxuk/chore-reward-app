/**
 * PCGuardian 网页版赚时间 — Cloudflare Worker（单文件部署）
 * ============================================================
 * 部署（Cloudflare Dashboard → Workers → Create Worker → 粘贴本文件）：
 *   1. Settings → Variables（明文）：GIST_ID = 存放任务数据的 Gist ID
 *   2. Settings → Variables（加密 Secret）：
 *        GITHUB_TOKEN = 有该 Gist 写权限的 token（用于写入成绩）
 *        MIMO_KEY     = 小米 MiMo 平台的 API Key
 *   3. 家长端「🤖 AI设置」里把本 Worker 地址（https://xxx.workers.dev）
 *      填入「🌐 网页版赚时间」，保存后孩子电脑自动开启网页任务通道。
 *
 * 路由：
 *   GET  /                → 赚时间页面（手机/平板响应式）
 *   GET  /api/config      → 任务列表（朗读/单词 + 音频/视频 URL 清单）
 *   GET  /api/media?url=  → 音视频代理（绕过 CORS，供页面播放）
 *   POST /api/asr         → MiMo ASR（{audioBase64, lang}）
 *   POST /api/judge       → 出题/判分（{mode, target, heard}）
 *   POST /api/result      → 写成绩（追加到 Gist 的 web-earn-results.json）
 *   GET  /api/results     → 成绩列表（孩子电脑轮询入账）
 */

const GIST_API = "https://api.github.com/gists/";

const PAGE_HTML = `<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>🌟 赚使用时间</title>
<style>
 body{margin:0;background:#10141f;color:#e8ecf5;font-family:"Microsoft YaHei UI",sans-serif}
 .tabbar{display:flex;position:sticky;top:0;background:#0d1119;z-index:5}
 .tabbar button{flex:1;padding:12px 4px;border:0;background:#141b2b;color:#8fa0bd;font-size:15px;font-weight:600}
 .tabbar button.on{background:#3d3063;color:#ffe08a}
 .wrap{padding:16px;max-width:560px;margin:0 auto}
 .card{background:#1a2338;border-radius:12px;padding:16px;margin-bottom:14px}
 .card h3{margin:0 0 10px;color:#ffe08a;font-size:17px}
 .content{font-size:17px;line-height:1.9;white-space:pre-wrap}
 .btn{width:100%;padding:14px;border:0;border-radius:10px;font-size:16px;font-weight:700;
      color:#fff;background:#2e7d32;cursor:pointer;margin-top:10px}
 .btn.rec{background:#8a6d1f}
 .btn:disabled{opacity:.5}
 .word{font-size:40px;text-align:center;margin:18px 0;font-weight:700}
 .score{font-size:30px;text-align:center;color:#7ed957;font-weight:700;margin:10px}
 .msg{font-size:13px;color:#8fa0bd;margin-top:6px;min-height:1.2em}
 video,audio{width:100%;border-radius:10px;background:#000}
 .done{color:#7ed957;font-size:15px}
</style></head><body>
<div class="tabbar">
 <button data-t="read" class="on">📖 阅读</button><button data-t="words">🔤 单词</button>
 <button data-t="listen">🎧 音频</button><button data-t="watch">📺 视频</button>
</div>
<div class="wrap" id="main"><div class="card"><h3>加载中…</h3></div></div>
<script>
const $=s=>document.querySelector(s);
let CFG=null,tab="read",rec=null,chunks=[],recing=false,state={};
function api(p,o){return fetch(p,o).then(r=>r.json()).then(j=>{if(j.error)throw new Error(j.error);return j})}
function render(){
 document.querySelectorAll(".tabbar button").forEach(b=>b.classList.toggle("on",b.dataset.t===tab));
 const m=$("#main");m.innerHTML="";
 const list=CFG?CFG[tab]||[]:[];
 if(!list.length){m.innerHTML='<div class="card"><h3>该分类暂无任务</h3><p class="msg">家长还没有发布'+tabName(tab)+'任务</p></div>';return}
 list.forEach((t,i)=>{
  const d=t.done?{c:"#2e5d3a",t:"✅ 已完成"}:{c:"#1a2338",t:""};
  const card=document.createElement("div");card.className="card";
  card.style.background=d.c;card.innerHTML="<h3>"+(i+1)+". "+t.name+
    (t.minutes?' <small style="color:#8fa0bd">· '+t.minutes+' 分钟</small>':"")+"</h3>";
  if(t.done){card.innerHTML+='<div class="done">已完成，等待电脑同步</div>'}
  m.appendChild(card);
  if(t.done)return;
  if(tab==="read"){
   const c=document.createElement("div");c.className="content";c.textContent=t.content;card.appendChild(c);
   addRecBtn(card,t);
  }else if(tab==="words"){
   const w=document.createElement("div");w.className="word";w.textContent=t.word;card.appendChild(w);
   addRecBtn(card,t);
  }else{
   const el=document.createElement(tab==="watch"?"video":"audio");
   el.controls=false;el.src="/api/media?url="+encodeURIComponent(t.url);card.appendChild(el);
   const st=document.createElement("div");st.className="msg";st.textContent="点击播放，听/看完自动记录";card.appendChild(st);
   const b=document.createElement("button");b.className="btn";b.textContent="▶ 开始"+(tab==="watch"?"观看":"收听");
   let played=0,last=0;
   b.onclick=()=>{ if(el.paused){el.play();last=Date.now();b.textContent="⏸ 暂停";
      const iv=setInterval(()=>{ if(el.paused||el.ended){clearInterval(iv);return}
        played+=(Date.now()-last)/1000;last=Date.now();
        st.textContent="已累计 "+Math.floor(played/60)+" 分 "+Math.floor(played%60)+" 秒 / 需 "+Math.ceil((t.duration||0))+" 秒";
      },500);
      el.onended=()=>{clearInterval(iv);finish(t,played)};}
     else{el.pause()} };
   card.appendChild(b);
  }
 });
}
function tabName(t){return {read:"朗读",words:"单词",listen:"音频",watch:"视频"}[t]||""}
function addRecBtn(card,t){
 const b=document.createElement("button");b.className="btn";b.textContent="🎤 按住开始朗读 → 点击结束评分";
 b.onclick=async()=>{
  if(!recing){
   chunks=[];const stream=await navigator.mediaDevices.getUserMedia({audio:true});
   rec=new MediaRecorder(stream);rec.ondataavailable=e=>chunks.push(e.data);rec.start();
   recing=true;b.classList.add("rec");b.textContent="⏹ 朗读完成，点击评分";
  }else{
   rec.stop();recing=false;b.disabled=true;b.textContent="⏳ 评分中…";
   const blob=new Blob(chunks,{type:rec.mimeType||"audio/webm"});
   const b64=btoa(String.fromCharCode(...new Uint8Array(blob).slice(0,8*1024*1024)));
   try{
    const asr=await api("/api/asr",{method:"POST",body:JSON.stringify({audioBase64:b64,lang:t.lang||"en"})});
    const j=await api("/api/judge",{method:"POST",body:JSON.stringify(
        {mode:tab==="read"?"read":"word",target:t.content||t.word,heard:asr.text})});
    const s=document.createElement("div");s.className="score";s.textContent=j.score+" 分";
    s.innerHTML=j.score+" 分"+(j.score>=(t.threshold||80)?' <span class="done">🎉 通过！奖励将由电脑自动发放</span>':"");
    card.appendChild(s);b.remove();
    if(j.score>=(t.threshold||80)) await api("/api/result",{method:"POST",body:JSON.stringify(
        {id:t.id,kind:tab,name:t.name,minutes:t.minutes})});
   }catch(e){const s=document.createElement("div");s.className="msg";s.textContent="❌ "+e.message;card.appendChild(s);
     b.disabled=false;b.textContent="🎤 再试一次";}
  }
 };
 card.appendChild(b);
}
document.querySelectorAll(".tabbar button").forEach(b=>b.onclick=()=>{tab=b.dataset.t;render()});
api("/api/config").then(c=>{CFG=c;render()}).catch(e=>{
 $("#main").innerHTML='<div class="card"><h3>加载失败</h3><p class="msg">'+e.message+'</p></div>'});
</script></body></html>`;

function gistHeaders() {
  return { "Authorization": "Bearer " + GITHUB_TOKEN, "Content-Type": "application/json", "User-Agent": "PCGuardian-Worker" };
}
async function gistFile(name) {
  const r = await fetch(GIST_API + GIST_ID, { headers: gistHeaders() });
  if (!r.ok) throw new Error("Gist 读取失败 " + r.status);
  const j = await r.json();
  const f = j.files[name];
  return f && f.content ? JSON.parse(f.content) : null;
}
async function putGistFile(name, obj) {
  const body = JSON.stringify({ files: { [name]: { content: JSON.stringify(obj, null, 2) } } });
  const r = await fetch(GIST_API + GIST_ID, { method: "PATCH", headers: gistHeaders(), body });
  if (!r.ok) throw new Error("Gist 写入失败 " + r.status);
}
const J = (o, code = 200) => new Response(JSON.stringify(o), { status: code, headers: { "Content-Type": "application/json; charset=utf-8" } });
const HTML = new Response(PAGE_HTML, { headers: { "Content-Type": "text/html; charset=utf-8" } });

async function mimoChat(messages) {
  const r = await fetch("https://api.xiaomimimo.com/v1/chat/completions", {
    method: "POST",
    headers: { "Authorization": "Bearer " + MIMO_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({ model: "mimo-v2.5", messages, temperature: 0.2 })
  });
  if (!r.ok) throw new Error("MiMo " + r.status);
  const j = await r.json();
  return (j.choices || [{}])[0].message?.content || "";
}
function parseJsonLoose(s) {
  if (!s) return null; s = s.trim();
  const a = s.indexOf("{"), b = s.lastIndexOf("}");
  if (a < 0 || b <= a) return null;
  try { return JSON.parse(s.slice(a, b + 1)); } catch (e) { return null; }
}

export default {
  async fetch(req) {
    const url = new URL(req.url);
    try {
      if (url.pathname === "/" ) return HTML;
      if (url.pathname === "/api/config") {
        const data = await gistFile("chore-data.json");
        const web = await gistFile("web-earn-tasks.json").catch(() => null);
        const disp = ((data.config || {}).dispatch || {}).tasks || [];
        const child = data.childName || "";
        const read = [], words = [];
        for (const t of disp) {
          if (t.kind !== "read" && t.kind !== "words") continue;
          (t.kind === "read" ? read : words).push({
            id: "dp-" + t.id, name: t.name, minutes: Math.max(10, Math.round(
              (t.kind === "read" ? String(t.content || "").replace(/\s/g, "").length / 250 * 10
                                 : (Array.isArray(t.content) ? t.content.length : 0) * 0.5))),
            content: t.kind === "read" ? String(t.content || "") : "",
            word: t.kind === "words" ? (Array.isArray(t.content) ? t.content[0] : "") : "",
            lang: t.kind === "words" ? "en" : "zh", threshold: t.threshold || 80
          });
        }
        const cfg = { child, read, words, listen: (web && web.listen) || [], watch: (web && web.watch) || [] };
        // 已完成标记（web-earn-results.json 里已有的 id 不再出现）
        const done = new Set(((await gistFile("web-earn-results.json").catch(() => null)) || {}).done || []);
        for (const k of ["read", "words", "listen", "watch"])
          cfg[k] = cfg[k].map(t => ({ ...t, done: done.has(t.id) }));
        return J(cfg);
      }
      if (url.pathname === "/api/media") {
        const u = url.searchParams.get("url");
        if (!u || !/^https:\/\//.test(u)) return J({ error: "仅支持 https 直链" }, 400);
        return fetch(u);
      }
      if (url.pathname === "/api/asr" && req.method === "POST") {
        const b = await req.json();
        const r = await fetch("https://api.xiaomimimo.com/v1/chat/completions", {
          method: "POST",
          headers: { "Authorization": "Bearer " + MIMO_KEY, "Content-Type": "application/json" },
          body: JSON.stringify({ model: "mimo-v2.5-asr", messages: [{ role: "user", content: [
            { type: "input_audio", input_audio: { data: b.audioBase64 } }], asr_options: { language: b.lang || "en" } }])
        });
        const j = await r.json();
        const text = ((j.choices || [{}])[0].message || {}).content || "";
        return J({ text: text.trim() });
      }
      if (url.pathname === "/api/judge" && req.method === "POST") {
        const b = await req.json();
        const sys = b.mode === "read"
          ? "你是小学朗读评分老师，只输出JSON。孩子朗读课文，语音识别可能有错别字。按整体准确度打0-100分。"
          : "你是英语听力判断助手，只输出JSON。识别结果可能把英文误写成中文或拼错。";
        const usr = b.mode === "read"
          ? ('课文："' + b.target + '"\n孩子朗读的识别结果："' + b.heard + '"\n打0-100分。输出 {"score": 数字}')
          : ('孩子正在读单词 "' + b.target + '"。识别结果："' + b.heard + '"\n这段识别是否表示孩子读对了？输出 {"score": 数字}（读对=100，接近=60-89，不对=<30）');
        const out = parseJsonLoose(await mimoChat([
          { role: "system", content: sys }, { role: "user", content: usr }]));
        return J({ score: Math.max(0, Math.min(100, (out && out.score) || 0)) });
      }
      if (url.pathname === "/api/result" && req.method === "POST") {
        const b = await req.json();
        if (!b.id || !b.kind || !b.minutes) return J({ error: "参数不全" }, 400);
        const doc = (await gistFile("web-earn-results.json").catch(() => null)) || { done: [], results: [] };
        if ((doc.done || []).includes(b.id)) return J({ ok: true, dup: true });
        doc.done = doc.done || []; doc.results = doc.results || [];
        doc.done.push(b.id);
        doc.results.push({ ...b, ts: Date.now() });
        await putGistFile("web-earn-results.json", doc);
        return J({ ok: true });
      }
      if (url.pathname === "/api/results") {
        const doc = (await gistFile("web-earn-results.json").catch(() => null)) || { results: [] };
        return J({ results: doc.results || [], done: doc.done || [] });
      }
      return J({ error: "not found" }, 404);
    } catch (e) {
      return J({ error: String(e.message || e) }, 500);
    }
  }
};
