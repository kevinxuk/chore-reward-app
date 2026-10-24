# -*- coding: utf-8 -*-
"""PCVoiceScore / AI 评分 —— 本地自动化测试
覆盖：评分公式纯函数、朗读/单词完整流程（fake ASR）、防代读复测、问答、
     mock OpenAI 服务器 E2E（--wav 子进程）、尝试次数、guardian 集成（钱包/解锁条件）。
运行：python test_ai.py   （本目录；自动拷贝最新 guardian.pyw / PCVoiceScore.pyw 为被测对象）
"""
import importlib.util, json, os, random, shutil, subprocess, sys, threading, time, io, wave

HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.dirname(HERE)
APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PCGuardian")

# ---- 隔离影子副本 ----
BAK = APPDATA + ".testbak"
had_bak_dir = os.path.exists(APPDATA)
if had_bak_dir:
    shutil.rmtree(BAK, ignore_errors=True)
    shutil.move(APPDATA, BAK)

shutil.copy(os.path.join(PROD, "guardian.pyw"), os.path.join(HERE, "guardian.pyw"))
shutil.copy(os.path.join(PROD, "PCVoiceScore.pyw"), os.path.join(HERE, "PCVoiceScore.pyw"))
for f in ("guardian.session.json", "guardian.reward.session.json", "guardian.spent.json",
          "guardian.aiscore.json", "guardian.log"):
    p = os.path.join(HERE, f)
    if os.path.exists(p):
        os.remove(p)
for f in ("guardian.aiscore.json",):
    p = os.path.join(APPDATA, f)
    if os.path.exists(p):
        os.remove(p)

sys.argv = [os.path.join(HERE, "test_ai.py"), "--demo"]
# 本地 ASR 模型目录：优先用生产目录（被测代码默认找自己旁边，测试环境指过去）
_models_prod = os.path.join(PROD, "models")
if os.path.isdir(_models_prod):
    os.environ["PCGUARDIAN_MODELS"] = _models_prod

def _imp(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

G = _imp("g_guardian", os.path.join(HERE, "guardian.pyw"))
S = _imp("g_score", os.path.join(HERE, "PCVoiceScore.pyw"))

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else "FAIL  ") + name + (("   << " + str(extra) + " >>") if (extra and not cond) else ""))

READ_CONTENT = ("秋天的雨，是一把钥匙。它带着清凉和温柔，轻轻地，轻轻地，趁你没留意，"
               "把秋天的大门打开了。秋天的雨，有一盒五彩缤纷的颜料。你看，它把黄色给了银杏树，"
               "把红色给了枫树，把金黄色给了田野，把橙红色给了果树。")
READ_TASK = {"id": "dpx1", "kind": "read", "name": "朗读《秋天的雨》", "minutes": 20,
             "content": READ_CONTENT, "threshold": 80, "bindTo": "t13"}
WORDS_TASK = {"id": "dpx2", "kind": "words", "name": "Unit3 单词背诵", "minutes": 10,
              "content": ["think", "apple", "car"], "threshold": 80}
AI_CFG_TEST = os.path.join(HERE, "ai-config-test.json")
with open(AI_CFG_TEST, "w", encoding="utf-8") as f:
    json.dump({"endpoint": "http://127.0.0.1:8199/v1", "apiKey": "test-key-local",
               "asrModel": "mock-asr", "chatModel": "mock-chat", "fluentTs": True}, f)
# 子进程 E2E 用独立数据文件（不占用联调种子 chore-data.json）
E2E_DATA = os.path.join(HERE, "ai-e2e-data.json")
with open(E2E_DATA, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "childName": "测试",
               "config": {"computer": {"minutes": 30, "unlock": False, "required": ["t13", "t17"]},
                          "dispatch": {"tasks": [READ_TASK, WORDS_TASK]},
                          "ai": {"cfgUrl": AI_CFG_TEST.replace(chr(92), "/")},
                          "tasks": [
                              {"id": "t13", "name": "课外阅读30分钟"},
                              {"id": "t17", "name": "背单词/古诗词（10个）"}]},
               "records": {}}, f, ensure_ascii=False)

class FakeIO:
    """recordings: 预设录音队列；follow=True 时按"当前提示内容"自动生成完美朗读，
    mishear: {提示文本→实际听成} 用于模拟读错。"""
    def __init__(self, recordings=None, confirm=False, follow=False, mishear=None, dur_scale=3.5):
        self.recordings = list(recordings or [])
        self.confirm_val = confirm
        self.msgs = []
        self.follow = follow
        self.mishear = mishear or {}
        self.dur_scale = dur_scale
        self.said = []          # follow 模式：每次"说"出的文本（供 asr 消费）
        self.current = ""
    def message(self, title, lines):
        self.msgs.append(title)
        if lines:
            self.current = str(lines[0])
    def get_recording(self, max_seconds, hint="", delay=0.0):
        if self.follow:
            txt = self.mishear.get(self.current, self.current)
            self.said.append(txt)
            return (b"wav", max(1.0, len(txt) / self.dur_scale))
        return self.recordings.pop(0) if self.recordings else (None, 0.0)
    def confirm(self, text):
        return self.confirm_val
    def status(self, text):
        self.statuses = getattr(self, "statuses", [])
        self.statuses.append(text)
    def continue_(self):
        self.continues = getattr(self, "continues", 0) + 1
    def pause(self, seconds):
        self.pauses = getattr(self, "pauses", 0) + 1
    def speak_word(self, word):
        self.speak_calls = getattr(self, "speak_calls", []) + [(self.current, word)]
    def aborted(self):
        return False

class FollowASR:
    """与 FakeIO(follow=True) 配套：把 io.said 的内容按序作为识别结果。"""
    def __init__(self, fio, words=True):
        self.fio = fio; self.use_words = words
    def __call__(self, wav, dur):
        txt = self.fio.said.pop(0) if self.fio.said else ""
        words = None
        if self.use_words:
            words = [[ch, i * 0.3, i * 0.3 + 0.25] for i, ch in enumerate(txt)]
        return {"text": txt, "words": words, "duration": dur}

def make_asr(transcripts, dur=10.0, words_tpl=None):
    seq = list(transcripts)
    def f(wav, d):
        t = seq.pop(0) if seq else ""
        words = None
        if words_tpl is not None:
            # 均匀铺时间戳：每字 0.35s，无长停顿
            n = len(t) or 1
            words = [[ch, i * 0.35, i * 0.35 + 0.3] for i, ch in enumerate(t)]
        return {"text": t, "words": words, "duration": dur}
    return f


def make_local_asr_fn(engine, lang):
    return lambda wav, dur: engine.transcribe(wav, lang, duration_hint=dur)

print("== A. 评分公式纯函数 ==")
segs = S.segment_text(READ_TASK["content"])
check("A1 分段 2~3 段且 40~80 字", 2 <= len(segs) <= 3 and all(0 < len(s) <= 80 for s in segs),
      [len(s) for s in segs])
check("A2 分段内容无损", "".join(segs) == READ_TASK["content"].replace(" ", ""))

acc, ins = S.align_score("秋天的大门", "秋天的大门")
check("A3 完全一致 ACC=1", acc == 1.0 and ins == 0, acc)
acc2, _ = S.align_score("钥匙", "要是")   # yao shi 同音
check("A4 音同字不同 ACC=0.6", abs(acc2 - 0.6) < 1e-9, acc2)
acc3, ins3 = S.align_score("大门", "大门被打开了")   # 多读插入
check("A5 插入惩罚", acc3 < 1.0 and ins3 >= 2, (acc3, ins3))
acc4, _ = S.align_score("五个", "苹果")
check("A6 完全不符低分", acc4 <= 0.3, acc4)

check("A7 语速满分区间", S.speed_score(200) == 100 and S.speed_score(110) == 100)
check("A8 语速过慢扣分", 0 < S.speed_score(70) < 100 and S.speed_score(30) == 0)
check("A9 语速过快扣分", 0 < S.speed_score(400) < 100)
check("A10 长停顿计数", S.count_pauses([["a",0,1],["b",3,4],["c",4.2,5]]) == 1)

s_perfect, a_p, f_p = S.seg_score(segs[0], segs[0], len(segs[0]) / 4.0, None)
check("A11 段分公式(无词戳,语速正常)", s_perfect > 60 and a_p == 1.0, (s_perfect, a_p, f_p))

print("== B. 朗读流程（fake ASR） ==")
def seq_asr3(transcripts, d):
    seq = list(transcripts)
    def f(wav, dur):
        return {"text": seq.pop(0) if seq else "", "words": None, "duration": d}
    return f

def run_read(fio, asr, rng_seed=7, chat=None, task=None):
    return S.evaluate_read(task or READ_TASK, fio, asr, chat, random.Random(rng_seed))

# B1: 全部完美，无重读，复测也完美 → 通过（follow：读的就是屏幕上的内容）
n = len(segs)
fio = FakeIO(confirm=False, follow=True)
r = run_read(fio, FollowASR(fio))
check("B1 完美朗读通过", r["pass"] and r["score"] >= 80, r)

# B2: 全部读错 → 不通过
fio2 = FakeIO(recordings=[(b"wav", 0)] * (n + 2), confirm=False)
r2 = run_read(fio2, seq_asr3(["完全不同的内容啊啊啊"] * (n + 2), 8))
check("B2 读错不通过", not r2["pass"] and r2["score"] < 80, r2)

# B3: 每段读完立即出分 + 评分标准常驻 + 等孩子点「继续」（防带读抽查已按家长要求移除）
fio3 = FakeIO(confirm=False, follow=True)
r3 = run_read(fio3, FollowASR(fio3))
check("B3 达标自动进入下一段(无停顿等待)", r3["pass"] and getattr(fio3, "pauses", 0) >= len(segs)
      and any("评分标准" in s for s in fio3.statuses) and any("已得分" in s for s in fio3.statuses),
      (r3, getattr(fio3, "statuses", [])[:2], getattr(fio3, "pauses", 0)))

# B4: 最低段重读一次（confirm=True，follow 依旧完美）
fio4 = FakeIO(confirm=True, follow=True)
r4 = run_read(fio4, FollowASR(fio4))
check("B4 全达标流程通过(门禁不触发)", r4["pass"], r4)

# B5: 漏读一段（第一段没录）→ 完整度拉低
fio5 = FakeIO(recordings=[(None, 0)] + [(b"wav", 0)] * (n + 2), confirm=False)
r5 = run_read(fio5, seq_asr3([segs[i] for i in range(1, n)] + [segs[0], segs[1]], 8))
check("B5 漏读扣完整度", r5["completeness"] < 1 and not r5["pass"], r5)

# B6: quiz 模式（fake chat：出3题，每题给90分）
def fake_chat(messages, temperature=0.0, timeout=60):
    if "questions" in messages[-1]["content"]:   # 出题请求
        return json.dumps({"questions": [{"q": "秋天的雨把什么颜色给了银杏树？", "points": ["黄色"]} for _ in range(3)]})
    return json.dumps({"score": 90, "reason": "答到要点"})
task_q = dict(READ_TASK); task_q["quiz"] = True
fio6 = FakeIO(confirm=False, follow=True, mishear={"秋天的雨把什么颜色给了银杏树？": "黄色，给了银杏树黄色"})
r6 = run_read(fio6, FollowASR(fio6), chat=fake_chat, task=task_q)
check("B6 朗读+问答总分(90/10权重)", r6["quiz"] is not None and abs(r6["score"] - (0.9 * r6["read"] + 0.1 * 90)) < 1.0, r6)

print("== C. 单词流程 ==")
def run_words(fio, asr, seed=7):
    return S.evaluate_words(WORDS_TASK, fio, asr, random.Random(seed))

fio = FakeIO(follow=True, dur_scale=1.0)
w1 = run_words(fio, FollowASR(fio, words=False))
check("C1 全对满分通过", w1["pass"] and w1["score"] == 100, w1)

fio = FakeIO(follow=True, dur_scale=1.0, mishear={"think": "sink", "car": "cat"})
w2 = run_words(fio, FollowASR(fio, words=False))
think_s = S.word_score("think", "sink"); cat_s = S.word_score("car", "cat")
expect = round((think_s + 100 + cat_s) / 3, 1)
check("C2 音标结构评分", abs(w2["score"] - expect) < 1.0 and 60 <= think_s <= 95 and cat_s < think_s,
      (w2, think_s, cat_s))
spoke_words = [w for _, w in getattr(fio, "speak_calls", [])]
check("C2b 不达标单词提供听发音", spoke_words.count("think") >= 1 and spoke_words.count("car") >= 1,
      getattr(fio, "speak_calls", []))

fio = FakeIO(follow=True, dur_scale=1.0, mishear={"think": "", "apple": "", "car": ""})
w3 = run_words(fio, FollowASR(fio, words=False))
check("C3 没读=0分不通过", not w3["pass"] and w3["score"] < 80, w3)

check("C4 word_score 精确=100", S.word_score("Apple", "apple.") == 100)
check("C5 word_score 大小写标点容错", S.word_score("apple", "Apple!") == 100)
check("C6 word_score 完全不对低分", S.word_score("think", "banana") <= 25)
check("C7 phone_hint 标准 IPA", S.phone_hint("think") == "/θɪŋk/", S.phone_hint("think"))
check("C8 IPA 长元音/双元音", S.phone_hint("through") == "/θruː/" and S.phone_hint("apple").startswith("/ˈæ") , (S.phone_hint("through"), S.phone_hint("apple")))
check("C9 IPA 浊音/重音", "ð" in S.phone_hint("weather") and "ˈ" in S.phone_hint("banana"), (S.phone_hint("weather"), S.phone_hint("banana")))

# B7: 逐段达标门禁 —— 第1段两次都不达标 → 提前结束（第2段为 None），问答跳过
fio7 = FakeIO(confirm=True, follow=True, mishear={segs[0]: "完全不同的内容啊啊啊"})
task_g = dict(READ_TASK); task_g["quiz"] = True
r7 = S.evaluate_read(task_g, fio7, FollowASR(fio7), fake_chat, random.Random(3))
check("B7 门禁:首段两次未过→提前结束", (not r7["pass"]) and r7["segments"][1] is None
      and r7["quiz"] is None and r7["completeness"] < 1.0, r7)

# B8: 门禁+重读达标 → 继续下一段（第1次读错，重读变正确）
class RetryFakeIO(FakeIO):
    def get_recording(self, max_seconds, hint=""):
        txt = self.mishear.get(self.current, self.current)
        self.said.append(txt)
        self.mishear.pop(self.current, None)   # 每段第2次重读自动变完美
        return (b"wav", max(1.0, len(txt) / self.dur_scale))
fio8 = RetryFakeIO(confirm=True, follow=True, mishear={segs[0]: "秋天的雨是一把" + "锁" * 20})
r8 = S.evaluate_read(READ_TASK, fio8, FollowASR(fio8), random.Random(3))
check("B8 门禁:重读后达标继续", r8["pass"] and r8["segments"][1] is not None,
      (r8, getattr(fio8, "statuses", [])[:3]))

print("== D. mock OpenAI 服务器 E2E ==")
from http.server import BaseHTTPRequestHandler, HTTPServer
MOCK_TXT = segs[0]
class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if self.path.endswith("/audio/transcriptions"):
            resp = {"text": MOCK_TXT, "duration": 6.0,
                    "words": [{"word": ch, "start": i * 0.3, "end": i * 0.3 + 0.25}
                              for i, ch in enumerate(MOCK_TXT)]}
        else:
            resp = {"choices": [{"message": {"content": json.dumps({"score": 80, "reason": "ok"})}}]}
        data = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

srv = HTTPServer(("127.0.0.1", 8199), MockHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

ai_cfg = json.load(open(AI_CFG_TEST, encoding="utf-8"))
client = S.AIClient(ai_cfg)
with open(os.path.join(HERE, "chore-data.json"), "rb") as f:
    pass
# 造一个 1 秒的静音 WAV（内容不重要，mock 不看）
buf = io.BytesIO()
with wave.open(buf, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"\x00\x00" * 16000)
wav1s = buf.getvalue()
tr = client.transcribe(wav1s)
check("D1 transcribe 解析(verbose_json+词戳)", tr["text"] == MOCK_TXT and tr["words"] and len(tr["words"]) == len(MOCK_TXT), tr)
content = client.chat([{"role": "user", "content": "hi"}])
check("D2 chat 解析", json.loads(content)["score"] == 80)
t0 = time.time()
tr2 = client.transcribe(wav1s)
check("D3 transcribe 速度", time.time() - t0 < 5)

# 子进程 --wav 模式（load_task_and_client 全链路）
with open(os.path.join(HERE, "tts_test_cn.wav"), "rb") as f:
    cn_wav = f.read()
# D4a：本地引擎优先（dpx1 为中文任务 → paraformer）
env_local = dict(os.environ)
p = subprocess.run([sys.executable, os.path.join(HERE, "PCVoiceScore.pyw"),
                    "--task", "dpx1", "--wav", "-", "--data-url", E2E_DATA],
                   input=cn_wav, capture_output=True, timeout=180, cwd=HERE, env=env_local)
out = p.stdout.decode("utf-8", "ignore")
try:
    j = json.loads(out[out.find("{"):out.rfind("}") + 1])
except Exception:
    j = {}
check("D4a 子进程 --wav 本地引擎", p.returncode == 0 and j.get("engine") == "local:zh"
      and "秋天的雨" in (j.get("text") or ""), (p.returncode, str(j)[:160], p.stderr.decode("utf-8", "ignore")[:200]))

# D4b：本地模型不可用 → 云端 mock 后备
env_cloud = dict(os.environ)
env_cloud["PCGUARDIAN_NO_LOCAL"] = "1"     # 诊断开关：强制云端后备
p2 = subprocess.run([sys.executable, os.path.join(HERE, "PCVoiceScore.pyw"),
                     "--task", "dpx1", "--wav", "-", "--data-url", E2E_DATA],
                    input=cn_wav, capture_output=True, timeout=180, cwd=HERE, env=env_cloud)
out2 = p2.stdout.decode("utf-8", "ignore")
try:
    j2 = json.loads(out2[out2.find("{"):out2.rfind("}") + 1])
except Exception:
    j2 = {}
check("D4b 云端识别后备(8199测试mock)", p2.returncode == 0 and j2.get("engine") == "cloud"
      and j2.get("text") == MOCK_TXT, (p2.returncode, str(j2)[:160]))

print("== E. 尝试次数 ==")
for f2 in ("guardian.aiscore.json",):
    for base in (HERE, APPDATA):
        p2 = os.path.join(base, f2)
        if os.path.exists(p2):
            os.remove(p2)
d1, _ = S.begin_attempt("dpx_test", 2)
check("E1 首次尝试", d1 is not None and d1["attempts"] == 1)
S.finish_attempt("dpx_test", {"score": 70, "pass": False})
d2, _ = S.begin_attempt("dpx_test", 2)
S.finish_attempt("dpx_test", {"score": 85, "pass": True})
d3, _ = S.begin_attempt("dpx_test", 2)
check("E2 次数用完/已通过即拒绝", d3 is None and d2["attempts"] == 2)
doc = G.load_ai_score()
check("E3 best/pass 持久化", doc["results"]["dpx_test"][G.today_key()]["best"] == 85
      and doc["results"]["dpx_test"][G.today_key()]["pass"] is True)

print("== F. guardian 集成（钱包/解锁条件/发放） ==")
for f2 in ("guardian.aiscore.json", "guardian.session.json", "guardian.reward.session.json", "guardian.spent.json"):
    for base in (HERE, APPDATA):
        p2 = os.path.join(base, f2)
        if os.path.exists(p2):
            os.remove(p2)
gu = G.Guardian()
gu.build_ui()
gu.root.withdraw()

data = G.fetch_state(E2E_DATA)
gu.apply_state(data)
gu.render()
check("F1 AI 任务显示在锁定页", any(t["id"] == "dpx1" for t in gu.ai_tasks))
check("F2 未通过时 t13 仍需打卡", gu.done_map.get("t13") is not True)

# 模拟评分通过：写 aiscore（dpx1 通过 → bindTo t13 + 发放 20 分钟）
doc = G.load_ai_score()
doc.setdefault("results", {})["dpx1"] = {G.today_key(): {"attempts": 1, "best": 88, "pass": True,
                                                         "final": {"score": 88, "pass": True}}}
G.save_ai_score(doc)
gu._reload_ai_score()
gu.apply_state(gu._last_data)
gu.render()
check("F3 解耦:AI通过不影响必做(t13仍需打卡)", gu.done_map.get("t13") is not True)
check("F4 奖励分钟入钱包", any(w["id"].startswith("ai-dpx1") and w["minutes"] == 20 for w in gu.wallet), gu.wallet)
doc2 = G.load_ai_score()
check("F5 发放只记一次", len([r for r in doc2.get("rewards", []) if r.get("task") == "dpx1"]) == 1)
gu.apply_state(gu._last_data)
doc3 = G.load_ai_score()
check("F6 重复 apply 不重复发放", len([r for r in doc3.get("rewards", []) if r.get("task") == "dpx1"]) == 1)

# F7: 家长打卡两门必做（t13 不再由 AI 替代）→ 必做全完成 → 保持锁定（不自动解锁）
data2 = json.loads(json.dumps(gu._last_data))
data2["records"] = {G.today_key(): {"t13": {"done": True, "amount": 1}, "t17": {"done": True, "amount": 0.5}}}
gu.apply_state(data2)
gu.render()
check("F7 必做(配置)全完成→保持锁定不自动解锁", gu.mode == gu.MODE_LOCKED and gu.all_done)

# F8: 钱包里 AI 奖励可直接勾选使用（必做已全部满足）
try:
    if gu.hud and gu.hud.winfo_exists():
        gu.hud.withdraw()
except Exception:
    pass
rid = next(w["id"] for w in gu.wallet if w["id"].startswith("ai-dpx1"))
gu.wallet_pick[rid].set(True)
gu.start_reward_use()
check("F8 AI 奖励可使用并立即扣除", gu.mode == gu.MODE_UNLOCKED and gu.reward_ids == [rid])
sp = G.load_spent()
check("F9 台账记录 AI 奖励已用", rid in sp)
try:
    gu.root.destroy()
except Exception:
    pass

print("== G. 录音引擎冒烟（本机麦克风，可选） ==")
try:
    rec = S.Recorder()
    rec.start()
    time.sleep(1.2)
    wav, dur = rec.stop()
    check("G1 WinMM 录音", len(wav) > 30000 and dur >= 1.0, (len(wav), dur))
except Exception as e:
    print("  SKIP  G1 录音冒烟（无麦克风或被占用）：%r" % e)

print("== H. 评分 UI 冒烟 ==")
try:
    app = S.ScoreApp(READ_TASK, client)
    app.root.withdraw()
    app.message("测试标题", ["测试内容行"])
    app.root.update()
    app.quit_all()
    check("H1 ScoreApp 构造与渲染", True)
except Exception as e:
    check("H1 ScoreApp 构造与渲染", False, repr(e))

print("== L. 本地双引擎 ASR（中文 Paraformer / 英文 Whisper，模型缺失则跳过） ==")
la = S.LocalASR()
has_zh = la.available("zh")
has_en = la.available("en")
print("  模型可用性: zh=%s en=%s (目录 %s)" % (has_zh, has_en, S.MODELS_DIR))

check("L1 语言自动判定", S.detect_lang({"content": ["think", "apple"]}) == "en"
      and S.detect_lang({"content": "秋天的雨是一把钥匙"}) == "zh"
      and S.detect_lang({"content": "Lesson One How are you fine thanks"}) == "en"
      and S.detect_lang({"content": ["静夜思，床前明月光"]}) == "zh")

def _read(p):
    with open(p, "rb") as f:
        return f.read()

if has_zh:
    r = la.transcribe(_read(os.path.join(HERE, "tts_test_cn.wav")), "zh", duration_hint=8.5)
    check("L2 中文识别(阿里Paraformer)", "秋天的雨" in r["text"] and r["words"] and len(r["words"]) >= 10,
          (r["text"][:40], r["words"] and len(r["words"])))
else:
    print("  SKIP  L2 中文识别（模型未安装）")

if has_en:
    r = la.transcribe(_read(os.path.join(HERE, "tts_test.wav")), "en")
    check("L3 英文识别(阿里Paraformer-en)", "reading scoring system" in r["text"]
          and float(r.get("duration") or 0) > 0,
          (r["text"][:60], r.get("duration")))

    # 真实本地 E2E：英文朗读任务 = TTS 句子原文，识别后应高分通过
    SENT = "Hello, this is a test of the reading scoring system. I think this through very carefully."
    task_en = {"id": "en1", "kind": "read", "name": "English Reading", "minutes": 10,
               "content": SENT, "threshold": 50}
    fioL = FakeIO(recordings=[(_read(os.path.join(HERE, "tts_test.wav")), 0)] * 8, confirm=False)
    rl = S.evaluate_read(task_en, fioL, make_local_asr_fn(la, "en"), random.Random(1))
    check("L4 英文本地 E2E 高分通过", rl["score"] >= 60, rl)   # TTS 语音语速放宽到 60 线验证链路
else:
    print("  SKIP  L3/L3b 英文识别（模型未安装）")

if has_zh:
    # 真实本地 E2E：中文朗读 = TTS 朗读的课文
    CN = "秋天的雨，是一把钥匙。它带着清凉和温柔，轻轻地打开了秋天的大门。"
    task_cn = {"id": "cn1", "kind": "read", "name": "课文朗读", "minutes": 10, "content": CN, "threshold": 50}
    fioC = FakeIO(recordings=[(_read(os.path.join(HERE, "tts_test_cn.wav")), 0)] * 8, confirm=False)
    rc = S.evaluate_read(task_cn, fioC, make_local_asr_fn(la, "zh"), random.Random(1))
    check("L5 中文本地 E2E", rc["score"] >= 55, rc)   # 系统 TTS 发音不完全标准，链路验证放宽
else:
    print("  SKIP  L5 中文 E2E（模型未安装）")

srv.shutdown()
shutil.rmtree(APPDATA, ignore_errors=True)
if had_bak_dir:
    shutil.move(BAK, APPDATA)

print("\n========== 通过 %d 项 / 失败 %d 项 ==========" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
