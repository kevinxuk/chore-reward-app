# -*- coding: utf-8 -*-
"""reset_demo.py —— 一键重置联调环境（数据 + 孩子端本地状态 + 回执 + 日志）
用法：python reset_demo.py   （不影响局域网服务器；手机端刷新页面即可拿到重置数据）
"""
import json, os, shutil, time

HERE = os.path.dirname(os.path.abspath(__file__))
APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PCGuardian")

d = json.load(open(os.path.join(HERE, "seed-default.json"), encoding="utf-8"))
d["childName"] = "悠悠"
d["config"]["computer"] = {"minutes": 30, "unlock": False,
                           "redeem": {"enabled": True, "dailyCap": 10},
                           "required": []}
d["config"]["ai"] = {"cfgUrl": "C:/PCGuardian/localtest/ai-config.json"}
READ = ("秋天的雨，是一把钥匙。它带着清凉和温柔，轻轻地，趁你没留意，"
        "把秋天的大门打开了。秋天的雨，有一盒五彩缤纷的颜料。你看，它把黄色给了银杏树，"
        "把红色给了枫树，把金黄色给了田野，把橙红色给了果树。")
d["config"]["dispatch"] = {"tasks": [
    {"id": "dp1", "name": "完成数学口算一页", "minutes": 20, "createdAt": "2026-10-24T09:00:00"},
    {"id": "dpx1", "kind": "read", "name": "朗读《秋天的雨》", "minutes": 20,
     "content": READ, "threshold": 80, "quiz": True, "createdAt": "2026-10-24T10:00:00"},
    {"id": "dpx2", "kind": "words", "name": "Unit3 单词背诵", "minutes": 10,
     "content": ["think", "through", "weather", "apple", "car"], "threshold": 80,
     "createdAt": "2026-10-24T10:00:00"}]}
d["records"], d["expenses"] = {}, {}
d["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
json.dump(d, open(os.path.join(HERE, "chore-data.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

cleared = []
for base in (HERE, APPDATA):
    for f in ("guardian.session.json", "guardian.reward.session.json",
              "guardian.spent.json", "guardian.aiscore.json", "guardian.redeem.json"):
        p = os.path.join(base, f)
        if os.path.exists(p):
            os.remove(p); cleared.append(f)
if os.path.isdir(APPDATA) and not os.listdir(APPDATA):
    shutil.rmtree(APPDATA, ignore_errors=True)
for f in ("chore-ai-results.json", "receipt.json", "guardian.log", "scorer-run.log"):
    p = os.path.join(HERE, f)
    if os.path.exists(f and p):
        open(p, "w").close()
print("重置完成；清掉:", sorted(set(cleared)) or "无残留")
