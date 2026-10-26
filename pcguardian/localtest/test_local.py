# -*- coding: utf-8 -*-
"""PCGuardian 任务分派（奖励时间）功能 —— 本地自动化测试
覆盖：钱包计算/台账/会话文件等纯逻辑 + 无头 UI 集成（真实 tkinter 控件，不开 mainloop）。
运行：python test_local.py   （在本目录下；自动拷贝最新 guardian.pyw 作为被测对象）
"""
import importlib.util, json, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.dirname(HERE)
APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PCGuardian")

# ---- 隔离 %LOCALAPPDATA% 影子副本（测试产物不污染真实环境，结束后还原） ----
BAK = APPDATA + ".testbak"
had_bak_dir = os.path.exists(APPDATA)
if had_bak_dir:
    shutil.rmtree(BAK, ignore_errors=True)
    shutil.move(APPDATA, BAK)

# ---- 拷贝最新源码为被测对象，清旧产物 ----
shutil.copy(os.path.join(PROD, "guardian.pyw"), os.path.join(HERE, "guardian.pyw"))
for f in ("guardian.session.json", "guardian.reward.session.json", "guardian.spent.json",
          "guardian.aiscore.json", "guardian.redeem.json", "guardian.log"):
    p = os.path.join(HERE, f)
    if os.path.exists(p):
        os.remove(p)
for f in ("guardian.session.json", "guardian.reward.session.json", "guardian.spent.json",
          "guardian.aiscore.json", "guardian.redeem.json"):
    p = os.path.join(APPDATA, f)
    if os.path.exists(p):
        os.remove(p)

sys.argv = [os.path.join(HERE, "test_local.py"), "--demo"]   # demo → dry_run，绝不真关机
spec = importlib.util.spec_from_file_location("guardian_test", os.path.join(HERE, "guardian.pyw"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

today = g.today_key()

def DATA(records=None, dispatch=None, computer=None):
    return {
        "version": 1, "childName": "测试",
        "config": {
            "gameRate": 5,
            "computer": computer or {"minutes": 30, "unlock": False, "required": ["t13", "t17"]},
            "dispatch": dispatch if dispatch is not None else {"tasks": [
                {"id": "dp1", "name": "完成数学口算一页", "minutes": 20, "createdAt": "2026-10-24T09:00:00"},
                {"id": "dp2", "name": "整理书桌和书架", "minutes": 10, "createdAt": "2026-10-24T09:00:00"},
                {"id": "dp3", "name": "跳绳500下", "minutes": 15, "createdAt": "2026-10-24T09:00:00"},
            ]},
            "tasks": [
                {"id": "t13", "group": "学习", "name": "课外阅读30分钟", "amount": 1, "amountText": "+1元", "weekendOnly": False},
                {"id": "t17", "group": "学习", "name": "背单词/古诗词（10个）", "amount": 0.5, "amountText": "+0.5元", "weekendOnly": False},
            ],
        },
        "records": records if records is not None else {},
        "expenses": {},
    }

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else "FAIL  ") + name + (("   << " + str(extra) + " >>") if (extra and not cond) else ""))


def pane_texts(inner):
    """收集滚动栏内全部文本（标签/按钮），用于断言展示内容。"""
    out = []
    def walk(w):
        for c in w.winfo_children():
            try:
                t = c.cget("text")
            except Exception:
                t = ""
            if t:
                out.append(str(t))
            walk(c)
    walk(inner)
    return " | ".join(out)

def hide_hud(gu):
    try:
        if gu.hud is not None and gu.hud.winfo_exists():
            gu.hud.withdraw()
    except Exception:
        pass

print("== A. 模块级纯逻辑 ==")
recs = {"2026-10-20": {"dp1": {"done": True}}, "2026-10-22": {"dp1": {"done": False}}}
check("A1 latest_done 撤销生效(最新一天优先)", g.latest_done(recs, "dp1") is False)
check("A2 latest_done 历史完成", g.latest_done({"2026-10-20": {"dp1": {"done": True}}}, "dp1") is True)
check("A3 latest_done 从未记录=None", g.latest_done({}, "dpX") is None)

d = DATA(records={today: {"dp1": {"done": True}}})
w = g.compute_wallet(d, {})
check("A4 钱包=已完成未使用", [x["id"] for x in w] == ["dp1"] and w[0]["minutes"] == 20)
w2 = g.compute_wallet(d, {"dp1": {"date": today, "minutes": 20}})
check("A5 台账排除已用", [x["id"] for x in w2] == [])
w3 = g.compute_wallet(DATA(records={today: {"dp2": {"done": True}}}), {})
check("A6 第二个任务入钱包", [x["id"] for x in w3] == ["dp2"])
w4 = g.compute_wallet(DATA(records={today: {"dp1": {"done": True}}}, dispatch={"tasks": [
    {"id": "dp1", "name": "无时长任务", "minutes": 0}]}), {})
check("A7 零时长任务忽略", w4 == [])

E2E = os.path.join(HERE, "e2e-data.json")
with open(E2E, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False)
check("A8 fetch_state 本地路径", g.fetch_state(E2E)["config"]["dispatch"]["tasks"][0]["id"] == "dp1")
check("A9 fetch_state file:// URL", g.fetch_state("file:///" + E2E.replace("\\", "/"))["version"] == 1)

g.save_spent({"dp1": {"date": today, "minutes": 20}})
check("A10 台账往返", g.load_spent().get("dp1", {}).get("minutes") == 20)
check("A11 台账影子副本存在", os.path.exists(os.path.join(APPDATA, "guardian.spent.json")))

end = time.time() + 600
g.save_reward_session(end, time.time() - 10)
st, e2 = g.load_reward_session()
check("A12 奖励会话 restore", st == "restore" and abs(e2 - end) < 1)
g.clear_reward_session()
check("A13 奖励会话清除", g.load_reward_session() == (None, None))

g.save_session(time.time() - 100, time.time() - 4000, closed=True)
check("A14 基础会话 closed=used_up(不补关机)", g.load_session()[0] == "used_up")

print("== B. 无头 UI 集成（真实控件，不开 mainloop） ==")
for f in ("guardian.session.json", "guardian.reward.session.json", "guardian.spent.json"):
    for base in (HERE, APPDATA):
        p = os.path.join(base, f)
        if os.path.exists(p):
            os.remove(p)

gu = g.Guardian()
gu.build_ui()
gu.root.withdraw()
gu.render()

gu.apply_state(DATA())
gu.render()
check("B1 锁定+无钱包", gu.mode == gu.MODE_LOCKED and not gu.wallet and "还没有" in pane_texts(gu.done_inner))
_todo_txt = pane_texts(gu.todo_inner)
check("B2 待完成任务在右栏展示", "完成数学口算一页" in _todo_txt and "20 分钟" in _todo_txt, _todo_txt)

gu.apply_state(DATA(records={today: {"dp1": {"done": True},
                                    "t13": {"done": True}, "t17": {"done": True}}}))
gu.render()
check("B3 完成后钱包出现", [x["id"] for x in gu.wallet] == ["dp1"])
check("B4 已完成栏勾选项已渲染", "完成数学口算一页" in pane_texts(gu.done_inner))

gu.wallet_pick["dp1"].set(True)
gu._update_use_btn()
check("B5 勾选后按钮就绪", str(gu.btn_use.cget("state")) == "normal" and "20" in gu.btn_use.cget("text"))
gu.start_reward_use()
hide_hud(gu)
check("B6 奖励轮解锁", gu.mode == gu.MODE_UNLOCKED and gu.reward_ids == ["dp1"])
check("B7 时长=20分钟", abs((gu.end_time - time.time()) - 20 * 60) < 5, gu.end_time - time.time())
check("B8 台账立即扣除(选定即扣除)", "dp1" in g.load_spent())
check("B9 奖励会话文件已写", g.load_reward_session()[0] == "restore")
check("B10 钱包UI已清空该奖励", all(x["id"] != "dp1" for x in gu.wallet))

gu.apply_state(DATA())   # 基础打卡全部消失
gu.render(); hide_hud(gu)
check("B11 奖励轮豁免打卡取消(不回锁)", gu.mode == gu.MODE_UNLOCKED)

gu.end_time = time.time() - 1
gu.finish_timeout()
check("B12 到点且无其他奖励→关机流程", gu.mode == gu.MODE_SHUTTING)
gu.abort_shutdown("测试")

gu.apply_state(DATA(records={today: {"dp2": {"done": True}, "dp3": {"done": True},
                                    "t13": {"done": True}, "t17": {"done": True}}},
                     dispatch={"tasks": [
                         {"id": "dp1", "name": "完成数学口算一页", "minutes": 20},
                         {"id": "dp2", "name": "整理书桌和书架", "minutes": 10},
                         {"id": "dp3", "name": "跳绳500下", "minutes": 15}]}))
gu.render()
check("B13 新奖励进入钱包", [x["id"] for x in gu.wallet] == ["dp2", "dp3"])
gu.wallet_pick["dp2"].set(True)
gu.start_reward_use()
hide_hud(gu)
gu.end_time = time.time() - 1
gu.finish_timeout()
check("B14 到点仍有奖励→不关机回选择页", gu.mode == gu.MODE_LOCKED and g.load_reward_session() == (None, None))

print("== C. 必做任务前提 + 家长解锁（时长只来自分派/兑换） ==")
if gu.mode == gu.MODE_SHUTTING:
    gu.abort_shutdown("测试兜底")
gu.apply_state(DATA(records={today: {"t13": {"done": True, "amount": 1}, "t17": {"done": True, "amount": 0.5},
                                   "dp3": {"done": True}}}))
gu.render()
check("C1 必做完成→保持锁定不自动解锁", gu.mode == gu.MODE_LOCKED and "已完成" in gu.lb_title.cget("text"))
check("C2 分派奖励入钱包可使用", any(w["id"] == "dp3" for w in gu.wallet))
wid = next(w["id"] for w in gu.wallet if w["id"] == "dp3")
gu.wallet_pick[wid].set(True)
gu.start_reward_use()
hide_hud(gu)
check("C3 奖励轮解锁(勾选即扣)", gu.mode == gu.MODE_UNLOCKED and gu.reward_ids == [wid])
gu.end_time = time.time() - 1
gu.finish_timeout()
check("C4 奖励到点无其他奖励→关机流程", gu.mode == gu.MODE_SHUTTING)
gu.abort_shutdown("测试")

gu.apply_state(DATA(computer={"minutes": 30, "unlock": True}))
gu.render(); hide_hud(gu)
check("C5 家长临时解锁", gu.mode == gu.MODE_UNLOCKED and gu.end_time is None)
gu.apply_state(DATA(computer={"minutes": 30, "unlock": False}))
gu.render(); hide_hud(gu)
check("C6 关闭家长解锁→回到锁定", gu.mode == gu.MODE_LOCKED)

gu.apply_state(DATA(records={today: {"t13": {"done": True}, "dp9": {"done": True}}},
                     dispatch={"tasks": [{"id": "dp9", "name": "额外任务九", "minutes": 12}]}))   # t17 未完成
gu.render()
check("C7 必做未完成→钱包禁用", str(gu.btn_use.cget("state")) == "disabled" and "必做" in gu.lb_use.cget("text"))
wid2 = next(w["id"] for w in gu.wallet if w["id"] == "dp9")
gu.wallet_pick[wid2].set(True)
gu.start_reward_use()
hide_hud(gu)
check("C8 未完成时使用被拒", gu.mode == gu.MODE_LOCKED)
gu9 = g.Guardian(); gu9.build_ui(); gu9.root.withdraw()
gu9.apply_state(DATA(records={today: {"t13": {"done": True}, "dp8": {"done": True}}},
                     dispatch={"tasks": [{"id": "dp8", "name": "全新任务八", "minutes": 12}]},
                     computer={"minutes": 30, "unlock": False, "required": []}))
gu9.render()
wid3 = next(w["id"] for w in gu9.wallet if w["id"] == "dp8")
gu9.wallet_pick[wid3].set(True)
gu9.start_reward_use()
check("C9 未勾必做→无门禁立即可用", gu9.mode == gu9.MODE_UNLOCKED and gu9.reward_ids == [wid3])
try:
    gu9.root.destroy()
except Exception:
    pass
try:
    gu.root.destroy()
except Exception:
    pass

print("== R. 零花钱兑换电脑时长 ==")
g.clear_reward_session()   # 前面段落可能留下未结束的奖励会话（恢复优先逻辑会把它还原出来）
# 清兑换台账
for f in ("guardian.redeem.json", "guardian.aiscore.json"):
    for base in (HERE, APPDATA):
        p = os.path.join(base, f)
        if os.path.exists(p):
            os.remove(p)

def RDATA(records=None, computer=None, gameRate=5):
    return {"version": 1, "childName": "测试",
            "config": {"gameRate": gameRate,
                       "computer": computer or {"minutes": 30, "unlock": False,
                                                 "redeem": {"enabled": True, "dailyCap": 10}},
                       "dispatch": {"tasks": []},
                       "tasks": [
                           {"id": "t13", "name": "课外阅读30分钟"},
                           {"id": "t17", "name": "背单词/古诗词（10个）"}]},
            "records": records if records is not None else {"2026-10-20": {"t99": {"done": True, "amount": 30}}},
            "expenses": {"2026-10-21": [{"amount": 5}]}}

check("R1 cloud_balance 口径=收入-支出", g.cloud_balance(RDATA()) == 25.0, g.cloud_balance(RDATA()))
gr = g.Guardian(); gr.build_ui(); gr.root.withdraw()
gr.redeem_enabled = True; gr.cfg["redeem_weekend_only"] = False   # 测试不依赖周末
gr.apply_state(RDATA()); gr.render()
check("R2 顶栏余额+兑换分布", "25.00" in gr.lb_money_total.cget("text")
      and len(gr.redeem_frame.winfo_children()) > 0 and "30分钟" in pane_texts(gr.redeem_frame),
      (gr.lb_money_total.cget("text"), pane_texts(gr.redeem_frame)[:80]))
gr._on_redeem_chip(60)          # 第一次点击：武装确认
check("R3 两步确认武装", gr._redeem_arm is not None and gr._redeem_arm[1] == 5.0)
gr._on_redeem_chip(60)          # 第二次点击：执行
gr.render()
check("R4 兑换成功入钱包", any(w["id"].startswith("rd-") and w["minutes"] == 60 for w in gr.wallet), gr.wallet)
check("R5 台账记账", g.load_redeem()["totalSpent"] == 5.0 and g.load_redeem()["days"][today]["spent"] == 5.0)
check("R6 余额同步扣减", gr._money_left() == 20.0)
# 上限：今日 5/10，再兑 60 分钟(¥5) = 刚好到上限
gr._on_redeem_chip(60); gr._on_redeem_chip(60)
gr.render()
check("R7 每日上限内二次兑换", g.load_redeem()["days"][today]["spent"] == 10.0 and gr._money_left() == 15.0)
gr._redeem_arm = None
gr._on_redeem_chip(30); gr._on_redeem_chip(30)   # ¥2.5 超上限 → 两步后拒绝
gr.render()
check("R8 超每日上限拒绝", g.load_redeem()["days"][today]["spent"] == 10.0 and "上限" in gr._redeem_msg)
# 重启：台账与已发放奖励持久
try:
    gr.root.destroy()
except Exception:
    pass
gr2 = g.Guardian(); gr2.build_ui(); gr2.root.withdraw()
gr2.redeem_enabled = True; gr2.cfg["redeem_weekend_only"] = False
gr2.apply_state(RDATA()); gr2.render()
check("R9 重启后台账生效(余额15)", gr2._money_left() == 15.0 and "15.00" in gr2.lb_money_total.cget("text"))
check("R10 已兑换时长跨重启在钱包", sum(1 for w in gr2.wallet if w["id"].startswith("rd-")) == 2)
# 关闭兑换 → 面板消失
gr2.apply_state(RDATA(computer={"minutes": 30, "unlock": False, "redeem": {"enabled": False, "dailyCap": 10}}))
gr2.render()
check("R11 关闭后兑换分布隐藏", not gr2.redeem_frame.winfo_manager())
# 余额不足
gr3 = g.Guardian(); gr3.build_ui(); gr3.root.withdraw()
gr3.redeem_enabled = True; gr3.cfg["redeem_weekend_only"] = False
gr3.apply_state(RDATA(records={"2026-10-20": {"t99": {"done": True, "amount": 3}}},
                       computer={"minutes": 30, "unlock": False, "redeem": {"enabled": True, "dailyCap": 50}}))
gr3.render()
gr3._on_redeem_chip(60); gr3._on_redeem_chip(60)
gr3.render()
check("R12 余额不足拒绝", "不足" in gr3._redeem_msg and g.load_redeem()["totalSpent"] == 10.0)
try:
    gr2.root.destroy(); gr3.root.destroy()
except Exception:
    pass

print("== D. 重启恢复 / 躲避关机 ==")
for f in ("guardian.aiscore.json", "guardian.redeem.json", "guardian.spent.json",
          "guardian.session.json", "guardian.reward.session.json"):
    for base in (HERE, APPDATA):
        p = os.path.join(base, f)
        if os.path.exists(p):
            os.remove(p)
r_end = time.time() + 300
g.save_reward_session(r_end, time.time() - 60)
gu2 = g.Guardian()
gu2.build_ui(); gu2.root.withdraw()
check("D1 重启载入奖励会话", gu2.reward_restore_end is not None)
gu2.apply_state(DATA(records={today: {"dp3": {"done": True}}}))
gu2.render(); hide_hud(gu2)
check("D2 恢复奖励倒计时(不重不发)", gu2.mode == gu2.MODE_UNLOCKED and abs(gu2.end_time - r_end) < 1 and gu2.reward_ids is not None)
check("D3 已用台账跨重启生效", [x["id"] for x in gu2.wallet] == ["dp3"])
try:
    gu2.root.destroy()
except Exception:
    pass

g.save_reward_session(time.time() - 300, time.time() - 1800)
gu3 = g.Guardian()
gu3.build_ui(); gu3.root.withdraw()
check("D4 载入expired(躲避关机)", gu3.reward_expired)
gu3.apply_state(DATA())
gu3.render()
check("D5 补执行关机(无需基础打卡全完成)", gu3.mode == gu3.MODE_SHUTTING)
gu3.abort_shutdown("测试")
# 家长解锁可救回
gu3.apply_state(DATA(computer={"minutes": 30, "unlock": True}))
gu3.render(); hide_hud(gu3)
check("D6 家长解锁救回关机", gu3.mode == gu3.MODE_UNLOCKED and gu3.end_time is None)
try:
    gu3.root.destroy()
except Exception:
    pass

print("== M. 本地媒体文件夹（扫描/MD 解析/奖励/进度） ==")
g.clear_reward_session()
MEDIA_TMP = os.path.join(APPDATA, "media_test")
os.makedirs(MEDIA_TMP, exist_ok=True)
with open(os.path.join(MEDIA_TMP, "Unit3单词.md"), "w", encoding="utf-8") as f:
    f.write("# Unit3 单词\nthink\napple\ncar\n")
with open(os.path.join(MEDIA_TMP, "秋天的雨.md"), "w", encoding="utf-8") as f:
    f.write("## 朗读：秋天的雨\n" + "秋天的雨，是一把钥匙，带着清凉和温柔打开了秋天的大门。\n"
            "它把黄色给了银杏树，把红色给了枫树，把金黄色给了田野和果树。\n")
with open(os.path.join(MEDIA_TMP, "test.mp3"), "wb") as f:
    f.write(b"ID3fake")
with open(os.path.join(MEDIA_TMP, "test.mp4"), "wb") as f:
    f.write(b"fake")

gu4 = g.Guardian()
gu4.build_ui()
gu4.root.withdraw()
gu4.cfg["media_dir"] = MEDIA_TMP
scan = gu4._scan_local_tasks()
check("M1 MD→单词任务", any(t["kind"] == "words" and t["name"] == "Unit3 单词"
                            and t["content"] == ["think", "apple", "car"] for t in scan["docs"]),
      [(t["kind"], t["name"]) for t in scan["docs"]])
check("M2 MD→朗读任务", any(t["kind"] == "read" and "秋天的雨" in t["content"] for t in scan["docs"]),
      [(t["kind"], t["name"]) for t in scan["docs"]])
check("M3 音视频识别分类", {m["kind"] for m in scan["media"]} == {"listen", "watch"}, scan["media"])
if scan["docs"]:
    check("M4 任务ID稳定(内容不变ID不变)", gu4._scan_local_tasks()["docs"][0]["id"] == scan["docs"][0]["id"])

# 媒体播放完成 → 奖励发放一次（用 watch 的 mp4 测；listen 的 mp3 留在待办里）
mid = next(m["id"] for m in scan["media"] if m["kind"] == "watch")
prog = gu4._load_media_progress()
prog[mid] = {"done": True, "pos": 999, "name": "test"}
gu4._save_media_progress(prog)
gu4.local_scan = scan
gu4.local_media = scan["media"]
g.save_ai_score({"results": {}, "rewards": []})
gu4.ai_doc = g.load_ai_score()
gu4._grant_local_rewards()
rewards = g.load_ai_score().get("rewards", [])
check("M5 媒体完成发奖励一次", len([r for r in rewards if r["task"] == mid]) == 1, rewards)
gu4._grant_local_rewards()
rewards = g.load_ai_score().get("rewards", [])
check("M6 重复发放只记一次", len([r for r in rewards if r["task"] == mid]) == 1, rewards)

# apply_state 后本地 MD 任务并入 AI 任务列表（分类区可见、可开始评分）；音视频进入播放待办
gu4.apply_state(DATA(computer={"minutes": 30, "unlock": False, "required": []}))
check("M7 本地MD任务并入AI任务列表", any(t.get("local") and t["kind"] == "words" for t in gu4.ai_tasks),
      [(t["kind"], t.get("local")) for t in gu4.ai_tasks])
check("M8 完成的移出待办、未完成的在待办", len(gu4._media_todo("watch")) == 0
      and len(gu4._media_todo("listen")) == 1,
      (gu4._media_todo("listen"), gu4._media_todo("watch")))
# 合集：子文件夹 = 合集，集内连续播放
COLL = os.path.join(MEDIA_TMP, "纪录片蓝猫")
os.makedirs(COLL, exist_ok=True)
for i, fn in enumerate(("01.mp3", "02.mp3")):
    with open(os.path.join(COLL, fn), "wb") as f:
        f.write(b"ID3fake%d" % i)
scan2 = gu4._scan_local_tasks()
coll_items = [m for m in scan2["media"] if m.get("collection") == "纪录片蓝猫"]
check("M9 子文件夹→合集（递归扫描）", len(coll_items) == 2
      and all(m["kind"] == "listen" for m in coll_items), scan2["media"])
todo_l = gu4._media_todo("listen") if hasattr(gu4, "local_media") else []
gu4.local_scan = scan2
gu4.local_media = scan2["media"]
todo_l = [m for m in gu4._media_todo("listen") if m.get("collection") == "纪录片蓝猫"]
check("M10 合集待办含当前集（第一集）", len(todo_l) == 2 and todo_l[0]["name"] == "01", todo_l)
nxt = gu4._next_episode(todo_l[0])
check("M11 合集连播：下一集正确", nxt is not None and nxt["name"] == "01", nxt)
prog = gu4._load_media_progress()
prog[todo_l[0]["id"]] = {"done": True, "pos": 0}
gu4._save_media_progress(prog)
nxt2 = gu4._next_episode(todo_l[0])
check("M12 第一集完成后接第二集", nxt2 is not None and nxt2["name"] == "02", nxt2)

# M14: 完成的任务文件自动归档到 media\已完成\（不再重复识别为任务）
doc_t = next(t for t in scan["docs"] if t["kind"] == "read")
g.save_ai_score({"results": {doc_t["id"]: {g.today_key(): {"attempts": 1, "best": 95, "pass": True}}},
                 "rewards": []})
gu4.ai_doc = g.load_ai_score()
gu4._grant_local_rewards()
done_dir = os.path.join(MEDIA_TMP, "已完成")
archived = os.path.join(done_dir, os.path.basename(doc_t["path"]))
check("M14a 完成文件归档到已完成文件夹", os.path.exists(archived)
      and not os.path.exists(doc_t["path"]), (archived, doc_t["path"]))
scan3 = gu4._scan_local_tasks()
check("M14b 归档后不再扫描为任务", all(t["id"] != doc_t["id"] for t in scan3["docs"]))
prog_all = gu4._load_media_progress()
check("M14c 进度标记隐藏(归档失败的保险)", bool(prog_all.get(doc_t["id"], {}).get("done")))
gu4.apply_state(DATA(computer={"minutes": 30, "unlock": False, "required": []}))
check("M14d apply_state 不再并入已完成文档", all(t["id"] != doc_t["id"] for t in gu4.ai_tasks))

# M15: 类型文件夹——阅读/单词/视频/音频，类型以文件夹为准
d_read = os.path.join(MEDIA_TMP, "阅读"); os.makedirs(d_read, exist_ok=True)
d_words = os.path.join(MEDIA_TMP, "单词"); os.makedirs(d_words, exist_ok=True)
d_watch = os.path.join(MEDIA_TMP, "视频"); os.makedirs(d_watch, exist_ok=True)
d_listen = os.path.join(MEDIA_TMP, "音频"); os.makedirs(d_listen, exist_ok=True)
with open(os.path.join(d_read, "阅读夹里的单词样式.md"), "w", encoding="utf-8") as f:
    f.write("单词样式\nthink\napple\ncar\n")          # 单词样式内容 → 放阅读夹 = 朗读任务
with open(os.path.join(d_words, "Unit9.md"), "w", encoding="utf-8") as f:
    f.write("Unit9\nsunny\ncloudy\nrainy\n")
with open(os.path.join(d_watch, "ep.mp4"), "wb") as f:
    f.write(b"x")
with open(os.path.join(d_listen, "ep.mp3"), "wb") as f:
    f.write(b"y")
scan4 = gu4._scan_local_tasks()
rd = next(t for t in scan4["docs"] if t["name"] == "单词样式")
wd = next(t for t in scan4["docs"] if t["name"].endswith("Unit9"))
check("M15a 阅读夹内容一律朗读任务", rd["kind"] == "read", rd["kind"])
check("M15b 单词夹内容一律单词任务", wd["kind"] == "words", wd["kind"])
check("M15c 视频音频夹按类型收任务",
      any(m["kind"] == "watch" for m in scan4["media"] if "视频" in m["path"])
      and any(m["kind"] == "listen" for m in scan4["media"] if "音频" in m["path"]),
      [(m["kind"], m["path"]) for m in scan4["media"]])

# M16: 阅读任务按字数计奖励（每 250 字 = 10 分钟，不足 250 字按 10 分钟起）
long_text = "今天天气真好，我和小伙伴们一起去公园里玩耍，我们看到了很多美丽的花朵和绿油油的小草。" * 12   # ≈ 504 字
short_text = "秋天来了，树叶黄了，一群大雁往南飞，一会儿排成个人字，一会儿排成个一字，真是好看极了。"
with open(os.path.join(d_read, "长课文.md"), "w", encoding="utf-8") as f:
    f.write("长课文\n" + long_text)
with open(os.path.join(d_read, "短课文.md"), "w", encoding="utf-8") as f:
    f.write("短课文\n" + short_text)
scan5 = gu4._scan_local_tasks()
ml = next(t for t in scan5["docs"] if t["name"] == "长课文")
ms = next(t for t in scan5["docs"] if t["name"] == "短课文")
check("M16a 长文按250字=10分钟计", ml["minutes"] == round(ml["chars"] / 250.0 * 10)
      and ml["minutes"] >= 20, (ml["chars"], ml["minutes"]))
check("M16b 短文保底10分钟", ms["minutes"] == 10, (ms["chars"], ms["minutes"]))

# M17: 单词任务按行数计奖励（每行 0.5 分钟，多行汇总）
with open(os.path.join(d_words, "十个词.md"), "w", encoding="utf-8") as f:
    f.write("十个词\n" + "\n".join("w%d" % i for i in range(10)))
with open(os.path.join(d_words, "二十个词.md"), "w", encoding="utf-8") as f:
    f.write("二十个词\n" + "\n".join("q%d" % i for i in range(20)))
scan6 = gu4._scan_local_tasks()
t10 = next(t for t in scan6["docs"] if t["name"] == "十个词")
t20 = next(t for t in scan6["docs"] if t["name"] == "二十个词")
check("M17a 10行=5分钟", t10["minutes"] == 5 and t10["chars"] == 10, (t10["chars"], t10["minutes"]))
check("M17b 20行=10分钟", t20["minutes"] == 10, (t20["chars"], t20["minutes"]))

# M18: 音视频计奖 = 播放总时长 ÷ 2（时长解析 + 换算）
import wave as _wave
with _wave.open(os.path.join(d_listen, "长音频.wav"), "wb") as w:      # 120 秒 → 奖励 1 分钟
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"\x00\x00" * 16000 * 120)
scan7 = gu4._scan_local_tasks()
au = next(m for m in scan7["media"] if m["name"] == "长音频")
check("M18a 音频时长解析(≈120秒)", abs(au["duration_ms"] - 120000) < 1500, au["duration_ms"])
check("M18b 奖励=时长÷2(120秒→1分钟)", au["minutes"] == 1, au["minutes"])
try:
    import cv2 as _cv2b
    import numpy as _np2
    vp = os.path.join(d_watch, "一分钟测试.mp4")
    vw = _cv2b.VideoWriter(vp, _cv2b.VideoWriter_fourcc(*"mp4v"), 15, (320, 180))
    for i in range(15 * 60):                     # 60 秒
        vw.write(_np2.zeros((180, 320, 3), dtype=_np2.uint8))
    vw.release()
    scan8 = gu4._scan_local_tasks()
    vd = next(m for m in scan8["media"] if m["kind"] == "watch" and m["name"] == "一分钟测试")
    check("M18c 视频时长÷2(60秒→1分钟)", abs(vd["duration_ms"] - 60000) < 2000
          and vd["minutes"] == 1, (vd["duration_ms"], vd["minutes"]))
except Exception as e:
    print("  SKIP  M18c（cv2 不可用）：%r" % e)
check("M18d 时长缓存生效(同ID不重测)", gu4._scan_local_tasks()["media"][0]["duration_ms"]
      == scan7["media"][0]["duration_ms"])

# M19: 媒体行渲染回归（文案格式错误会让整个媒体区块消失）
def _all_text(widget, acc):
    for w in widget.winfo_children():
        try:
            acc.append(str(w.cget("text")))
        except Exception:
            pass
        _all_text(w, acc)
gu4.local_media = scan8["media"]
gu4._todo_tab = "watch"
gu4._todo_sig = None
gu4.render()
txts = []
_all_text(gu4.todo_inner, txts)
check("M19 媒体行渲染完整(本地/时长/奖励齐全)", any("本地视频" in t for t in txts)
      and any("时长约" in t for t in txts) and any("奖励" in t for t in txts), txts[:6])

# M13: 防跳看——视频时长解析 + 观看时长判定
with open(os.path.join(MEDIA_TMP, "ten_sec.mp4"), "wb") as f:
    pass
try:
    import cv2 as _cv2
    import numpy as _np
    vw = _cv2.VideoWriter(os.path.join(MEDIA_TMP, "ten_sec.mp4"),
                          _cv2.VideoWriter_fourcc(*"mp4v"), 15, (320, 180))
    for i in range(150):
        vw.write(_np.zeros((180, 320, 3), dtype=_np.uint8))
    vw.release()
    dur = g._video_duration_ms(os.path.join(MEDIA_TMP, "ten_sec.mp4"))
    check("M13a 视频时长解析(≈10秒)", abs(dur - 10000) < 1500, dur)
    ok1, r1 = g._watch_enough(dur, 5)          # 只看 5 秒 → 拒绝
    ok2, r2 = g._watch_enough(dur, 120)        # 看 2 分钟 → 通过
    check("M13b 防跳判定(短拒长过)", (not ok1) and ok2 and r1 > 0, (ok1, ok2, r1, r2))
    ok3, _ = g._watch_enough(0, 60)            # 时长未知 → 需 20 分钟
    ok4, _ = g._watch_enough(0, 1300)
    check("M13c 时长未知需20分钟", (not ok3) and ok4)
except Exception as e:
    print("  SKIP  M13（cv2 不可用）：%r" % e)
os.remove(os.path.join(MEDIA_TMP, "ten_sec.mp4"))

try:
    gu4.root.destroy()
except Exception:
    pass
shutil.rmtree(MEDIA_TMP, ignore_errors=True)

print("== N. 奖励轮暂停/继续/提前结束退回 ==")
gu5 = g.Guardian()
gu5.build_ui()
gu5.root.withdraw()
gu5.apply_state(DATA(computer={"minutes": 30, "unlock": False, "required": []}))
gu5.enter_unlock(parent=False, reward=(120 * 60, ["r1"]))   # 120 分钟奖励轮
check("N1 奖励轮进入", gu5.mode == g.Guardian.MODE_UNLOCKED and gu5.reward_ids == ["r1"])
gu5.end_time = time.time() + 100 * 60                        # 剩 100 分钟
gu5._pause_reward_use()
check("N2 暂停→回锁定页", gu5.mode == g.Guardian.MODE_LOCKED)
rstate, rend = g.load_reward_session()
check("N3 会话保留可恢复(剩余秒数≈100分钟)", rstate == "paused"
      and 90 * 60 < rend <= 101 * 60, (rstate, rend))
spent_before = len(g.load_spent())
gu5.start_reward_use()
check("N4 开始使用→恢复奖励轮(不重复扣台账)", gu5.mode == g.Guardian.MODE_UNLOCKED
      and gu5.reward_ids == [] and len(g.load_spent()) == spent_before)
# 跨天恢复：把会话日期改成昨天
try:
    p0 = g._reward_session_paths()[0]
    if os.path.exists(p0):
        os.remove(p0)                        # 隐藏文件需先删再写
    d0 = {"date": "2000-01-01", "paused": True, "remaining": 100 * 60,
          "end_time": time.time() - 86400, "unlock_start": time.time() - 90000}
    json.dump(d0, open(p0, "w", encoding="utf-8"))
    rstate2, rend2 = g.load_reward_session()
    check("N5 跨天仍可恢复", rstate2 == "paused" and rend2 == 100 * 60, (rstate2, rend2))
except Exception as e:
    check("N5 跨天仍可恢复", False, repr(e))
# 提前结束 → 剩余退回钱包
gu5.end_time = time.time() + 30 * 60
gu5._end_reward_use()
rewards_n = g.load_ai_score().get("rewards", [])
check("N6 结束→剩余30分钟退回", gu5.mode == g.Guardian.MODE_LOCKED
      and len([r for r in rewards_n if str(r.get("task", "")).startswith("resume")
               and r.get("minutes") == 30]) == 1, rewards_n)
# N6b: 向上取整——剩 29:30 也退整 30 分钟（不丢零头）
gu5.enter_unlock(parent=False, reward=(60 * 60, ["r5"]))
gu5.end_time = time.time() + 30 * 60 - 30
gu5._end_reward_use()
rewards_n2 = g.load_ai_score().get("rewards", [])
check("N6b 剩29:30退整30分钟", len([r for r in rewards_n2 if str(r.get("task", "")).startswith("resume")
                                    and r.get("minutes") == 30]) >= 1, rewards_n2[-3:])
gu5.apply_state(DATA(computer={"minutes": 30, "unlock": False, "required": []}))
check("N7 退回时间入钱包", any(w["minutes"] == 30 and "剩余" in w["name"] for w in gu5.wallet),
      gu5.wallet)

# N8: 护眼模式——每 35 分钟全屏绿幕休息 3 分钟（休息不扣倒计时）
gu5.eye_base = time.time() - 36 * 60          # 已使用 36 分钟
gu5.end_time = time.time() + 60 * 60
gu5._eye_tick(time.time())
check("N8a 满35分钟触发绿幕", gu5.eye_overlay is not None and gu5.eye_until is not None)
end_frozen = gu5.end_time
time.sleep(1.1)
gu5._eye_tick(time.time())
check("N8b 休息期间倒计时冻结", abs(gu5.end_time - end_frozen) < 0.5
      or gu5.end_time >= end_frozen, (end_frozen, gu5.end_time))
gu5.eye_until = time.time() - 1               # 休息结束
gu5._eye_tick(time.time())
check("N8c 休息结束绿幕关闭并重新累计", gu5.eye_overlay is None
      and gu5.eye_base is not None and (time.time() - gu5.eye_base) < 5)
check("N8d 休息时间不扣使用时长", gu5.end_time >= end_frozen - 0.5)
gu5._eye_abort()

# N9: 暂停/结束时同步收起媒体面板（此前面板会盖住锁定页导致无法操作）
os.makedirs(MEDIA_TMP, exist_ok=True)
with _wave.open(os.path.join(MEDIA_TMP, "panel_test.wav"), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"\x00\x00" * 16000 * 120)     # 120 秒，避免“瞬间播完”触发完成
gu5.enter_unlock(parent=False, reward=(60 * 60, ["r2"]))
gu5.cfg["media_dir"] = MEDIA_TMP
mt9 = {"id": "med_t9", "kind": "listen", "name": "面板测试",
       "path": os.path.join(MEDIA_TMP, "panel_test.wav"), "collection": "",
       "minutes": 1, "local": True}
gu5.local_media = [mt9]
gu5.open_media_player(dict(mt9))
check("N9a 播放面板已打开", gu5.media_panel is not None)
gu5._pause_reward_use()
check("N9b 暂停时媒体面板同步收起", gu5.media_panel is None
      and gu5._media_state is None and gu5.mode == g.Guardian.MODE_LOCKED)
check("N9c 媒体进度已保留", "pos" in gu5._load_media_progress().get("med_t9", {}),
      gu5._load_media_progress().get("med_t9"))
check("N9d 奖励会话仍保留可续", g.load_reward_session()[0] == "paused")
gu5.start_reward_use()
gu5._end_reward_use()
g.clear_reward_session()

# N10: 暂停期间墙钟流逝不扣时间（本 bug 的核心回归：旧实现存时间点，暂停=照扣）
gu5.enter_unlock(parent=False, reward=(60 * 60, ["r4"]))
gu5.end_time = time.time() + 50 * 60
gu5._pause_reward_use()
time.sleep(3)                                  # 暂停挂 3 秒
gu5.start_reward_use()
left = gu5.end_time - time.time()
check("N10 暂停期间不流失(3秒后仍≈50分钟)", gu5.mode == g.Guardian.MODE_UNLOCKED
      and 49 * 60 < left <= 50 * 60, left)
gu5._end_reward_use()
g.clear_reward_session()
try:
    gu5.root.destroy()
except Exception:
    pass

# ---- 收尾：清理影子副本测试产物并还原 ----
shutil.rmtree(APPDATA, ignore_errors=True)
if had_bak_dir:
    shutil.move(BAK, APPDATA)

print("\n========== 通过 %d 项 / 失败 %d 项 ==========" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
