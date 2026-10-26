# -*- coding: utf-8 -*-
"""PCVoiceScore / AI 评分 —— 本地自动化测试
覆盖：评分公式纯函数、朗读/单词完整流程（fake ASR）、防代读复测、问答、
     mock 服务器 E2E（MiMo 协议 + --wav 子进程）、尝试次数、guardian 集成（钱包/解锁条件）。
运行：python test_ai.py   （本目录；自动拷贝最新 guardian.pyw / PCVoiceScore.pyw 为被测对象）
"""
import base64
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
               "asrModel": "mock-asr", "chatModel": "mock-chat"}, f)
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
        self._rec_n = 0         # 录音单调计数：每次录音 wav 唯一（复识同 wav 才返回同结果）
        self.current = ""
    def message(self, title, lines):
        self.msgs.append(title)
        if lines:
            self.current = str(lines[0])
    def get_recording(self, max_seconds, hint="", delay=0.0):
        if self.follow:
            txt = self.mishear.get(self.current, self.current)
            self.said.append(txt)
            self._rec_n += 1
            return (("wav%d" % self._rec_n).encode(), max(1.0, len(txt) / self.dur_scale))
        self._rec_n += 1
        return self.recordings.pop(0) if self.recordings else (None, 0.0)
    def confirm(self, text):
        return self.confirm_val
    def status(self, text):
        self.statuses = getattr(self, "statuses", [])
        self.statuses.append(text)
    def continue_(self, label="继续"):
        self.continues = getattr(self, "continues", 0) + 1
        self.cont_labels = getattr(self, "cont_labels", []) + [label]
    def pause(self, seconds):
        self.pauses = getattr(self, "pauses", 0) + 1
    def speak_word(self, word):
        self.speak_calls = getattr(self, "speak_calls", []) + [(self.current, word)]
    def segment_score(self, score, acc, flu):
        self.seg_scores = getattr(self, "seg_scores", []) + [(score, acc, flu)]
    def word_score(self, word, heard, score):
        self.word_scores = getattr(self, "word_scores", []) + [(word, heard, score)]
    def aborted(self):
        return False

class FollowASR:
    """与 FakeIO(follow=True) 配套：把 io.said 的内容按序作为识别结果。
    同一 wav 复识（低分自动复识）返回同一结果——真实语义：同一音频识别结果稳定。"""
    def __init__(self, fio, words=True):
        self.fio = fio; self.use_words = words
        self._last_wav = None; self._last_txt = ""
    def __call__(self, wav, dur):
        if wav is not None and wav == self._last_wav:
            return {"text": self._last_txt, "words": None, "duration": dur}
        txt = self.fio.said.pop(0) if self.fio.said else ""
        self._last_wav, self._last_txt = wav, txt
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
check("B1b 达标在阅读页原地下分(不整页跳转)", getattr(fio, "seg_scores", None) == [(100.0, 1.0, 1.0)] * len(segs),
      getattr(fio, "seg_scores", None))
check("B1c 达标后出现下一段/结束按钮", getattr(fio, "cont_labels", None) == ["下一段 ▶", "结束 ▶"],
      getattr(fio, "cont_labels", None))

# B2: 全部读错 → 不通过
fio2 = FakeIO(recordings=[(b"wav", 0)] * (n + 2), confirm=False)
r2 = run_read(fio2, seq_asr3(["完全不同的内容啊啊啊"] * (n + 2), 8))
check("B2 读错不通过", not r2["pass"] and r2["score"] < 80, r2)

# B3: 每段读完立即出分 + 评分标准常驻 + 孩子点「下一段/结束」进入下一步
fio3 = FakeIO(confirm=False, follow=True)
r3 = run_read(fio3, FollowASR(fio3))
check("B3 达标后等孩子点下一段(不再自动跳)", r3["pass"] and getattr(fio3, "continues", 0) >= len(segs)
      and any("评分标准" in s for s in fio3.statuses) and any("已得分" in s for s in fio3.statuses),
      (r3, getattr(fio3, "statuses", [])[:2], getattr(fio3, "continues", 0)))

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
check("C1b 单词在页面下方出分(不跳结果页)",
      {w: (h, s) for w, h, s in getattr(fio, "word_scores", [])} == {w: (w, 100) for w in ("think", "apple", "car")},
      getattr(fio, "word_scores", None))

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
check("C10 word_score 拼写兜底(无音标变体)", S.word_score("weather", "weathor") == 85
      and S.word_score("weather", "wheather") == 85, (S.word_score("weather", "weathor"),
                                                      S.word_score("weather", "wheather")))

# C11: 低分自动复识取最优（识别有随机性；同一音频重识别能纠偏，不浪费孩子机会）
seqW = make_asr(["xyzzy garbage", "car"], dur=5)
fio11 = FakeIO(recordings=[(b"c1", 0), (b"c1", 0)], confirm=False)
task11 = {"id": "w11", "kind": "words", "name": "复识取优", "minutes": 5,
          "content": ["car"], "threshold": 80}
w11 = S.evaluate_words(task11, fio11, seqW, random.Random(1))
check("C11 低分自动复识取最优", w11["pass"] and w11["score"] == 100, w11)

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
        self._rec_n += 1
        return (("wav%d" % self._rec_n).encode(), max(1.0, len(txt) / self.dur_scale))
fio8 = RetryFakeIO(confirm=True, follow=True, mishear={segs[0]: "秋天的雨是一把" + "锁" * 20})
r8 = S.evaluate_read(READ_TASK, fio8, FollowASR(fio8), random.Random(3))
check("B8 门禁:重读后达标继续", r8["pass"] and r8["segments"][1] is not None,
      (r8, getattr(fio8, "statuses", [])[:3]))

# B9: 出题失败自动重试（第1次返回垃圾，第2次正常）→ 问答不跳过
calls = {"gen": 0}
def chat_retry(messages, temperature=0.0, timeout=60):
    is_gen = any("出3道" in str(m.get("content") or "") for m in messages)   # 出题请求（含重试加强消息）
    if is_gen:
        calls["gen"] += 1
        if calls["gen"] == 1:
            return "抱歉，我理解不了这个要求。"      # 模拟一次不按 JSON 输出
        return json.dumps({"questions": [{"q": "秋天的雨像什么？", "points": ["钥匙"]} for _ in range(2)]})
    return json.dumps({"score": 90, "reason": "ok"})
fio9 = FakeIO(confirm=False, follow=True)
r9 = run_read(fio9, FollowASR(fio9), chat=chat_retry, task=task_q)
check("B9 出题失败自动重试不跳过", r9["quiz"] is not None and abs(r9["quiz"] - 90) < 1 and calls["gen"] == 2, r9)

# B10: 判题失败 ≠ 答错：失败的题不计入均分（第1题判题挂，第2题 70 → quiz=70）
def chat_judge_flaky(messages, temperature=0.0, timeout=60):
    if "questions" in messages[-1]["content"]:
        return json.dumps({"questions": [{"q": "问题%d？" % i, "points": ["要点"]} for i in range(2)]})
    chat_judge_flaky.n = getattr(chat_judge_flaky, "n", 0) + 1
    if chat_judge_flaky.n == 1:
        raise RuntimeError("mock 判题网络故障")
    return json.dumps({"score": 70, "reason": "ok"})
fio10 = FakeIO(confirm=False, follow=True)
r10 = run_read(fio10, FollowASR(fio10), chat=chat_judge_flaky, task=task_q)
check("B10 判题失败不计0分(跳过该题)", r10["quiz"] is not None and abs(r10["quiz"] - 70) < 1, r10)

# C13: 识别把英文误写成中文（真机实测 book→'书.'）→ chat 判断"听到的是不是这个词"
class _ChatFix:
    chat_ok = True
    def __init__(self): self.prompt = ""
    def chat(self, messages, temperature=0.0, timeout=60):
        self.prompt = messages[-1]["content"]
        return json.dumps({"match": True})
seqC13 = make_asr(["书。", "书。"], dur=5)          # 两次识别都是 '书.'（含 MiMo 残片重试）
fioC13 = FakeIO(recordings=[(b"w", 0), (b"w", 0)], confirm=False)
fixer = _ChatFix()
taskC13 = {"id": "w13", "kind": "words", "name": "chat判词", "minutes": 5,
           "content": ["book"], "threshold": 80}
w13 = S.evaluate_words(taskC13, fioC13, seqC13, random.Random(1), client=fixer)
check("C13 chat判词(book≠书)", w13["pass"] and w13["score"] == 100
      and "book" in fixer.prompt and "书" in fixer.prompt, (w13, fixer.prompt[:100]))
# chat 判"不是这个词" → 不放水
class _ChatNo(_ChatFix):
    def chat(self, messages, temperature=0.0, timeout=60):
        return json.dumps({"match": False})
seqC14 = make_asr(["书。", "书。"], dur=5)
fioC14 = FakeIO(recordings=[(b"w", 0), (b"w", 0)], confirm=False)
w14 = S.evaluate_words(taskC13, fioC14, seqC14, random.Random(1), client=_ChatNo())
check("C14 chat判否不放水", not w14["pass"], w14)
# C15: 识别提示只含当前目标词（不含整组词表）
class _PromptCap2:
    chat_ok = False
    def __init__(self): self.prompts = []
    def transcribe(self, wav, lang="en", timeout=90, prompt=None):
        self.prompts.append(prompt or "")
        return {"text": "book", "words": None, "duration": 5}
pc2 = _PromptCap2()
aw3 = S.make_asr(pc2, "en")
fioC15 = FakeIO(recordings=[(b"w", 0), (b"w", 0)], confirm=False)
S.evaluate_words({"id": "w15", "kind": "words", "name": "t", "minutes": 5,
                  "content": ["book", "apple"], "threshold": 80},
                 fioC15, aw3, random.Random(1), client=None)
check("C15 识别提示只含当前词(不混词表)", all((("book" in p) != ("apple" in p)) and p.strip()
                                             for p in pc2.prompts if p), pc2.prompts[:2])

# B11: 没录到声音 → 本段原样重试（不再静默跳段导致"突然跳到评分页"）
fio12 = FakeIO(recordings=[(None, 0)] + [(b"wav", 0)] * 4, confirm=False)
r12 = run_read(fio12, make_asr([segs[0], segs[1]], dur=8), rng_seed=9)
check("B11 空录本段重试不跳段", r12["pass"] and r12["completeness"] == 1.0, r12)

# B12: 听到但识别为空 → 提示"没听到，请重新阅读一遍"后重读本段
fio13 = FakeIO(recordings=[(b"wav", 0)] * 5, confirm=False)
r13 = run_read(fio13, make_asr(["", segs[0], segs[1]], dur=8), rng_seed=10)
check("B12 识别为空提示重读本段", r13["pass"] and r13["completeness"] == 1.0
      and any("没有听到声音" in str(m) for m in getattr(fio13, "msgs", [])), r13)

print("== D. mock 服务器 E2E（MiMo 协议：识别/判题同走 /chat/completions） ==")
from http.server import BaseHTTPRequestHandler, HTTPServer
MOCK_TXT = segs[0]
class MockHandler(BaseHTTPRequestHandler):
    tts_hits = 0                    # 真实到达服务器的 TTS 请求数（验证客户端缓存）
    def log_message(self, *a):
        pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if b"input_audio" in body:      # MiMo ASR 请求
            resp = {"choices": [{"message": {"content": MOCK_TXT}}]}
        elif b'"audio"' in body or b"voicedesign" in body:   # MiMo TTS（标准音色/音色设计）
            MockHandler.tts_hits += 1
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 1600)     # 0.1s 静音 WAV
            resp = {"choices": [{"message": {
                "role": "assistant",
                "audio": {"data": base64.b64encode(buf.getvalue()).decode()}}}]}
        else:                            # 出题/判题 chat 请求
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
# 造一个 1 秒的静音 WAV（内容不重要，mock 不看）
buf = io.BytesIO()
with wave.open(buf, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"\x00\x00" * 16000)
wav1s = buf.getvalue()
tr = client.transcribe(wav1s, lang="zh")
check("D1 transcribe 解析(MiMo chat协议)", tr["text"] == MOCK_TXT and tr["words"] is None, tr)
content = client.chat([{"role": "user", "content": "hi"}])
check("D2 chat 解析", json.loads(content)["score"] == 80)
t0 = time.time()
tr2 = client.transcribe(wav1s, lang="zh")
check("D3 transcribe 速度", time.time() - t0 < 5)

# D5/D6：MiMo TTS（听发音）—— 返回可解析 WAV + 客户端缓存（同词第二次不发请求）
w1 = client.tts("think")
with wave.open(io.BytesIO(w1), "rb") as _wf:
    ok_wav = _wf.getnframes() > 0
hits = MockHandler.tts_hits
w2 = client.tts("think")
check("D5 TTS 返回可解析WAV", ok_wav and w1 == w2, len(w1))
check("D6 TTS 客户端缓存(同词无二次请求)", MockHandler.tts_hits == hits, (hits, MockHandler.tts_hits))
# D7：音色设计模式（ttsStyle 非空 → voicedesign 模型，user=描述 / assistant=文本）
cfg_vd = dict(ai_cfg); cfg_vd["ttsStyle"] = "语速偏慢、吐字清晰的温柔女声"
w3 = S.AIClient(cfg_vd).tts("car")
with wave.open(io.BytesIO(w3), "rb") as _wf:
    ok_wav3 = _wf.getnframes() > 0
check("D7 TTS 音色设计(voicedesign)", ok_wav3, len(w3))

# D8：识别偶发返回空文本 → 同一音频自动重试一次（不让孩子白读）
class _FlakyClient:
    def __init__(self): self.n = 0
    def transcribe(self, wav, lang="zh", timeout=90, prompt=None):
        self.n += 1
        return {"text": "" if self.n == 1 else "重试成功", "words": None, "duration": None}
asr_r = S.make_asr(_FlakyClient(), "zh")
check("D8 空识别自动重试一次", asr_r(b"wav", 5)["text"] == "重试成功")

# D9：识别提示注入（prompt_box → asr_options.prompt，抑制短音频幻觉）
class _PromptCap:
    def __init__(self): self.prompts = []
    def transcribe(self, wav, lang="zh", timeout=90, prompt=None):
        self.prompts.append(prompt)
        return {"text": "ok", "words": None, "duration": 5}
pc = _PromptCap()
asr_p = S.make_asr(pc, "zh")
asr_p.prompt_box[0] = "词表提示"
asr_p(b"wav", 5)
check("D9 ASR 提示注入传递", pc.prompts == ["词表提示"], pc.prompts)
asr_p2 = S.make_asr(_PromptCap(), "zh")
asr_p2(b"wav", 5)
check("D9b 未设提示时为 None", pc.prompts is not None)

# D10：英文任务识别混入中文 → "只输出英文"强提示重试一次
class _CnThenEn:
    def __init__(self): self.calls = []
    def transcribe(self, wav, lang="zh", timeout=90, prompt=None):
        self.calls.append((lang, prompt))
        if len(self.calls) == 1:
            return {"text": "汽车", "words": None, "duration": 5}
        return {"text": "car", "words": None, "duration": 5}
ce = _CnThenEn()
ae = S.make_asr(ce, "en")
ae.prompt_box[0] = "vocab hint"
r_e = ae(b"w", 5)
check("D10 英文混中文自动重试", r_e["text"] == "car" and len(ce.calls) == 2, ce.calls)
check("D10b 重试提示含禁止翻译指令+原提示", "Never translate" in (ce.calls[1][1] or "") and "vocab hint" in (ce.calls[1][1] or ""),
      ce.calls)
cz = _CnThenEn()
az = S.make_asr(cz, "zh")
az(b"w", 5)
check("D10c 中文任务不触发英文重试", len(cz.calls) == 1, cz.calls)

# D11：静音剪辑 + 无人声拦截（防呼吸/噪声被幻觉成词——"上一个词串到下一个"的元凶）
import struct as _st
def _mkwav(samples, sr=16000):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(_st.pack("<%dh" % len(samples), *samples))
    return b.getvalue()

class _CapWav:
    def __init__(self): self.got = []
    def transcribe(self, wav, lang="zh", timeout=90, prompt=None):
        self.got.append(len(wav))
        return {"text": "ok", "words": None, "duration": 5}

tone = [12000 if i % 320 < 160 else -12000 for i in range(8000)]     # 0.5s 方波（响亮）
quiet = [400 if i % 320 < 160 else -400 for i in range(8000)]        # 0.5s 方波（低音量 rms≈400）
sil = [0] * 16000                                                    # 1s 静音
cw = _CapWav()
aw = S.make_asr(cw, "zh")
raw = _mkwav(sil + tone + sil)
r_t = aw(raw, 2.5)
check("D11a 首尾静音已剪(识别收到更短音频)", r_t["text"] == "ok" and 0 < cw.got[0] < len(raw),
      (cw.got[0], len(raw)))
n_before = len(cw.got)
r_s = aw(_mkwav(sil), 1.0)
check("D11b 纯静音不调用识别(返回空)", r_s["text"] == "" and len(cw.got) == n_before, (r_s, cw.got))
# D11c：低音量人声不被误判为静音（自适应阈值——真机曾因绝对阈值 500 把低音量整段判无人声）
cw2 = _CapWav()
aw2 = S.make_asr(cw2, "zh")
raw_q = _mkwav(sil + quiet + sil)
r_q = aw2(raw_q, 2.5)
check("D11c 低音量人声正常送识别", r_q["text"] == "ok" and len(cw2.got) == 1 and cw2.got[0] < len(raw_q),
      (r_q, cw2.got, len(raw_q)))
# D11e：低音量自动增益——识别收到的音频峰值被拉到正常电平（≥4000）
class _CapWavBytes:
    def __init__(self): self.wavs = []
    def transcribe(self, wav, lang="zh", timeout=90, prompt=None):
        self.wavs.append(wav)
        return {"text": "ok", "words": None, "duration": 5}
cw2b = _CapWavBytes()
aw2b = S.make_asr(cw2b, "zh")
aw2b(raw_q, 2.5)
pk = max(abs(v) for v in _st.unpack("<%dh" % (len(cw2b.wavs[-1]) // 2), cw2b.wavs[-1]))
check("D11e 低音量自动增益(峰值放大)", pk >= 4000, pk)
# D11d：录音连续性（无采样间隔）：2 秒睡眠窗应录到 ≈2.0 秒音频（32000 字节/秒）
try:
    rec_t = S.Recorder()
    rec_t.start(); time.sleep(2.0)
    wav_r, dur_r = rec_t.stop()
    check("D11d 录音连续无间隔", abs(len(wav_r) / 32000.0 - 2.0) < 0.15,
          (len(wav_r), len(wav_r) / 32000.0))
except Exception as e:
    print("  SKIP  D11d 录音连续性（无麦克风或被占用）：%r" % e)

# D12：麦克风设备枚举 + 配置读写（试音工具的基础）
devs = S.list_mic_devices()
check("D12a 麦克风设备枚举", isinstance(devs, list)
      and all(isinstance(d[0], int) and isinstance(d[1], str) and d[1] for d in devs), devs)
cfga = dict(G.load_audio_cfg()); cfga["micDevice"] = None
G.save_audio_cfg(cfga)
check("D12b 音频配置读写", G.load_audio_cfg().get("micDevice", "missing") in (None, "missing"),
      G.load_audio_cfg())

# 子进程 --wav 模式（load_task_and_client 全链路：任务配置 → ai cfgUrl → mock 识别）
p = subprocess.run([sys.executable, os.path.join(HERE, "PCVoiceScore.pyw"),
                    "--task", "dpx1", "--wav", "-", "--data-url", E2E_DATA],
                   input=wav1s, capture_output=True, timeout=180, cwd=HERE)
out = p.stdout.decode("utf-8", "ignore")
try:
    j = json.loads(out[out.find("{"):out.rfind("}") + 1])
except Exception:
    j = {}
check("D4 子进程 --wav 云端识别(MiMo mock)", p.returncode == 0 and j.get("engine") == "mimo"
      and j.get("text") == MOCK_TXT, (p.returncode, str(j)[:160], p.stderr.decode("utf-8", "ignore")[:200]))

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
try:
    v = S.ScoreApp.ambient_level(None)
    note = S.ScoreApp.ambient_note(v)
    check("G2 环境底噪检测", (v is None and note == "") or (isinstance(v, int) and 0 <= v < 40000),
          (v, note))
except Exception as e:
    print("  SKIP  G2 环境底噪检测：%r" % e)

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

print("== L. 语言判定（识别统一走 MiMo 云端，按任务语言 zh/en） ==")

check("L1 语言自动判定", S.detect_lang({"content": ["think", "apple"]}) == "en"
      and S.detect_lang({"content": "秋天的雨是一把钥匙"}) == "zh"
      and S.detect_lang({"content": "Lesson One How are you fine thanks"}) == "en"
      and S.detect_lang({"content": ["静夜思，床前明月光"]}) == "zh")
# 未配置识别接口时 make_asr_for_task 应给出明确报错（而不是崩溃）
try:
    S.make_asr_for_task(READ_TASK, S.AIClient({}))
    check("L2 未配置识别时明确报错", False, "未抛异常")
except RuntimeError as e:
    check("L2 未配置识别时明确报错", "MiMo" in str(e) or "云端" in str(e), str(e))
except Exception as e:
    check("L2 未配置识别时明确报错", False, repr(e))

srv.shutdown()
shutil.rmtree(APPDATA, ignore_errors=True)
if had_bak_dir:
    shutil.move(BAK, APPDATA)

print("\n========== 通过 %d 项 / 失败 %d 项 ==========" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
