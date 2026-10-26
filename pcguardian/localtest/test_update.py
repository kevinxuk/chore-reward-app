# -*- coding: utf-8 -*-
"""guardian 自动升级 —— 本地 E2E 测试
覆盖：清单获取失败（网络）、无更新、有更新（.pyw 下载+sha256 校验+换位+重启 bat 生成）、
     sha 不符拒绝安装、update_base 覆盖生效。
运行：python test_update.py   （本目录；自动拷贝最新 guardian.pyw / PCVoiceScore.pyw 为被测对象）
说明：update_base 指向本地目录（file:// URL）模拟升级服务器；subprocess.Popen 被替换为
     记录器，被测代码运行时才 import subprocess 取属性，替换模块属性即生效——真机不会重启。
"""
import hashlib, importlib.util, json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.dirname(HERE)
APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PCGuardian")

# ---- 隔离影子副本（同 test_ai.py） ----
BAK = APPDATA + ".testbak"
had_bak_dir = os.path.exists(APPDATA)
if had_bak_dir:
    shutil.rmtree(BAK, ignore_errors=True)
    shutil.move(APPDATA, BAK)

shutil.copy(os.path.join(PROD, "guardian.pyw"), os.path.join(HERE, "guardian.pyw"))
shutil.copy(os.path.join(PROD, "PCVoiceScore.pyw"), os.path.join(HERE, "PCVoiceScore.pyw"))

class FakePopen:
    calls = []
    def __init__(self, *a, **k):
        FakePopen.calls.append(a)

_real_popen = subprocess.Popen
subprocess.Popen = FakePopen

def _imp(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

G = _imp("g_update_guardian", os.path.join(HERE, "guardian.pyw"))

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else "FAIL  ") + name + (("   << " + str(extra) + " >>") if (extra and not cond) else ""))

def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()

SERVER = os.path.join(HERE, "update_server_sim")
BASE = "file:///" + SERVER.replace("\\", "/")

def build_server(version, marker):
    """生成升级服务器目录：marker 非空时文件内容加标记（制造与本地差异）。"""
    shutil.rmtree(SERVER, ignore_errors=True)
    os.makedirs(SERVER)
    files = {}
    for n in ("guardian.pyw", "PCVoiceScore.pyw"):
        b = open(os.path.join(HERE, n), "rb").read()
        if marker:
            b = b + ("\n# UPDATE_MARKER %s\n" % marker).encode("utf-8")
        open(os.path.join(SERVER, n), "wb").write(b)
        files[n] = {"sha256": sha_bytes(b)}
    json.dump({"version": version, "files": files},
              open(os.path.join(SERVER, "version.json"), "w"), indent=2)

def fresh_local_cfg(extra):
    with open(os.path.join(HERE, "guardian.local.json"), "w", encoding="utf-8") as f:
        json.dump(extra, f)

def restore_local_files():
    shutil.copy(os.path.join(PROD, "guardian.pyw"), os.path.join(HERE, "guardian.pyw"))
    shutil.copy(os.path.join(PROD, "PCVoiceScore.pyw"), os.path.join(HERE, "PCVoiceScore.pyw"))

def cleanup_app_files():
    for f in ("update_restart.bat", "guardian.pyw.new", "PCVoiceScore.pyw.new", "guardian.log"):
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            os.remove(p)

def new_guardian():
    g = G.Guardian()
    g._stop_up.set()          # 停掉后台升级线程（避免测试期间再真跑一轮）
    g.build_ui()
    g.root.withdraw()
    return g

print("== U. 自动升级 E2E（update_base=file:// 本地模拟，Popen 已伪化） ==")

# U1 清单获取失败（网络不通）→ 跳过本轮，不崩溃不重启
build_server("t0", None)
fresh_local_cfg({"update_base": "http://127.0.0.1:9/nope"})
cleanup_app_files()
g = new_guardian()
g._check_update()
check("U1 清单获取失败不崩溃", not FakePopen.calls and not os.path.exists(os.path.join(HERE, "guardian.pyw.new")))
g.root.destroy()

# U2 无更新：清单与本地一致 → 只记版本，不下载不重启
build_server("2026-10-25.test1", None)
fresh_local_cfg({"update_base": BASE})
g = new_guardian()
g._check_update()
check("U2 已是最新：不下载不重启", g._update_state.get("version") == "2026-10-25.test1"
      and not FakePopen.calls
      and not os.path.exists(os.path.join(HERE, "guardian.pyw.new")),
      (g._update_state, FakePopen.calls))
g.root.destroy()

# U3 有更新：服务器文件加标记 → 本地文件被替换 + .new 清理 + 重启 bat 生成（未执行）
build_server("2026-10-25.test2", "U3")
g = new_guardian()
g._check_update()
gb = open(os.path.join(HERE, "guardian.pyw"), "rb").read()
sb = open(os.path.join(HERE, "PCVoiceScore.pyw"), "rb").read()
bat_path = os.path.join(HERE, "update_restart.bat")
bat = open(bat_path, encoding="utf-8").read() if os.path.exists(bat_path) else ""
check("U3.1 两个文件都已更新到服务器版", b"UPDATE_MARKER U3" in gb and b"UPDATE_MARKER U3" in sb)
check("U3.2 .new 残留已清理", not os.path.exists(os.path.join(HERE, "guardian.pyw.new"))
      and not os.path.exists(os.path.join(HERE, "PCVoiceScore.pyw.new")))
check("U3.3 重启流程已触发（伪Popen 记录 1 次）", len(FakePopen.calls) == 1, FakePopen.calls)
check("U3.4 bat 内容正确（非冻结：pythonw + guardian.pyw）",
      "pythonw" in bat and "guardian.pyw" in bat and "timeout" in bat, bat)
check("U3.5 版本状态已记录", g._update_state.get("version") == "2026-10-25.test2", g._update_state)
g.root.destroy()

# U4 sha 不符 → 拒绝安装（文件保持原样、不重启）
build_server("2026-10-25.test3", "U4")
man_bad = json.load(open(os.path.join(SERVER, "version.json")))
man_bad["files"]["guardian.pyw"]["sha256"] = "0" * 64
json.dump(man_bad, open(os.path.join(SERVER, "version.json"), "w"))
restore_local_files()
FakePopen.calls.clear()
g = new_guardian()
g._check_update()
check("U4 校验失败拒绝安装", b"UPDATE_MARKER U4" not in open(os.path.join(HERE, "guardian.pyw"), "rb").read()
      and not FakePopen.calls, FakePopen.calls)
g.root.destroy()

# U5 update_base 未配置时走 GitHub raw（只验证 URL 生成逻辑，不发请求）
p = os.path.join(HERE, "guardian.local.json")
if os.path.exists(p):
    os.remove(p)
g = new_guardian()
urls = g._update_urls("version.json")
check("U5 默认地址=GitHub raw + jsdelivr 兜底", len(urls) == 2
      and urls[0].startswith("https://raw.githubusercontent.com/")
      and urls[1].startswith("https://cdn.jsdelivr.net/"), urls)
g.root.destroy()

# ---- 恢复现场 ----
subprocess.Popen = _real_popen
shutil.rmtree(SERVER, ignore_errors=True)
p = os.path.join(HERE, "guardian.local.json")
if os.path.exists(p):
    os.remove(p)
cleanup_app_files()
restore_local_files()
shutil.rmtree(APPDATA, ignore_errors=True)
if had_bak_dir:
    shutil.move(BAK, APPDATA)

print("\n========== 通过 %d 项 / 失败 %d 项 ==========" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
