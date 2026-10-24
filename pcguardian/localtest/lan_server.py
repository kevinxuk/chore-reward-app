# -*- coding: utf-8 -*-
"""lan_server.py —— 局域网联调服务器（手机=家长端，本机=儿童端）
================================================================
端口 8123，绑定 0.0.0.0：
  ① 静态托管家长端网页（../仓库目录 的 index.html 等）；
     对 index.html 运行时注入「同步桥」脚本（不改动仓库文件）：
     打开时拉取 /api/data 覆盖本地，每次 saveState 后 0.5 秒 POST 回服务器；
  ② /api/data  ↔  C:/PCGuardian/localtest/chore-data.json（儿童端的数据源）：
     GET 返回当前数据，POST 校验 JSON 后写盘（儿童端 3~5 秒内读到）；
  ③ /v1/chat/completions  mock 对话模型（问答任务的出题/判题，判分固定 88）；
     /v1/audio/transcriptions mock 云端识别（儿童端本地引擎优先，一般用不到）。

用法：python lan_server.py     （Ctrl+C 停止）
"""
import json
import urllib.request
import urllib.error
import os
import re
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

REPO = r"C:\Users\Administrator\Documents\1mycode\chore-reward-app"
DATA_FILE = r"C:\PCGuardian\localtest\chore-data.json"
AI_RESULTS_FILE = r"C:\PCGuardian\localtest\chore-ai-results.json"
PORT = 8123

SYNC_BRIDGE = """
<script>
/* 局域网测试同步桥（lan_server.py 注入；不影响正式版）v2：防刷新循环 + 错误上报 */
(function(){
  if (location.protocol === 'file:') return;
  window.__lanSync = true;   // 页面可据此使用本地测试服务器通道
  // 手机端 JS 错误上报（写进 lan-server.log，方便定位“按钮没反应”这类问题）
  window.addEventListener('error', function(e){
    try {
      fetch('/api/log', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({msg: String(e.message), src: (e.filename||'') + ':' + (e.lineno||0)})});
    } catch(_) {}
  });
  var lastReload = parseInt(document.cookie.replace(/(?:^|; )lanSyncAt=([^;]*).*/, '$1') || '0', 10);
  fetch('/api/data').then(function(r){return r.ok ? r.text() : null;}).then(function(t){
    if (!t) return;
    var cur = null;
    try { cur = localStorage.getItem('chore-app-data'); } catch(e) {}
    if (cur === t) return;
    var newer = true;
    try { newer = (JSON.parse(t).updatedAt||'') >= (cur ? (JSON.parse(cur).updatedAt||'') : ''); } catch(e){}
    if (!newer) return;
    try { localStorage.setItem('chore-app-data', t); } catch(e) { return; }
    // 15 秒内最多 reload 一次（cookie 节流，防隐私模式/存储异常导致刷新循环）
    var now = Date.now();
    if (now - lastReload > 15000) {
      document.cookie = 'lanSyncAt=' + now + '; path=/; max-age=60';
      location.reload();
    }
  }).catch(function(){});
  function pullResults(){
    fetch('/api/airesults').then(function(r){ return r.ok ? r.json() : null; }).then(function(j){
      if (!j) return;
      window.__aiResults = j.results || [];
      try { if (window.__dpRefresh) window.__dpRefresh(); } catch(e) {}
    }).catch(function(){});
  }
  pullResults();
  setInterval(pullResults, 20000);
  var timer = null;
  try {
    if (typeof window.saveState === 'function') {
      var _save = window.saveState;
      window.saveState = function(){
        _save.apply(this, arguments);
        clearTimeout(timer);
        timer = setTimeout(function(){
          var body = null;
          try { body = localStorage.getItem('chore-app-data'); } catch(e) {}
          if (body) fetch('/api/data', {method:'POST', headers:{'Content-Type':'application/json'},
                                        body: body}).catch(function(){});
        }, 500);
      };
    }
  } catch(e) {}
})();
</script>
"""

def _load_ai_cfg():
    """读孩子端同款 AI 配置：主数据 config.ai.cfgUrl → 本地文件或 URL。"""
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            url = ((json.load(f).get("config") or {}).get("ai") or {}).get("cfgUrl")
    except Exception:
        url = None
    if not url:
        raise RuntimeError("主数据缺少 AI 配置 (config.ai.cfgUrl)")
    low = url[:8].lower()
    path = None
    if low.startswith("file:///"):
        path = url[8:]
    elif low.startswith("file://"):
        path = url[7:]
    elif len(url) > 2 and url[1] == ":":
        path = url
    if path:
        with open(path.replace("/", os.sep), encoding="utf-8") as f:
            return json.load(f)
    req = urllib.request.Request(url, headers={"User-Agent": "lan_server"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

POEM_ANCHORS = {
    1: "《咏鹅》骆宾王、《静夜思》李白、《悯农（其二）》李绅、《春晓》孟浩然、《村居》高鼎",
    2: "《登鹳雀楼》王之涣、《望庐山瀑布》李白、《夜宿山寺》李白、《敕勒歌》北朝民歌、《梅花》王安石",
    3: "《山行》杜牧、《赠刘景文》苏轼、《夜书所见》叶绍翁、《望天门山》李白、《饮湖上初晴后雨》苏轼、《望洞庭》刘禹锡、《咏柳》贺知章、《春日》朱熹、《乞巧》林杰、《嫦娥》李商隐",
    4: "《题西林壁》苏轼、《暮江吟》白居易、《出塞》王昌龄、《凉州词》王翰、《夏日绝句》李清照、《别董大》高适",
    5: "《蝉》虞世南、《示儿》陆游、《题临安邸》林升、《己亥杂诗》龚自珍、《山居秋暝》王维、《枫桥夜泊》张继",
    6: "《宿建德江》孟浩然、《六月二十七日望湖楼醉书》苏轼、《西江月·夜行黄沙道中》辛弃疾、《过故人庄》孟浩然、《春夜喜雨》杜甫、《回乡偶书》贺知章",
}

GRADE_CN = {1: "一年级", 2: "二年级", 3: "三年级", 4: "四年级", 5: "五年级",
            6: "六年级", 7: "七年级", 8: "八年级", 9: "九年级"}

AI_CFG_FILE = os.path.join(os.path.dirname(DATA_FILE), "ai-config.json")

def _ai_chat_once(cfg, messages, temperature=0.0, timeout=30):
    body = json.dumps({"model": cfg.get("chatModel"), "messages": messages,
                       "temperature": temperature}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request((cfg.get("endpoint") or "").rstrip("/") + "/chat/completions",
                                 data=body,
                                 headers={"Authorization": "Bearer " + (cfg.get("apiKey") or ""),
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    return ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

def _ai_generate(cfg, p):
    g = GRADE_CN.get(int(p.get("grade") or 3), "三年级")
    if p.get("type") == "poem":
        sys_u = "你是小学语文老师。你只输出用于朗读的古诗词材料，绝不输出解释、点评或生成说明。"
        count = max(1, min(6, int(p.get("count") or 3)))
        anchors = POEM_ANCHORS.get(int(p.get("grade") or 3),
                                   "《静夜思》李白、《春晓》孟浩然、《登鹳雀楼》王之涣")
        u = ("请从中国小学%s语文教材中选取%d首**真实存在的**经典古诗词（必须是教材里的名篇，"
             "例如：%s。严禁虚构、改编或拼凑任何诗句；每首诗你必须百分之百确定原文无误），"
             "生成一份朗读材料。每首格式如下："
             "《诗名》 朝代·作者，然后另起一行输出诗文原文（保留标点，必须与教材原文完全一致），"
             "每首之间空一行。所有诗加起来正文不少于%d字。") % (g, count, anchors, int(p.get("words") or 120))
        return _ai_chat_once(cfg, [{"role": "system", "content": sys_u},
                                   {"role": "user", "content": u}], temperature=0.3, timeout=120)
    if p.get("type") == "words":
        sys_u = "你是小学英语老师，只输出单词列表，每行一个，不要编号、音标或中文解释。"
        u = "请列出中国九年义务教育%s学生需要掌握的英语单词，共%d个。" % (g, int(p.get("count") or 10))
    else:
        sys_u = "你是小学语文老师，为学生的朗读练习生成材料。只输出正文，不要标题、序号或解释。"
        u = ("请生成一篇适合%s小学生朗读的中文短文：全文约%d字，分成%d个自然段（段与段之间空一行）。"
             "内容积极向上、贴近孩子生活，句子简短、标点完整。"
             % (g, int(p.get("words") or 200), max(1, int(p.get("paras") or 2))))
    return _ai_chat_once(cfg, [{"role": "system", "content": sys_u},
                                {"role": "user", "content": u}], temperature=0.8, timeout=120)

def _load_ai_results():
    try:
        with open(AI_RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"results": []}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=REPO, **kw)

    def log_message(self, fmt, *args):
        ip = self.client_address[0]
        sys.stderr.write("[%s] %s\n" % (ip, fmt % args))

    # ---------- 静态 + 注入 ----------
    def do_GET(self):
        if self.path.split("?")[0] == "/api/data":
            return self._send_json_file()
        if self.path.split("?")[0] == "/api/airesults":
            return self._json(_load_ai_results())
        if self.path.split("?")[0] == "/api/aicfg":
            try:
                return self._json(_load_ai_cfg())
            except Exception as e:
                return self._json({"error": str(e)}, 404)
        if self.path.split("?")[0] in ("/", "/index.html"):
            return self._serve_index_injected()
        return super().do_GET()

    def _serve_index_injected(self):
        try:
            with open(os.path.join(REPO, "index.html"), "rb") as f:
                html = f.read().decode("utf-8")
            if "</body>" in html:
                html = html.replace("</body>", SYNC_BRIDGE, 1)
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _send_json_file(self):
        try:
            with open(DATA_FILE, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self._json({"error": "no data"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ---------- 数据回写 + mock AI ----------
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        path = self.path.split("?")[0]
        if path == "/api/data":
            try:
                doc = json.loads(body.decode("utf-8"))
                if not (isinstance(doc, dict) and doc.get("version") == 1 and isinstance(doc.get("config"), dict)):
                    return self._json({"error": "格式不符"}, 400)
                with open(DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=2)
                print("[sync] 数据已回写（dispatch=%d 项, updatedAt=%s）"
                      % (len((doc.get("config", {}).get("dispatch", {}) or {}).get("tasks", []) or []),
                         doc.get("updatedAt")))
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/aicfg":
            try:
                try:
                    cfg = json.loads(body.decode("utf-8"))
                except UnicodeDecodeError:
                    cfg = json.loads(body.decode("gbk"))
                if not (isinstance(cfg, dict) and cfg.get("endpoint") and cfg.get("apiKey") and cfg.get("chatModel")):
                    return self._json({"error": "配置不完整（endpoint/apiKey/chatModel）"}, 400)
                with open(AI_CFG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                print("[aicfg] AI 配置已保存（测试服务器）")
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/ai/test":
            try:
                try:
                    cfg = json.loads(body.decode("utf-8"))
                except UnicodeDecodeError:
                    cfg = json.loads(body.decode("gbk"))
                text = _ai_chat_once(cfg, [{"role": "user", "content": "回复ok"}])
                return self._json({"ok": True, "sample": text[:50]})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:200]}, 200)
        if path == "/api/ai/generate":
            try:
                try:
                    p = json.loads(body.decode("utf-8"))
                except UnicodeDecodeError:
                    p = json.loads(body.decode("gbk"))
                cfg = _load_ai_cfg()
                text = _ai_generate(cfg, p)
                if p.get("type") == "words":
                    words = [w.strip() for w in text.splitlines() if w.strip()]
                    return self._json({"words": words})
                return self._json({"text": text.strip()})
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        if path == "/api/airesult":
            try:
                try:
                    j = json.loads(body.decode("utf-8"))
                except UnicodeDecodeError:
                    j = json.loads(body.decode("gbk"))
                doc = _load_ai_results()
                results = [x for x in (doc.get("results") or []) if x.get("taskId") != j.get("taskId")]
                results.append(j)
                doc["results"] = results
                doc["updatedAt"] = __import__("time").time()
                with open(AI_RESULTS_FILE, "w", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=2)
                print("[airesult] 回执：%s %s分" % (j.get("name"), j.get("score")))
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/log":
            try:
                j = json.loads(body.decode("utf-8", "ignore"))
                print("[js-error][%s] %s (%s)" % (self.client_address[0], j.get("msg"), j.get("src")))
            except Exception:
                print("[js-error][%s] %r" % (self.client_address[0], body[:200]))
            return self._json({"ok": True})
        if path == "/v1/chat/completions":
            return self._mock_chat(body)
        if path == "/v1/audio/transcriptions":
            canned = "【mock识别】本机模型未安装，此为假结果"
            return self._json({"text": canned, "duration": 6.0,
                               "words": [{"word": c, "start": i * 0.3, "end": i * 0.3 + 0.25}
                                         for i, c in enumerate(canned)]})
        return self._json({"error": "not found"}, 404)

    def _mock_chat(self, body):
        raw = body.decode("utf-8", "ignore")
        content = ""
        try:
            msgs = json.loads(body.decode("utf-8")).get("messages", [])
            content = (msgs[-1].get("content") or "") if msgs else ""
        except Exception:
            pass
        if "古诗词" in raw and ("教材" in raw or "生成" in raw):
            reply = "《山行》 唐·杜牧\n远上寒山石径斜，白云生处有人家。停车坐爱枫林晚，霜叶红于二月花。\n\n《赠刘景文》 宋·苏轼\n荷尽已无擎雨盖，菊残犹有傲霜枝。一年好景君须记，最是橙黄橘绿时。\n\n《夜书所见》 宋·叶绍翁\n萧萧梧叶送寒声，江上秋风动客情。知有儿童挑促织，夜深篱落一灯明。"
        elif "短文" in raw and "生成" in raw:
            reply = "秋天的风吹过了操场，树叶像小船一样飘下来。我们捡了很多金黄色的叶子，夹在课本里做书签。\n\n傍晚的时候，天空出现了美丽的晚霞。我和小伙伴坐在台阶上，看云变成了小狗和小马。\n\n晚上，秋虫在草丛里唱歌。妈妈说，秋天是一个收获的季节。"
        elif "义务教育" in raw or ("单词" in raw and "列出" in raw):
            import re as _re
            m = _re.search("共(\d+)个", raw) or _re.search(r'"count"\s*:\s*(\d+)', raw)
            n = int(m.group(1)) if m else 10
            pool = ["apple", "tiger", "happy", "water", "smile", "green", "jump",
                    "bread", "cloud", "star", "milk", "bird", "book", "tree"]
            reply = chr(10).join(pool[i % len(pool)] for i in range(n))
        elif "questions" in raw or "questions" in content or "得分要点" in raw or "出题" in raw:
            reply = json.dumps({"questions": [
                {"q": "短文里提到了哪些颜色？", "points": ["黄色", "红色", "金黄色", "橙红色"]},
                {"q": "秋天的雨被比作了什么？", "points": ["钥匙", "颜料"]},
                {"q": "你最喜欢秋天的什么？为什么？", "points": ["说出喜欢的事物", "说出理由"]}]}, ensure_ascii=False)
        else:
            reply = json.dumps({"score": 88, "reason": "答到了要点（mock 判分）"}, ensure_ascii=False)
        return self._json({"choices": [{"message": {"role": "assistant", "content": reply}}]})

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

if __name__ == "__main__":
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if not os.path.exists(DATA_FILE):
        open(DATA_FILE, "w", encoding="utf-8").write('{"version":1,"config":{"tasks":[]},"records":{}}')
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("=" * 56)
    print(" 局域网联调服务器已启动（Ctrl+C 停止）")
    print("  手机家长端 : http://%s:%d/index.html" % (lan_ip(), PORT))
    print("  本机家长端 : http://127.0.0.1:%d/index.html" % PORT)
    print("  数据文件   : %s（儿童端 3~5 秒内读取）" % DATA_FILE)
    print("  mock 对话  : http://127.0.0.1:%d/v1 （问答任务判分固定 88）" % PORT)
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
