# -*- coding: utf-8 -*-
"""make_manifest.py —— 发布更新后运行：按 pcguardian/ 下的实际文件生成 version.json
（含 sha256 校验值）。儿童端启动约 90 秒后自动检测并下载安装。
用法：python make_manifest.py [版本号]   （默认用时间戳，如 2026-10-25.1）
"""
import hashlib, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["PCVoiceScore.pyw", "guardian.pyw"]
EXE = os.path.join(os.path.dirname(HERE), "build_pkg", "dist", "PCGuardian.exe")

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

man = {"version": sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d.1"),
       "files": {n: {"sha256": sha(os.path.join(HERE, n))} for n in FILES}}
if os.path.exists(EXE):
    man["exe"] = {"version": man["version"], "sha256": sha(EXE), "size": os.path.getsize(EXE),
                  "url": "https://github.com/kevinxuk/chore-reward-app/releases/latest/download/PCGuardian.exe"}
out = os.path.join(HERE, "version.json")
json.dump(man, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("version.json 已生成:", man["version"])
for n in FILES:
    print("  ", n, man["files"][n]["sha256"][:12])
if "exe" in man:
    print("   exe", man["exe"]["sha256"][:12])
