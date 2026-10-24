# -*- coding: utf-8 -*-
"""生成孩子端（PCGuardian 锁定页 / PCVoiceScore 评分页）各状态截图 → screenshots/child-*.png"""
import importlib.util, json, os, shutil, sys, time, types

HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.dirname(HERE)
APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PCGuardian")
OUT = os.path.join(HERE, "screenshots")
os.makedirs(OUT, exist_ok=True)

BAK = APPDATA + ".shotbak"
had = os.path.exists(APPDATA)
if had:
    shutil.rmtree(BAK, ignore_errors=True)
    shutil.move(APPDATA, BAK)
shutil.copy(os.path.join(PROD, "guardian.pyw"), os.path.join(HERE, "guardian.pyw"))
shutil.copy(os.path.join(PROD, "PCVoiceScore.pyw"), os.path.join(HERE, "PCVoiceScore.pyw"))
for f in ("guardian.session.json", "guardian.reward.session.json", "guardian.spent.json", "guardian.aiscore.json"):
    for base in (HERE, APPDATA):
        p = os.path.join(base, f)
        if os.path.exists(p):
            os.remove(p)

sys.argv = [os.path.join(HERE, "shots_child.py"), "--demo"]

def _imp(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

G = _imp("sg", os.path.join(HERE, "guardian.pyw"))
S = _imp("ss", os.path.join(HERE, "PCVoiceScore.pyw"))
from PIL import ImageGrab

TODAY = G.today_key()
READ = ("秋天的雨，是一把钥匙。它带着清凉和温柔，轻轻地，轻轻地，趁你没留意，"
        "把秋天的大门打开了。秋天的雨，有一盒五彩缤纷的颜料。你看，它把黄色给了银杏树，"
        "把红色给了枫树，把金黄色给了田野，把橙红色给了果树。")

def DATA(records=None, dispatch=None, computer=None):
    return {"version": 1, "childName": "悠悠",
            "config": {"computer": computer or {"minutes": 30, "unlock": False},
                       "dispatch": {"tasks": dispatch if dispatch is not None else [
                           {"id": "dp1", "name": "完成数学口算一页", "minutes": 20},
                           {"id": "dp3", "name": "跳绳500下", "minutes": 15},
                           {"id": "dpx1", "kind": "read", "name": "朗读《秋天的雨》", "minutes": 20,
                            "content": READ, "threshold": 80, "bindTo": "t13"},
                           {"id": "dpx2", "kind": "words", "name": "Unit3 单词背诵", "minutes": 10,
                            "content": ["think", "apple", "car"], "threshold": 80}]},
                       "tasks": [
                           {"id": "t13", "group": "📚 学习", "name": "课外阅读30分钟", "amount": 1, "amountText": "+1元", "weekendOnly": False},
                           {"id": "t17", "group": "📚 学习", "name": "背单词/古诗词（10个）", "amount": 0.5, "amountText": "+0.5元", "weekendOnly": False}]},
            "records": records if records is not None else {}, "expenses": {}}

def grab(widget, name, pad=6):
    widget.update_idletasks(); widget.update()
    time.sleep(0.25); widget.update()
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    w, h = widget.winfo_width(), widget.winfo_height()
    img = ImageGrab.grab(bbox=(max(0, x - pad), max(0, y - pad), x + w + pad, y + h + pad))
    img.save(os.path.join(OUT, name))
    print("shot:", name, img.size)

def windowize(root):
    root.attributes("-fullscreen", False)
    root.geometry("1180x820+60+40")
    root.deiconify()
    root.configure(bg="#10141f")

gu = G.Guardian()
gu.build_ui()
windowize(gu.root)

# ---- 状态1：基础锁定（任务未完成 + 分派任务清单 + AI 任务区） ----
gu.apply_state(DATA())
gu.render()
grab(gu.root, "child-01-locked.png")

# ---- 状态2：奖励钱包（dp1 已完成 → 可勾选累计使用） ----
gu.apply_state(DATA(records={TODAY: {"dp1": {"done": True}}}))
gu.render()
for w in gu.wallet:
    if w["id"] == "dp1":
        gu.wallet_pick[w["id"]].set(True)
gu._update_use_btn()
grab(gu.root, "child-02-wallet.png")

# ---- 状态3：AI 任务已测/已通过状态 ----
doc = G.load_ai_score()
doc["results"] = {"dpx1": {TODAY: {"attempts": 2, "best": 65, "pass": False}},
                  "dpx2": {TODAY: {"attempts": 1, "best": 90, "pass": True,
                                   "final": {"score": 90, "pass": True}}}}
G.save_ai_score(doc)
gu._reload_ai_score()
gu.apply_state(gu._last_data)
gu.render()
grab(gu.root, "child-03-ai-status.png")

# ---- 状态4：恭喜画面 ----
for f in ("guardian.aiscore.json",):
    for base in (HERE, APPDATA):
        p = os.path.join(base, f)
        if os.path.exists(p):
            os.remove(p)
gu._reload_ai_score()
gu.ai_doc = G.load_ai_score()
gu.apply_state(DATA(records={TODAY: {"t13": {"done": True, "amount": 1}, "t17": {"done": True, "amount": 0.5}}}))
gu.render()
grab(gu.root, "child-04-congrats.png")

# ---- 状态5：解锁 HUD 倒计时 ----
gu.congrats_until = time.time() - 0.1
gu.poll_forever_ui()
if gu.hud and gu.hud.winfo_exists():
    gu.hud.geometry("+300+300")
    gu.root.update()
    grab(gu.hud, "child-05-hud.png", pad=10)
try:
    gu.root.withdraw()
except Exception:
    pass

# ---- 状态6：今天基础时间用完，但还有奖励 ----
gu.abort_shutdown("截图")
for f in ("guardian.session.json", "guardian.reward.session.json", "guardian.spent.json"):
    for base in (HERE, APPDATA):
        p = os.path.join(base, f)
        if os.path.exists(p):
            os.remove(p)
gu2 = G.Guardian()
gu2.build_ui()
windowize(gu2.root)
G.save_session(time.time() - 3600, time.time() - 7200, closed=True)   # 当天基础机会已用完
gu2.daily_done = True
gu2.apply_state(DATA(records={TODAY: {"dp1": {"done": True}}}))
gu2.render()
for w in gu2.wallet:
    gu2.wallet_pick[w["id"]].set(True)
gu2._update_use_btn()
grab(gu2.root, "child-06-usedup-wallet.png")

# ---- 评分程序三个画面 ----
dummy_client = types.SimpleNamespace(ok=True)
app = S.ScoreApp({"id": "dpx1", "kind": "read", "name": "朗读《秋天的雨》", "minutes": 20,
                  "content": READ, "threshold": 80}, dummy_client)
windowize(app.root)
segs = S.segment_text(READ)
app.message("第 1/2 段 · 请朗读", [segs[0]])
app.meter.configure(text="● ▮▮▮▮▮▮▮▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯")
grab(app.root, "child-07-score-read.png")

app.message("单词 2/5（第2次，照音标读）", ["through", "/ TH R UW /"])
app.meter.configure(text="")
grab(app.root, "child-08-score-word.png")

app.message("🎉 恭喜通过！86 分（阈值 80）", ["奖励 20 分钟将由锁定页自动发放", "窗口即将自动关闭…"])
grab(app.root, "child-09-score-pass.png")

# ---- 清理还原 ----
for root in (app, gu2):
    try:
        root.quit_all()
    except Exception:
        try:
            root.root.destroy()
        except Exception:
            pass
try:
    gu.root.destroy()
except Exception:
    pass
for f in ("guardian.session.json", "guardian.reward.session.json", "guardian.spent.json", "guardian.aiscore.json"):
    p = os.path.join(HERE, f)
    if os.path.exists(p):
        os.remove(p)
shutil.rmtree(APPDATA, ignore_errors=True)
if had:
    shutil.move(BAK, APPDATA)
print("done")
