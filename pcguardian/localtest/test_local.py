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
gr2.apply_state(RDATA()); gr2.render()
check("R9 重启后台账生效(余额15)", gr2._money_left() == 15.0 and "15.00" in gr2.lb_money_total.cget("text"))
check("R10 已兑换时长跨重启在钱包", sum(1 for w in gr2.wallet if w["id"].startswith("rd-")) == 2)
# 关闭兑换 → 面板消失
gr2.apply_state(RDATA(computer={"minutes": 30, "unlock": False, "redeem": {"enabled": False, "dailyCap": 10}}))
gr2.render()
check("R11 关闭后兑换分布隐藏", not gr2.redeem_frame.winfo_manager())
# 余额不足
gr3 = g.Guardian(); gr3.build_ui(); gr3.root.withdraw()
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

# ---- 收尾：清理影子副本测试产物并还原 ----
shutil.rmtree(APPDATA, ignore_errors=True)
if had_bak_dir:
    shutil.move(BAK, APPDATA)

print("\n========== 通过 %d 项 / 失败 %d 项 ==========" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
