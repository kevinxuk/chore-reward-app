# -*- coding: utf-8 -*-
"""
PC Guardian —— 儿童电脑使用时间管控程序
========================================
与 https://kevinxuk.github.io/chore-reward-app/ 家长后台联动（共用同一份 Gist 数据）：

  ① 开机自启后立即进入「锁定模式」：全屏置顶 + 低级键盘钩子屏蔽键盘，
     屏幕上只有一个「关机」按钮可以点击；
  ② 每 poll_seconds 秒（默认 60）拉取 Gist chore-data.json，检查今天的打卡记录：
       - 「语文/英语阅读」和「背单词/古诗词」是每日使用电脑的**前提**（家长打卡或 AI 评分通过均可）；
       - 电脑时长全部来自家长「电脑任务分派」：普通任务打卡奖励 / AI 评分奖励 / 零花钱自助兑换，
         孩子在锁定页勾选（可多选累计）点「▶ 开始使用」主动开始；任一必做未完成 → 保持锁定。
       - 家长在后台打开「临时解锁」→ 无倒计时直接解锁（关闭后恢复管控）；
  ③ 解锁期间屏幕右上角悬浮倒计时；家长在后台改时长，1 分钟内生效（延长/缩短均可）；
  ④ 倒计时归零 → 重新锁定 → 屏幕可见倒计时后自动执行强制关机；
  ⑤ 任务分派（奖励时间）：家长在后台发布额外任务（不产生零花钱），完成后孩子可在
     锁定页勾选（支持多选）累计使用对应电脑时长；点击「开始使用」即从本机台账扣除，
     中途重启/杀进程不退不重（会话文件恢复剩余时间）。还有奖励可用时到点不关机。

仅用 Python 标准库（tkinter + ctypes + urllib），无需 pip 安装任何东西。
用法： pythonw guardian.pyw            正常运行
      pythonw guardian.pyw --install  写入开机自启（HKCU Run 键）
      pythonw guardian.pyw --remove   移除开机自启
      pythonw guardian.pyw --demo     测试模式：小窗口、不装钩子、不真关机
本地联调： guardian.local.json 的 gist_url 可填本地文件路径（如 C:/PCGuardian/localtest/
      chore-data.json 或 file:/// 开头），配合家长端「🔗 绑定数据文件」即可脱离 Gist 全链路测试。
"""
import json
import os
import re
import sys
import time
import queue
import ctypes
import hashlib
import datetime
import subprocess
import threading
import urllib.request
from ctypes import wintypes

import tkinter as tk

APP_NAME = "PCGuardian"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "PCGuardian"
UPDATE_REPO_RAW = "https://raw.githubusercontent.com/kevinxuk/chore-reward-app/main/pcguardian"
UPDATE_REPO_JSD = "https://cdn.jsdelivr.net/gh/kevinxuk/chore-reward-app@main/pcguardian"
UPDATE_FILES = ("PCVoiceScore.pyw", "guardian.pyw")

DEFAULT_CFG = {
    # 打卡数据的云端地址（Gist raw，免认证只读、永远指向最新版本）
    "gist_url": "https://gist.githubusercontent.com/kevinxuk/b96c91c9966987c90871a9dfb59520d9/raw/chore-data.json",
    "poll_seconds": 60,          # 解锁后的轮询间隔（秒）
    "poll_seconds_locked": 5,    # 锁定期间的轮询间隔（秒）——家长改动约10秒内生效
    "default_minutes": 30,       # 后台未配置时的默认可用时长
    "required_ids": ["t13", "t17"],      # 语文/英语阅读、背单词/古诗词
    "required_name_hints": ["阅读", "背单词"],  # 任务列表里找不到默认 id 时按名称兜底
    "congrats_seconds": 6,       # 「恭喜完成」提示停留秒数
    "shutdown_seconds": 20,      # 关机执行前屏幕可见的倒数秒数（同时回到锁定页）
    "expire_grace_seconds": 7200,  # 倒计时走完后多久内重启算「躲避关机」（补关机），超过则当天锁定到明天
    "reward_max_minutes": 480,   # 奖励时间单次最多可勾选使用的分钟数（防数据异常导致超长解锁）
    "redeem_weekend_only": True, # 零花钱兑换仅在周六/周日开放
    "ai_max_attempts": 5,        # AI 评分任务每天每任务默认尝试次数上限（任务里可覆盖）
    "dry_run": False,            # True = 只打印不真关机（--demo 强制为 True）
    "log": True,
    "auto_update": True,         # 自动升级：检测 GitHub 上的新版本并自动下载安装
    "update_hours": 6,           # 升级检查间隔（小时）；启动约 90 秒后也会先查一次
    "eye_break_minutes": 35,     # 护眼：每使用 N 分钟强制休息（0 = 关闭护眼模式）
    "eye_break_seconds": 180,    # 护眼休息时长（秒）；休息期间使用倒计时冻结
    "media_dir": "",             # 本地媒体文件夹：默认 <程序目录>\media\（可设绝对路径覆盖）
}

# ---------------------------------------------------------------- 基础工具
def script_dir():
    if getattr(sys, "frozen", False):          # PyInstaller exe：用 exe 所在目录
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(sys.argv[0] or __file__))

def _audio_cfg_path():
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                        "PCGuardian", "audio.json")

def load_audio_cfg():
    """音频硬件配置（麦克风设备选择等），存孩子电脑 APPDATA——不入 Gist、不进备份。"""
    try:
        with open(_audio_cfg_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_audio_cfg(d):
    try:
        p = _audio_cfg_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

# ---- 本地媒体文件夹：家长拷入音视频/MD 文档，孩子端自动扫描成任务 ----
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma")
VIDEO_EXT = (".mp4", ".avi", ".mkv", ".mov", ".webm", ".wmv")
_WORD_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]{0,30}$")

def _local_id(prefix, path):
    """本地任务稳定 ID：同文件内容/修改时间不变则 ID 不变（评分结果、奖励不重复）。"""
    try:
        st = os.stat(path)
        key = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
    except OSError:
        key = path
    return prefix + hashlib.md5(key.encode("utf-8")).hexdigest()[:8]

def _video_duration_ms(path):
    """视频总时长（毫秒）；无法解析返回 0。"""
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        if n > 0 and fps > 0:
            return int(n / fps * 1000)
    except Exception:
        pass
    return 0

def _watch_enough(duration_ms, watched_s):
    """防跳看判定：累计观看秒数是否达到 视频时长−1分钟容差（至少看 1 分钟）；
    时长未知要求满 20 分钟。不足 = 有跳过嫌疑，不发奖励。"""
    if duration_ms and duration_ms > 0:
        need = max(60.0, (duration_ms - 60000) / 1000.0)
    else:
        need = 1200.0
    return watched_s + 1.0 >= need, max(0.0, need - watched_s)

def parse_local_md(path):
    """media 文件夹里的 .md 文件 → 本地 AI 任务。
    首行 = 标题（# 号可有可无）；正文全英文单词行（3~60 行）→ 单词任务，否则朗读任务。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            raw = [l.rstrip() for l in f.readlines()]
    except Exception:
        return None
    nonempty = [i for i, l in enumerate(raw) if l.strip()]
    if not nonempty:
        return None
    t0 = nonempty[0]
    title = raw[t0].lstrip("#").strip() or "未命名"
    body = []
    for l in raw[t0 + 1:]:
        s = l.strip().lstrip("#").strip()
        if s and not set(s) <= set("-_* "):
            body.append(s)
    if not body:
        return None
    if 3 <= len(body) <= 60 and all(_WORD_LINE_RE.match(b) for b in body):
        return {"kind": "words", "name": title[:30], "content": body}
    content = "\n".join(body)
    if len(content.replace(" ", "").replace("\n", "")) < 20:
        return None
    return {"kind": "read", "name": title[:30], "content": content}

def load_cfg():
    cfg = dict(DEFAULT_CFG)
    path = os.path.join(script_dir(), "guardian.local.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print("[cfg] 本地配置读取失败，使用默认值:", e)
    if "--demo" in sys.argv:
        cfg["dry_run"] = True
    return cfg

class Log:
    def __init__(self, enabled):
        self.path = os.path.join(script_dir(), "guardian.log")
        self.enabled = enabled
    def write(self, msg):
        line = "[%s] %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        if not self.enabled:
            return
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > 512 * 1024:
                os.remove(self.path)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

def today_key():
    n = datetime.datetime.now()
    return "%04d-%02d-%02d" % (n.year, n.month, n.day)

# ---- 会话文件：记录「今天是否已发放过时长」（防重启/杀进程刷时间） ----
def _session_paths():
    paths = [os.path.join(script_dir(), "guardian.session.json")]
    try:  # 隐藏影子副本：孩子删掉主文件也没用
        appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        paths.append(os.path.join(appdata, "PCGuardian", "guardian.session.json"))
    except Exception:
        pass
    return paths

def _hide_file(path):
    try:
        ctypes.windll.kernel32.SetFileAttributesW(path, 2)  # FILE_ATTRIBUTE_HIDDEN
    except Exception:
        pass

def save_session(end_time, unlock_start, closed=False):
    doc = json.dumps({"date": today_key(), "end_time": end_time, "unlock_start": unlock_start,
                      "closed": bool(closed)})
    for p in _session_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            try:
                os.remove(p)   # Windows 隐藏文件无法直接覆盖写，先删再建
            except FileNotFoundError:
                pass
            with open(p, "w", encoding="utf-8") as f:
                f.write(doc)
            _hide_file(p)
        except Exception:
            pass

def clear_session(keep_today=False):
    for p in _session_paths():
        try:
            if keep_today and os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    if json.load(f).get("date") == today_key():
                        continue        # 当天已发放过时长的记录不能被「取消打卡」洗掉
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

def load_session(grace_seconds=7200):
    """返回 (state, end_time)：state ∈ None/'restore'/'expired'/'used_up'
       restore  = 当天剩余倒计时还在 → 原样恢复（重启不重置）
       expired  = 当天倒计时刚走完不久（grace 内）→ 视为躲避关机，补执行
       used_up  = 当天时长早已用完 → 锁定到明天
       多处副本取 unlock_start 最新的一份；非当天的旧记录自动清除。"""
    best = None
    for p in _session_paths():
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
            if best is None or float(s.get("unlock_start") or 0) > float(best.get("unlock_start") or 0):
                best = s
        except Exception:
            pass
    if not best:
        return None, None
    try:
        end = float(best.get("end_time") or 0)
        start = float(best.get("unlock_start") or 0)
    except Exception:
        return None, None
    now = time.time()
    if best.get("date") != today_key() or start <= 0 or start > now or end <= 0:
        clear_session()              # 非当天或损坏 → 清掉，正常发新
        return None, None
    if best.get("closed"):
        return "used_up", end        # 优雅闭环（到点时还有奖励，未关机）：当天基础机会已用完
    if end > now:
        if end - now > 12 * 3600 or end - start > 24 * 3600:
            clear_session()          # 数值离谱 = 手改文件
            return None, None
        return "restore", end
    if now - end <= grace_seconds:
        return "expired", end        # 刚走完不久：躲避关机
    return "used_up", end            # 当天已用完：锁定到明天

# ---------------------------------------------------------------- 任务分派（奖励时间）
def _spent_paths():
    paths = [os.path.join(script_dir(), "guardian.spent.json")]
    try:  # 隐藏影子副本：孩子删掉主文件也没用
        appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        paths.append(os.path.join(appdata, "PCGuardian", "guardian.spent.json"))
    except Exception:
        pass
    return paths

def load_spent():
    """奖励已用台账 {任务id: {date, minutes}}；多副本取并集（任一副本标记已用即已用）。"""
    out = {}
    for p in _spent_paths():
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f).get("spent") or {}
            for k, v in doc.items():
                if isinstance(v, dict):
                    out[k] = v
        except Exception:
            pass
    return out

def save_spent(spent):
    doc = json.dumps({"spent": spent}, ensure_ascii=False)
    for p in _spent_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
            with open(p, "w", encoding="utf-8") as f:
                f.write(doc)
            _hide_file(p)
        except Exception:
            pass

def latest_done(records, tid):
    """分派任务是否已完成：取全部记录中最新一天的 done 值（家长撤销打卡即撤销奖励）。"""
    res = None
    try:
        keys = sorted(records.keys()) if isinstance(records, dict) else []
    except Exception:
        keys = []
    for dk in keys:
        entry = (records.get(dk) or {}).get(tid)
        if isinstance(entry, dict):
            res = bool(entry.get("done"))
    return res

def compute_wallet(data, spent):
    """可用奖励清单：已完成 + 未使用 的分派任务 [{id,name,minutes}]。"""
    out = []
    cfg_all = data.get("config") or {}
    disp = ((cfg_all.get("dispatch") or {}).get("tasks")) or []
    records = data.get("records") or {}
    for t in disp:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or "")
        try:
            minutes = int(t.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        if not tid or minutes <= 0 or tid in spent:
            continue
        if t.get("kind"):          # AI 评分任务：奖励由本地评分结果发放，不走云端打卡
            continue
        if latest_done(records, tid) is True:
            out.append({"id": tid, "name": str(t.get("name") or tid), "minutes": minutes})
    return out

# ---- 奖励轮会话文件：与基础会话分文件存放，互不覆盖（基础轮的「当天一次机会」不受奖励轮影响） ----
def _reward_session_paths():
    paths = [os.path.join(script_dir(), "guardian.reward.session.json")]
    try:
        appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        paths.append(os.path.join(appdata, "PCGuardian", "guardian.reward.session.json"))
    except Exception:
        pass
    return paths

def save_reward_session(end_time, unlock_start, remaining=None):
    """remaining 有值 = 暂停语义：保存「剩余秒数」而非结束时间点——
    恢复时从当前时间重新起算，暂停期间墙钟流逝不扣时间（否则暂停等于没暂停）。"""
    doc = {"date": today_key(), "end_time": end_time, "unlock_start": unlock_start}
    if remaining is not None:
        doc["paused"] = True
        doc["remaining"] = int(remaining)
    s = json.dumps(doc)
    for p in _reward_session_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
            with open(p, "w", encoding="utf-8") as f:
                f.write(s)
            _hide_file(p)
        except Exception:
            pass

def clear_reward_session():
    for p in _reward_session_paths():
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

def load_reward_session(grace_seconds=7200):
    """奖励轮会话：restore=恢复剩余；expired=走完不久（躲避关机）；None=无/已闭环。
       奖励没有「当天一次」概念：超期直接清除（时间已在选定时刻从台账扣除）。"""
    best = None
    for p in _reward_session_paths():
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
            if best is None or float(s.get("unlock_start") or 0) > float(best.get("unlock_start") or 0):
                best = s
        except Exception:
            pass
    if not best:
        return None, None
    now = time.time()
    if best.get("paused"):
        # 暂停语义：存的是「剩余秒数」，跨天/跨重启都从当前时间重新起算
        try:
            remaining = float(best.get("remaining") or 0)
        except Exception:
            remaining = 0
        if remaining <= 0:
            clear_reward_session()
            return None, None
        return "paused", remaining
    try:
        end = float(best.get("end_time") or 0)
        start = float(best.get("unlock_start") or 0)
    except Exception:
        clear_reward_session()
        return None, None
    # 允许跨天继续使用（长奖励分几天用完）；只拦截明显异常的会话
    if start <= 0 or start > now or end <= 0:
        clear_reward_session()
        return None, None
    if end > now:
        if end - now > 24 * 3600 or end - start > 24 * 3600:
            clear_reward_session()
            return None, None
        return "restore", end
    if now - end <= grace_seconds:
        return "expired", end
    clear_reward_session()
    return None, None

# ---------------------------------------------------------------- AI 评分（PCVoiceScore 共用）
def _aiscore_paths():
    paths = [os.path.join(script_dir(), "guardian.aiscore.json")]
    try:
        appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        paths.append(os.path.join(appdata, "PCGuardian", "guardian.aiscore.json"))
    except Exception:
        pass
    return paths

def load_ai_score():
    """AI 评分结果/奖励发放记录（多副本取 updatedAt 最新的一份）。"""
    best = None
    for p in _aiscore_paths():
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
            if best is None or float(s.get("updatedAt") or 0) > float(best.get("updatedAt") or 0):
                best = s
        except Exception:
            pass
    return best or {"updatedAt": 0, "results": {}, "rewards": []}

def save_ai_score(doc):
    doc["updatedAt"] = time.time()
    body = json.dumps(doc, ensure_ascii=False)
    for p in _aiscore_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            _hide_file(p)
        except Exception:
            pass

# ---------------------------------------------------------------- 零花钱兑换电脑时长（本机记账）
def _redeem_paths():
    paths = [os.path.join(script_dir(), "guardian.redeem.json")]
    try:
        appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        paths.append(os.path.join(appdata, "PCGuardian", "guardian.redeem.json"))
    except Exception:
        pass
    return paths

def load_redeem():
    """兑换台账：totalSpent=累计消费（元），days[日期]={spent, entries}。"""
    best = None
    for p in _redeem_paths():
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
            if best is None or float(s.get("updatedAt") or 0) > float(best.get("updatedAt") or 0):
                best = s
        except Exception:
            pass
    return best or {"updatedAt": 0, "totalSpent": 0.0, "days": {}}

def save_redeem(doc):
    doc["updatedAt"] = time.time()
    body = json.dumps(doc, ensure_ascii=False)
    for p in _redeem_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            _hide_file(p)
        except Exception:
            pass

def cloud_balance(data):
    """云端零花钱余额（与家长端 totalBalance 同口径：全部已打卡金额 - 全部消费）。"""
    inc = exp = 0.0
    for day in (data.get("records") or {}).values():
        for v in (day or {}).values():
            try:
                if isinstance(v, dict) and v.get("done"):
                    inc += float(v.get("amount") or 0)
            except (TypeError, ValueError):
                pass
    for lst in (data.get("expenses") or {}).values():
        if isinstance(lst, list):
            for e in lst:
                try:
                    exp += float((e or {}).get("amount") or 0)
                except (TypeError, ValueError):
                    pass
    return round(inc - exp, 2)

def fetch_state(url, timeout=10):
    # 本地文件做数据源（本地联调）：直接读文件，无缓存问题
    low = url[:8].lower()
    if low.startswith("file://") or url.startswith("\\\\") or (len(url) > 2 and url[1] == ":"):
        path = url
        if low.startswith("file:///"):
            path = url[8:]
        elif low.startswith("file://"):
            path = url[7:]
        path = path.replace("/", os.sep)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    # 加 cache-buster，避免 gist CDN 的 ~5 分钟缓存让打卡更新迟迟不可见
    if "?" not in url:
        url = url + "?cb=%d" % int(time.time())
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "PCGuardian/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def resolve_required(tasks, cfg):
    """返回 [(id, 显示名)]；先按默认 id 匹配，缺失的按名称关键字兜底。"""
    out, used = [], set()
    names = {t.get("id"): t.get("name", "") for t in tasks if isinstance(t, dict)}
    for tid in cfg["required_ids"]:
        if tid in names:
            out.append((tid, names[tid])); used.add(tid)
    if len(out) < len(cfg["required_ids"]) and tasks:
        for hint in cfg["required_name_hints"]:
            for t in tasks:
                if isinstance(t, dict) and hint in t.get("name", "") and t["id"] not in used:
                    out.append((t["id"], t.get("name", ""))); used.add(t["id"]); break
    # 显示名兜底
    if not out:
        for i, hint in enumerate(cfg["required_name_hints"]):
            out.append((cfg["required_ids"][i] if i < len(cfg["required_ids"]) else "t%d" % (13 + i), hint + "（默认）"))
    return out

# ---------------------------------------------------------------- 键盘钩子
WH_KEYBOARD_LL = 13
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT))

class KeyBlocker:
    """锁定期间用低级键盘钩子吞掉一切按键（Alt+Tab / Win 键等全部失效；
    Ctrl+Alt+Del 属于系统安全序列，任何用户态程序都拦不住，此处同样拦截不了）。"""
    def __init__(self, log):
        self.block = threading.Event()
        self._log = log
        self._thread = None

    def _proc(self, nCode, wParam, lParam):
        if nCode == 0 and self.block.is_set():
            return 1  # 吞掉
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _worker(self):
        proc = HOOKPROC(self._proc)          # 回调对象必须保持引用
        self._cb_ref = proc
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, None, 0)
        if not hook:
            self._log.write("[hook] SetWindowsHookExW 失败 err=%d（键盘未被屏蔽）" % kernel32.GetLastError())
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def set_blocked(self, on):
        if on:
            self.block.set()
        else:
            self.block.clear()

# ---------------------------------------------------------------- 单实例
def another_instance_running():
    kernel32.CreateMutexW(None, False, "Global\\PCGuardianMutex")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS

# ---------------------------------------------------------------- 轮询线程
class Poller(threading.Thread):
    def __init__(self, cfg, log, out, interval_fn):
        super().__init__(daemon=True)
        self.cfg, self.log, self.out = cfg, log, out
        self.interval_fn = interval_fn     # 锁定时快轮询，解锁后慢轮询
        self.stop_flag = threading.Event()

    def run(self):
        first = True
        while not self.stop_flag.is_set():
            if not first:
                try:
                    wait = max(2, int(self.interval_fn()))
                except Exception:
                    wait = self.cfg["poll_seconds"]
                self.stop_flag.wait(wait)
            first = False
            if self.stop_flag.is_set():
                break
            try:
                data = fetch_state(self.cfg["gist_url"])
                self.out.put(("data", data))
            except Exception as e:
                self.out.put(("net_error", repr(e)))

# ---------------------------------------------------------------- 主程序
class Guardian:
    MODE_LOCKED, MODE_CONGRATS, MODE_UNLOCKED, MODE_SHUTTING = "locked", "congrats", "unlocked", "shutting"

    def __init__(self):
        self.cfg = load_cfg()
        self.log = Log(self.cfg["log"])
        self.demo = "--demo" in sys.argv
        self.q = queue.Queue()
        self.blocker = KeyBlocker(self.log)
        self.mode = self.MODE_LOCKED
        self.required = [(tid, name) for tid, name in
                         zip(self.cfg["required_ids"], self.cfg["required_name_hints"])]
        self.done_map = {}          # tid -> bool（最近一次成功拉取）
        self.net_ok = None          # None=还没拉过 / True / False
        self.last_sync = None
        self.minutes = int(self.cfg["default_minutes"])
        self.unlock_start = None
        self.end_time = None        # epoch 秒
        self.shutdown_deadline = None
        self.shut_latch = False
        grace = int(self.cfg.get("expire_grace_seconds") or 7200)
        self.session_state, self.restored_end = load_session(grace)
        self.restored_expired = self.session_state == "expired"
        self.daily_done = self.session_state == "used_up"
        if self.session_state:
            self.log.write("[session] 当天会话状态=%s end=%s" % (self.session_state,
                           datetime.datetime.fromtimestamp(self.restored_end).strftime("%H:%M:%S") if self.restored_end else "-"))
        # 任务分派（奖励时间）
        self.spent = load_spent()
        self.wallet = []             # 可用奖励（最近一次成功拉取后计算）
        self.wallet_pick = {}        # tid -> tk.BooleanVar（界面勾选状态）
        self._wallet_sig = None      # 钱包内容签名，变化时才重建界面
        # AI 评分（PCVoiceScore）
        self.ai_doc = load_ai_score()
        self._ai_mtime = max((os.path.getmtime(p) for p in _aiscore_paths() if os.path.exists(p)), default=0)
        self.ai_tasks = []           # 锁定页显示的 AI 评分任务
        self._ai_sig = None
        self.all_done = False        # 必做任务是否全部完成（含 AI 通过的）→ 使用电脑的前提
        # 零花钱兑换电脑时长
        self.redeem_doc = load_redeem()
        self._redeem_sig = None
        self._redeem_msg = ""
        self._redeem_arm = None      # (分钟, 花费, 时间戳) 两步确认
        self.redeem_enabled = False
        self.redeem_cap = 10.0
        self.game_rate = 5.0
        self.money_cloud = 0.0
        self.scorer_proc = None      # （历史字段，评分已改为进程内嵌）
        self.scorer = None           # 内嵌评分面板（ScoreApp）
        self.scorer_active = False   # 评分进行中
        self.score_panel = None      # 覆盖锁定页的面板 Frame
        self._S = None               # 懒加载的 PCVoiceScore 模块
        self._last_data = None       # 最近一次云端数据（aiscore 变化时重放）
        self.reward_ids = None       # 非 None = 当前是奖励轮解锁（列表为恢复的奖励轮）
        self.reward_restore_end = None
        self.reward_expired = False
        rstate, rend = load_reward_session(grace)
        if rstate == "paused":
            self.log.write("[reward] 奖励轮会话状态=paused 剩余=%s（锁定页点「开始使用」继续）"
                           % self.fmt_mmss(rend))
        elif rstate == "restore":
            self.reward_restore_end = rend
        elif rstate == "expired":
            self.reward_expired = True
        if rstate and rstate != "paused":
            self.log.write("[reward] 奖励轮会话状态=%s end=%s" % (rstate,
                           datetime.datetime.fromtimestamp(rend).strftime("%H:%M:%S") if rend else "-"))
        self.data_ready = threading.Event()
        self._update_state = {"version": None, "files": {}}
        self._stop_up = threading.Event()
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()

    # ---------- 数据侧 ----------
    def apply_state(self, data):
        self._last_data = data
        cfg_all = data.get("config") or {}
        tasks = cfg_all.get("tasks") or []
        pc0 = cfg_all.get("computer") or {}
        names_by_id = {str(t.get("id")): (str(t.get("name") or t.get("id"))) for t in tasks
                       if isinstance(t, dict) and t.get("id")}
        req_cfg = pc0.get("required")
        if isinstance(req_cfg, list) and req_cfg:
            # 家长在「🖥️ 电脑任务」里勾选的必须完成任务（主任务列表里的 id）
            self.required = [(str(tid), names_by_id.get(str(tid), str(tid))) for tid in req_cfg
                             if str(tid) in names_by_id]
        else:
            self.required = []   # 家长未勾选必做任务 → 电脑时长不受必做限制
        recs = (data.get("records") or {}).get(today_key()) or {}
        self.done_map = {tid: bool((recs.get(tid) or {}).get("done")) for tid, _ in self.required}

        pc = cfg_all.get("computer") or {}
        try:
            self.minutes = max(1, int(pc.get("minutes") or self.cfg["default_minutes"]))
        except (TypeError, ValueError):
            self.minutes = self.cfg["default_minutes"]
        parent_unlock = bool(pc.get("unlock"))

        # 零花钱兑换配置（家长端「🖥️ 电脑时长」里设置；兑换价 = 游戏兑换价 gameRate）
        rc = pc.get("redeem") or {}
        self.redeem_enabled = bool(rc.get("enabled"))
        try:
            self.redeem_cap = min(500.0, max(0.5, float(rc.get("dailyCap") or 10)))
        except (TypeError, ValueError):
            self.redeem_cap = 10.0
        try:
            self.game_rate = max(0.5, float(cfg_all.get("gameRate") or 5))
        except (TypeError, ValueError):
            self.game_rate = 5.0
        self.money_cloud = cloud_balance(data)

        # 任务分派：完整列表（供锁定页展示）+ 可用奖励钱包（已完成且未使用）
        records_all = data.get("records") or {}
        disp = ((cfg_all.get("dispatch") or {}).get("tasks")) or []
        self.last_dispatch = disp
        self.dispatch_view = []
        self.ai_tasks = []
        for t in disp:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or "")
            if not tid:
                continue
            try:
                m = int(t.get("minutes") or 0)
            except (TypeError, ValueError):
                m = 0
            kind = t.get("kind") or ""
            if kind in ("read", "words"):
                # AI 评分任务：状态来自本机评分结果（今天）
                res = ((self.ai_doc.get("results") or {}).get(tid) or {}).get(today_key()) or {}
                try:
                    attempts = int(res.get("attempts") or 0)
                except (TypeError, ValueError):
                    attempts = 0
                try:
                    best = int(res.get("best") or 0)
                except (TypeError, ValueError):
                    best = 0
                try:
                    thr = int(t.get("threshold") or 80)
                except (TypeError, ValueError):
                    thr = 80
                try:
                    max_att = int(t.get("maxAttempts") or self.cfg.get("ai_max_attempts") or 5)
                except (TypeError, ValueError):
                    max_att = 5
                passed = bool(res.get("pass"))
                self.ai_tasks.append({"id": tid, "name": str(t.get("name") or tid), "minutes": m,
                                      "kind": kind, "threshold": thr, "attempts": attempts,
                                      "max_attempts": max_att, "best": best, "pass": passed,
                                      "poem": bool(t.get("poem")), "quiz": bool(t.get("quiz"))})
            else:
                self.dispatch_view.append((tid, str(t.get("name") or tid), m,
                                           latest_done(records_all, tid) is True, kind))
        self._grant_ai_rewards(disp)
        # 本地媒体文件夹：MD 文档并入 AI 任务列表（复用评分/奖励体系），音视频进入播放任务
        self.local_scan = self._scan_local_tasks()
        seen_ids = {t["id"] for t in self.ai_tasks}
        done_docs = {t for t, p in self._load_media_progress().items() if p.get("done")}
        for t in (self.local_scan.get("docs") or []):
            if t["id"] in seen_ids or t["id"] in done_docs:
                continue                    # 已完成任务（文件归档/进度标记）不再出现
            res = ((self.ai_doc.get("results") or {}).get(t["id"]) or {}).get(today_key()) or {}
            try:
                att = int(res.get("attempts") or 0)
                best = int(res.get("best") or 0)
            except (TypeError, ValueError):
                att = best = 0
            self.ai_tasks.append({"id": t["id"], "name": t["name"], "minutes": t["minutes"],
                                  "kind": t["kind"], "threshold": t["threshold"], "attempts": att,
                                  "max_attempts": int(self.cfg.get("ai_max_attempts") or 5),
                                  "best": best, "pass": bool(res.get("pass")),
                                  "poem": False, "quiz": False, "local": True,
                                  "content": t.get("content")})
        self.local_media = self.local_scan.get("media") or []
        self._grant_local_rewards()
        self._grant_web_earn()
        old_wallet_ids = [w["id"] for w in self.wallet]
        self.wallet = compute_wallet(data, self.spent)
        for r in (self.ai_doc.get("rewards") or []):     # 本地 AI 奖励合入钱包
            rid = str(r.get("id") or "")
            try:
                rm = int(r.get("minutes") or 0)
            except (TypeError, ValueError):
                rm = 0
            if rid and rm > 0 and rid not in self.spent and rid not in [w["id"] for w in self.wallet]:
                self.wallet.append({"id": rid, "name": str(r.get("name") or "🎤 奖励"), "minutes": rm})
        new_wallet_ids = [w["id"] for w in self.wallet]
        if new_wallet_ids != old_wallet_ids:
            self.log.write("[wallet] 可用奖励 %s" % ("、".join("%s(%d分钟)" % (w["name"], w["minutes"]) for w in self.wallet) or "无"))

        self.net_ok = True
        self.last_sync = time.time()
        self.data_ready.set()
        self.log.write("[poll] required=%s done=%s minutes=%s unlock=%s reward=%s" %
                       ([tid for tid, _ in self.required], self.done_map, self.minutes, parent_unlock,
                        "+".join(str(w["minutes"]) for w in self.wallet) or "-"))

        all_done = (len(self.required) == 0) or all(self.done_map.values())
        self.all_done = all_done

        if parent_unlock:
            self.shut_latch = False   # 家长通道可以救回一次 dry-run/失败后的关机
            self.reward_expired = False   # 家长接管：奖励轮的躲避关机惩罚一并豁免
            self.reward_restore_end = None
            if self.mode != self.MODE_UNLOCKED or self.end_time is not None:
                self.enter_unlock(parent=True)
            self.abort_shutdown("家长临时解锁")
            return

        if self.shut_latch:           # 已执行过关机，不再自动解锁（真实模式下电脑已断电）
            return

        if self.reward_expired:       # 奖励轮倒计时在程序中断期间走完：补执行（不依赖基础打卡）
            self.reward_expired = False
            self.log.write("[restore] 奖励轮倒计时已在程序中断期间结束，补执行锁定+关机")
            self.start_shutdown(auto=True)
            return

        if self.reward_restore_end is not None and self.mode == self.MODE_LOCKED:
            end = self.reward_restore_end   # 恢复被中断的奖励倒计时（选定时刻已扣除，不重不发）
            self.reward_restore_end = None
            self.enter_unlock(parent=False, restore_end=end, reward_kind=True)
            return

        if all_done:
            if self.mode == self.MODE_LOCKED:
                if self.restored_expired:      # 兼容升级前遗留的基础轮会话：躲避关机仍补执行
                    self.restored_expired = False
                    self.log.write("[restore] 上次倒计时已在程序中断期间结束，补执行锁定+关机")
                    self.start_shutdown(auto=True)
                elif self.restored_end:        # 兼容遗留：恢复未走完的基础轮倒计时
                    end = self.restored_end
                    self.restored_end = None
                    self.enter_unlock(parent=False, restore_end=end)
                # 电脑时长不再自动发放：完成必做任务后，用「任务分派/零花钱兑换」的时长（见锁定页）
            elif self.mode == self.MODE_UNLOCKED and self.end_time is None and self.reward_ids is None:
                # 家长临时解锁关闭 → 回到锁定（时长只来自任务分派/兑换）
                self.log.write("[lock] 家长临时解锁关闭，恢复锁定")
                self.enter_locked(reason="unlock_off")
            elif self.mode == self.MODE_UNLOCKED and self.unlock_start is not None and self.reward_ids is None:
                # 兼容遗留基础轮：家长调整时长 → 以本轮起点 + 新时长重算
                new_end = self.unlock_start + self.minutes * 60
                if self.end_time != new_end:
                    self.end_time = new_end
                    save_session(self.end_time, self.unlock_start)
                    self.log.write("[time] 家长调整时长为 %d 分钟" % self.minutes)
        else:
            if self.reward_ids is not None:
                return   # 奖励轮解锁期间不看基础打卡状态（奖励是已完成任务换来的）
            if self.mode in (self.MODE_UNLOCKED, self.MODE_CONGRATS):
                self.log.write("[lock] 检测到任务被取消打卡，重新锁定")
            self.enter_locked(reason="tasks")

    def on_net_error(self, err):
        self.net_ok = False
        self.data_ready.set()
        self.log.write("[poll] 拉取失败 %s（保持当前状态，下一轮重试）" % err)

    # ---------- 模式切换 ----------
    def _to_lock_ui(self):
        """切回锁定画面：显示主窗口、销毁倒计时悬浮窗、刷新界面。"""
        try:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            pass
        if self.hud is not None:
            try:
                self.hud.destroy()
            except tk.TclError:
                pass
            self.hud = None
        self.render()

    def enter_locked(self, reason):
        if self.mode == self.MODE_SHUTTING and reason == "tasks":
            return  # 正在关机流程中，不回退
        self._parent_unlock = False
        self._eye_abort()                 # 关闭护眼绿幕并复位
        self.mode = self.MODE_LOCKED
        self.end_time = None
        self.unlock_start = None
        self.reward_ids = None
        self.blocker.set_blocked(self._hook_on())
        if reason == "tasks":
            clear_session(keep_today=True)   # 任务未完成导致的锁定：只清非当天的旧会话
        if hasattr(self, "root"):
            self._to_lock_ui()

    def show_congrats(self):
        self.mode = self.MODE_CONGRATS
        self.congrats_until = time.time() + self.cfg["congrats_seconds"]
        self.blocker.set_blocked(self._hook_on())
        self._to_lock_ui()

    # ---- 护眼模式：每使用 N 分钟 → 全屏绿幕强制休息 M 秒（休息不扣使用时间） ----
    def _eye_abort(self):
        """销毁护眼绿幕并复位累计（锁定/家长接管/重新进入使用时调用）。"""
        ov = getattr(self, "eye_overlay", None)
        self.eye_overlay = None
        self.eye_until = None
        self.eye_base = None
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass

    def _eye_tick(self, now):
        if self._parent_unlock:          # 家长临时解锁不做护眼打断
            return
        mins = int(self.cfg.get("eye_break_minutes") or 0)
        secs = int(self.cfg.get("eye_break_seconds") or 0)
        if mins <= 0 or secs <= 0:
            return
        if self.eye_until is not None:
            # 休息中：使用倒计时冻结（把休息耗时不计入），绿幕显示剩余
            if self.end_time is not None:
                self.end_time += now - (self._eye_last or now)
            self._eye_last = now
            left = int(self.eye_until - now)
            if left <= 0:
                self._eye_end()
            return
        if self.eye_base is None:
            self.eye_base = now
        if now - self.eye_base >= mins * 60:
            self._eye_start(secs)

    def _eye_start(self, secs):
        self.eye_until = time.time() + secs
        self._eye_last = time.time()
        ov = tk.Toplevel(self.root)
        ov.configure(bg="#1a8f3a")
        ov.geometry("%dx%d+0+0" % (ov.winfo_screenwidth(), ov.winfo_screenheight()))
        ov.attributes("-topmost", True)
        ov.overrideredirect(True)
        tk.Label(ov, text="🐼 护眼休息时间", font=("Microsoft YaHei UI", 42, "bold"),
                 bg="#1a8f3a", fg="white").pack(expand=True)
        tip = tk.Label(ov, text="", font=("Microsoft YaHei UI", 20),
                       bg="#1a8f3a", fg="#d9ffe3")
        tip.pack(pady=(0, int(ov.winfo_screenheight() * 0.12)))
        self.eye_overlay = ov
        self.log.write("[eye] 护眼休息开始（%d 秒），使用倒计时已冻结" % secs)

        def _tick():
            if self.eye_overlay is not ov or self.eye_until is None:
                return
            left = max(0, int(self.eye_until - time.time()))
            tip.configure(text="⏳ %d:%02d 后继续使用 · 看看远处，让眼睛休息一下"
                          % (left // 60, left % 60))
            ov.after(500, _tick)
        _tick()

    def _eye_end(self):
        ov = getattr(self, "eye_overlay", None)
        self.eye_overlay = None
        self.eye_until = None
        self.eye_base = time.time()      # 重新累计下一个 35 分钟
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass
        self.log.write("[eye] 护眼休息结束，继续计时")

    def enter_unlock(self, parent=False, restore_end=None, reward=None, reward_kind=False):
        """reward = (秒数, [奖励id])：奖励轮解锁，时长固定为勾选合计；
           reward_kind=True：恢复的是奖励轮倒计时（原 id 已在台账扣除，不重不发）。"""
        self._parent_unlock = bool(parent)
        self._eye_abort()                 # （重新）进入使用：复位护眼累计/关闭绿幕
        self.mode = self.MODE_UNLOCKED
        self.unlock_start = time.time()
        if parent:
            self.end_time = None
            self.reward_ids = None
            clear_reward_session()          # 家长接管：本轮奖励作废（选定即扣除，不退回）
        elif reward is not None:
            secs, ids = reward
            self.end_time = self.unlock_start + max(60, int(secs))
            self.reward_ids = list(ids)
            save_reward_session(self.end_time, self.unlock_start)
        elif restore_end:
            self.end_time = restore_end     # 恢复被中断的倒计时（防杀进程刷时间）
            self.reward_ids = [] if reward_kind else None
            if not reward_kind:
                save_session(self.end_time, self.unlock_start)
            self.log.write("[unlock] 恢复上次的倒计时，剩余 %s" %
                           self.fmt_mmss(self.end_time - time.time()))
        else:
            self.end_time = self.unlock_start + self.minutes * 60
            self.reward_ids = None
            save_session(self.end_time, self.unlock_start)
        self.blocker.set_blocked(False)
        kind = "（家长临时解锁）" if parent else ("（奖励轮，剩余 %s）" % self.fmt_mmss(self.end_time - time.time())
                                                if self.reward_ids is not None else
                                                "（剩余 %s）" % ("—" if self.end_time is None else self.fmt_mmss(self.end_time - time.time())))
        self.log.write("[unlock] 解锁 %s" % kind)
        self.show_unlock()

    def finish_timeout(self):
        """倒计时到点：还有可用奖励 → 回到锁定页由孩子继续选择（不关机）；
           没有奖励 → 维持原有行为：锁定 → 10 秒提示 → 强制关机。"""
        reward_round = self.reward_ids is not None
        base_end, base_start = self.end_time, self.unlock_start
        self.enter_locked(reason="timeout")
        if self.wallet:
            if reward_round:
                clear_reward_session()   # 奖励轮自然结束：会话闭环，重启不补关机
            else:
                save_session(base_end, base_start, closed=True)   # 基础轮优雅闭环：占用当天机会但不补关机
                self.daily_done = True   # 内存同步标记：当天基础机会已用完，不再自动发新轮
            self.log.write("[timeout] 时间到，还有可用奖励，回到选择界面（不关机）")
            return
        self.start_shutdown(auto=True)

    def start_shutdown(self, auto=True):
        if self.mode == self.MODE_SHUTTING:
            return
        self.mode = self.MODE_SHUTTING
        self.blocker.set_blocked(self._hook_on())
        self.shutdown_deadline = time.time() + self.cfg["shutdown_seconds"]
        self.log.write("[shutdown] %s关机流程启动（%d 秒后执行）" %
                       ("倒计时结束，自动" if auto else "手动点击了", self.cfg["shutdown_seconds"]))
        self._to_lock_ui()

    def abort_shutdown(self, who):
        if self.mode == self.MODE_SHUTTING:
            self.log.write("[shutdown] %s，取消关机" % who)
            self.shutdown_deadline = None
            self.mode = self.MODE_LOCKED

    def do_shutdown(self):
        self.log.write("[shutdown] 执行关机命令")
        self.shut_latch = True
        if self.cfg["dry_run"]:
            self.log.write("[shutdown] dry_run=True，跳过真实关机，回到锁定状态")
            self.shutdown_deadline = None
            self.enter_locked(reason="dryrun")
            return
        try:
            subprocess.Popen(["shutdown", "/s", "/f", "/t", "3"])
        except Exception as e:
            self.log.write("[shutdown] 关机命令失败: %s" % e)

    # ---------- 轮询调度 ----------
    def poll_forever_ui(self):
        # 处理后台轮询结果
        try:
            # AI 评分结果热加载（评分程序写盘后 0.5 秒内生效）+ 子进程退出检测
            if self._reload_ai_score() and self._last_data is not None:
                try:
                    self.apply_state(self._last_data)
                except Exception as e:
                    self.log.write("[ai] 重放数据异常 %r" % e)
            try:
                while True:
                    kind, payload = self.q.get_nowait()
                    if kind == "data":
                        try:
                            self.apply_state(payload)
                        except Exception as e:
                            self.log.write("[apply] 处理数据异常: %r" % e)
                    elif kind == "net_error":
                        self.on_net_error(payload)
            except queue.Empty:
                pass

            if self.mode == self.MODE_LOCKED:
                self.tick_lock()
            elif self.mode == self.MODE_CONGRATS:
                if time.time() >= self.congrats_until:
                    self.enter_unlock(parent=False)
            elif self.mode == self.MODE_UNLOCKED:
                # 超时判断不依赖悬浮窗（浮窗被关掉也照常处理）
                if self.end_time is not None and time.time() >= self.end_time:
                    self.finish_timeout()
                else:
                    self.tick_unlock()
            elif self.mode == self.MODE_SHUTTING:
                self.tick_shutdown()
        except Exception:
            import traceback
            self.log.write("[tick] 界面循环异常(已继续):\n" + traceback.format_exc(limit=3))

        self.root.after(500, self.poll_forever_ui)

    # ---------- UI ----------
    def build_ui(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg="#10141f")

        H = self.root.winfo_screenheight()
        self.big = max(18, int(H / 26)); self.mid = max(14, int(H / 42)); self.sml = max(11, int(H / 62))

        self.root.attributes("-topmost", True)
        self.root.attributes("-fullscreen", True)   # 锁定时无边框真全屏，隐藏任务栏
        if self.demo or self.cfg["dry_run"]:
            # 测试模式（--demo 或配置 dry_run）：Esc 可随时退出，其余与正式行为一致
            self.root.bind("<Escape>", lambda e: self.root.destroy())
            self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        else:
            self.root.protocol("WM_DELETE_WINDOW", lambda: None)  # 正式锁定：不许关

        self.lock_frame = tk.Frame(self.root, bg="#10141f")
        self.lock_frame.place(x=0, y=0, relwidth=1, relheight=1)

        # ---- 顶部：零花钱总数 / 今日可兑换 ----
        self.money_bar = tk.Frame(self.lock_frame, bg="#0d1119")
        self.money_bar.pack(fill="x", padx=14, pady=(10, 4))
        self.lb_money_total = tk.Label(self.money_bar, text="💰 零花钱总数 ¥--",
                                       font=("Microsoft YaHei UI", self.mid, "bold"),
                                       bg="#0d1119", fg="#ffe08a")
        self.lb_money_total.pack(side="left", padx=14, pady=6)
        self.lb_money_today = tk.Label(self.money_bar, text="今日可兑换 --",
                                       font=("Microsoft YaHei UI", self.sml),
                                       bg="#0d1119", fg="#8fa0bd")
        self.lb_money_today.pack(side="right", padx=14)

        # ---- 中部：锁芯 + 提示 + 可兑换时间分布 + 开始使用（居中） ----
        self.center_frame = tk.Frame(self.lock_frame, bg="#10141f")
        self.center_frame.pack(fill="x", pady=(2, 2))
        self.lb_lock = tk.Label(self.center_frame, text="🔒", font=("Segoe UI Emoji", int(self.big * 1.6)), bg="#10141f", fg="#fff")
        self.lb_lock.pack(pady=(6, 2))
        self.lb_title = tk.Label(self.center_frame, text="正在检查今日任务…", font=("Microsoft YaHei UI", self.big, "bold"), bg="#10141f", fg="#ffe08a", wraplength=1600, justify="center")
        self.lb_title.pack()
        self.redeem_frame = tk.Frame(self.center_frame, bg="#10141f")   # 可兑换时间分布（周末且开启时显示）
        # 开始使用：页面正中间，按钮在上、提示语在按钮下方
        self.use_bar = tk.Frame(self.center_frame, bg="#10141f")
        self.use_bar.pack(fill="x", pady=(8, 2))
        self.btn_use = tk.Button(self.use_bar, text="▶ 开始使用", font=("Microsoft YaHei UI", self.mid, "bold"),
                                 bg="#2e7d32", fg="white", activebackground="#256a29", activeforeground="white",
                                 relief="flat", cursor="hand2", padx=28, pady=8, state="disabled",
                                 disabledforeground="#9fb3a1", command=self.start_reward_use)
        self.btn_use.pack(anchor="center")
        self.lb_use = tk.Label(self.use_bar,
                               text="完成必做任务后，勾选已完成的任务开始使用电脑，支持多选，时间会自动累积",
                               font=("Microsoft YaHei UI", self.sml), bg="#10141f", fg="#8fa0bd",
                               wraplength=1600, justify="center")
        self.lb_use.pack(anchor="center", pady=(6, 0))

        # ---- 底部一半：左=已完成任务（可勾选），右=待完成任务（含开始任务按钮），均带滚动条 ----
        self.panes = tk.Frame(self.lock_frame, bg="#10141f")
        self.panes.pack(fill="both", expand=True, padx=14, pady=(2, 6))
        left = tk.Frame(self.panes, bg="#141b2b")
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        right = tk.Frame(self.panes, bg="#141b2b")
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.lb_done_head = tk.Label(left, text="✅ 已完成的任务（勾选后点上方「开始使用」）",
                                     font=("Microsoft YaHei UI", self.sml, "bold"), bg="#141b2b", fg="#b9f6c8", anchor="w")
        self.lb_done_head.pack(fill="x", padx=10, pady=(8, 2))
        self.done_canvas, self.done_sb, self.done_inner = self._make_scroll_pane(left)
        todo_head = tk.Frame(right, bg="#141b2b")
        todo_head.pack(fill="x", padx=10, pady=(8, 2))
        self.lb_todo_head = tk.Label(todo_head, text="📋 待完成的任务",
                                     font=("Microsoft YaHei UI", self.sml, "bold"), bg="#141b2b", fg="#ffd166", anchor="w")
        self.lb_todo_head.pack(side="left")
        self.todo_tab_bar = tk.Frame(todo_head, bg="#141b2b")
        self.todo_tab_bar.pack(side="right")
        self.todo_canvas, self.todo_sb, self.todo_inner = self._make_scroll_pane(right)

        # 滚轮全局分发：指针下的控件向上找所属 canvas（两个滚动栏共用，不再用 Enter/Leave 解绑）
        wheel_canvases = [self.done_canvas, self.todo_canvas]
        def _global_wheel(e):
            w = getattr(e, "widget", None)
            while w is not None:
                if isinstance(w, tk.Canvas) and w in wheel_canvases:
                    first, last = w.yview()
                    if first <= 0 and last >= 1:
                        return
                    w.yview_scroll(-1 * int(max(1, abs(e.delta) / 120)), "units")
                    return
                w = getattr(w, "master", None)
        self.root.bind_all("<MouseWheel>", _global_wheel, add="+")

        self.lb_net = tk.Label(self.lock_frame, text="", font=("Microsoft YaHei UI", self.sml), bg="#10141f", fg="#7f8aa3")
        self.lb_net.pack(pady=(0, 0))
        self.lb_clock = tk.Label(self.lock_frame, text="", font=("Microsoft YaHei UI", self.sml), bg="#10141f", fg="#5a6478")
        self.btn_mic = tk.Button(self.lock_frame, text="🎤 试音", font=("Microsoft YaHei UI", 10),
                                 bg="#1f2a3a", fg="#8fa0bd", activebackground="#2a3a50",
                                 activeforeground="#c8d4e8", relief="flat", cursor="hand2",
                                 padx=10, pady=2, command=self._open_mic_test)
        self.btn_mic.pack(side="bottom", pady=(0, 6))
        self.lb_clock.pack(side="bottom", pady=6)

        self.btn_off = tk.Button(self.lock_frame, text="⏻  关 机", font=("Microsoft YaHei UI", max(14, int(H / 46)), "bold"),
                                 bg="#e5484d", fg="white", activebackground="#c5363b", activeforeground="white",
                                 relief="flat", cursor="hand2", width=14, pady=4, command=self.start_shutdown)
        self.btn_off.pack(side="bottom", pady=2)

        # 解锁后的右上角倒计时悬浮窗
        self.hud = None
        # 护眼模式状态：累计窗口起点 / 全屏绿幕 / 休息截止 / 上次心跳
        self.eye_base = None
        self.eye_overlay = None
        self.eye_until = None
        self._eye_last = None
        self._parent_unlock = False

        self.root.bind("<Configure>", self._on_cfg)
        self._apply_fonts()
        self.root.after(50, self._keep_on_top)

    @staticmethod
    def _make_scroll_pane(parent):
        """带竖向滚动条的内容区（滚轮滚动），返回 (canvas, scrollbar, inner_frame)。"""
        canvas = tk.Canvas(parent, bg="#141b2b", highlightthickness=0)
        sb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview, width=14)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#141b2b")
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        # 滚轮由 build_ui 里的全局分发器处理（指针落在行控件上时 canvas 的 <Leave>
        # 会解绑 bind_all，导致列表填满后滚轮失效——tkinter 经典坑）
        return canvas, sb, inner

    def _on_cfg(self, event):
        """窗口尺寸变化 → 防抖后按新尺寸重算全部字体。"""
        if event.widget is not self.root or not hasattr(self, "lb_title"):
            return
        if getattr(self, "_cfg_after", None):
            try:
                self.root.after_cancel(self._cfg_after)
            except Exception:
                pass
        self._cfg_after = self.root.after(150, self._apply_fonts)

    def _apply_fonts(self):
        self._cfg_after = None
        h = self.root.winfo_height()
        w = self.root.winfo_width()
        if h < 80 or w < 80:      # 首帧未布局 → 用整屏
            h, w = self.root.winfo_screenheight(), self.root.winfo_screenwidth()
        h, w = max(320, h), max(480, w)
        self.big = max(14, int(h / 22))
        self.mid = max(12, int(h / 36))
        self.sml = max(10, int(h / 58))
        try:
            self.lb_lock.configure(font=("Segoe UI Emoji", int(self.big * 1.6)))
            self.lb_title.configure(font=("Microsoft YaHei UI", self.big, "bold"), wraplength=int(w * 0.9))
            self.lb_money_total.configure(font=("Microsoft YaHei UI", self.mid, "bold"))
            self.lb_money_today.configure(font=("Microsoft YaHei UI", self.sml))
            self.lb_net.configure(font=("Microsoft YaHei UI", self.sml))
            self.lb_clock.configure(font=("Microsoft YaHei UI", self.sml))
            self.btn_off.configure(font=("Microsoft YaHei UI", max(12, int(h / 46)), "bold"))
            self.btn_use.configure(font=("Microsoft YaHei UI", self.mid, "bold"))
            self.lb_use.configure(font=("Microsoft YaHei UI", self.sml))
            self.lb_done_head.configure(font=("Microsoft YaHei UI", self.sml, "bold"))
            self.lb_todo_head.configure(font=("Microsoft YaHei UI", self.sml, "bold"))
            self._wallet_sig = None    # 行控件按新字体重建
            self._todo_sig = None
            self._ai_sig = None
            self._redeem_sig = None
            if hasattr(self, "render"):
                self.render()
        except tk.TclError:
            pass

    def _keep_on_top(self):
        if self.mode in (self.MODE_LOCKED, self.MODE_CONGRATS, self.MODE_SHUTTING) and not self.demo:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.attributes("-fullscreen", True)  # 防止全屏状态被其他程序打断
            self.root.lift()
            try:
                self.root.focus_force()
            except tk.TclError:
                pass
        if self.hud is not None:
            try:
                self.hud.attributes("-topmost", True)
                self.hud.lift()
            except tk.TclError:
                pass
        self.root.after(800, self._keep_on_top)

    def fmt_mmss(self, secs):
        secs = max(0, int(secs))
        return "%02d:%02d" % (secs // 60, secs % 60)

    # ---- 锁定界面 ----
    def tick_lock(self):
        # 兑换两步确认 6 秒未点第二次 → 自动取消
        if self._redeem_arm is not None and time.time() - self._redeem_arm[2] > 6:
            self._redeem_arm = None
            self._redeem_sig = None
        self.render()

    def render(self):
        f = self.lock_frame
        if self.mode == self.MODE_CONGRATS:
            f.configure(bg="#123d1f"); self.lb_title.configure(fg="#b9f6c8")
            self.lb_title.configure(text="🎉 恭喜你！电脑使用时间开始计时 🎉")
            self._hide_lock_sections()
            self.lb_net.configure(text="")
        elif self.mode == self.MODE_SHUTTING:
            left = self.shutdown_deadline - time.time() if self.shutdown_deadline else 0
            f.configure(bg="#3d1215"); self.lb_title.configure(fg="#ffb3b6")
            self.lb_title.configure(text="⏳ 使用时间结束，%d 秒后自动关机" % max(1, int(left) + 1))
            self._hide_lock_sections()
            self.btn_off.configure(text="⏻ 关机中…", state="disabled", bg="#8f2a2e")
        else:
            f.configure(bg="#10141f"); self.lb_title.configure(fg="#ffe08a")
            if getattr(self, "scorer_active", False):
                self._hide_lock_sections()   # 内嵌评分面板覆盖中：锁定页各区块收起
            wallet = list(self.wallet)
            all_done = bool(getattr(self, "all_done", False))
            # 顶部：零花钱总数 / 今日可兑换
            bal = self._money_left()
            if self._redeem_open():
                cap_left = max(0.0, round(self.redeem_cap - self._redeem_spent_today(), 2))
                self.lb_money_today.configure(text="今日可兑换 ¥%.2f（¥%.2f/小时）" % (cap_left, self.game_rate),
                                              fg="#8fa0bd")
            elif self.redeem_enabled:
                self.lb_money_today.configure(text="零花钱兑换 · 周末开放", fg="#5a6478")
            else:
                self.lb_money_today.configure(text="兑换未开放", fg="#5a6478")
            self.lb_money_total.configure(text="💰 零花钱总数 ¥%.2f" % bal)
            # 中部标题
            if not self.data_ready.is_set():
                self.lb_title.configure(text="正在检查今日任务…")
            elif self.net_ok is False:
                self.lb_title.configure(text="☁️ 网络连接失败，保持锁定（每 10 秒自动重试）")
            elif all_done:
                self.lb_title.configure(text=("✅ 今日无必做任务 · 勾选奖励时间开始使用"
                                              if not self.required else
                                              "✅ 今日必做任务已完成 · 勾选奖励时间开始使用"))
            else:
                self.lb_title.configure(text="📖 请先完成必做任务，再使用电脑")
            # 中部：先恢复 use_bar（恭喜/关机画面会隐藏它），再放兑换分布
            self.use_bar.pack(fill="x", pady=(8, 2))
            if self._redeem_open() and self.data_ready.is_set() and self.net_ok is not False:
                self._render_redeem()
                try:
                    self.redeem_frame.pack(before=self.use_bar, pady=(4, 0))
                except tk.TclError:
                    self.redeem_frame.pack(fill="x", pady=(4, 0))
            else:
                self.redeem_frame.pack_forget()
            # 已完成栏标题带总计分钟
            self.lb_done_head.configure(text="✅ 已完成的任务 · 共 %d 分钟"
                                        % sum(w["minutes"] for w in self.wallet))
            # 双栏
            self.panes.pack(fill="both", expand=True, padx=14, pady=(2, 6))
            self.use_bar.pack(fill="x", padx=14, pady=(4, 2))
            self._render_done_pane()
            self._render_todo_pane()
            self._update_use_btn()
            self.btn_off.configure(text="⏻  关 机", state="normal", bg="#e5484d")

        if self.net_ok and self.last_sync:
            self.lb_net.configure(text="☁️ 数据更新于 " + datetime.datetime.fromtimestamp(self.last_sync).strftime("%H:%M:%S") + " · 每 5 秒自动检查打卡")
        elif self.net_ok is False and self.last_sync:
            self.lb_net.configure(text="☁️ 上次成功更新 " + datetime.datetime.fromtimestamp(self.last_sync).strftime("%H:%M:%S") + " · 正在重试")
        else:
            self.lb_net.configure(text="")
        self.lb_clock.configure(text=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def _hide_lock_sections(self):
        for w in (self.use_bar, self.panes):
            try:
                w.pack_forget()
            except tk.TclError:
                pass
        try:
            self.redeem_frame.pack_forget()
        except tk.TclError:
            pass

    # ---- 左栏：已完成的任务（可勾选，多选累加） ----
    def _render_done_pane(self):
        sig = [(w["id"], w["name"], w["minutes"]) for w in self.wallet]
        if sig == self._wallet_sig:
            return
        self._wallet_sig = sig
        prev_pick = {k: bool(v.get()) for k, v in self.wallet_pick.items()}
        for c in self.done_inner.winfo_children():
            c.destroy()
        self.wallet_pick = {}
        if not self.wallet:
            tk.Label(self.done_inner, text="（还没有已完成的任务奖励\n等右侧任务完成/兑换后出现在这里）",
                     font=("Microsoft YaHei UI", self.sml), bg="#141b2b", fg="#5a6478",
                     justify="left").pack(anchor="w", padx=12, pady=10)
            return
        for w in self.wallet:
            var = tk.BooleanVar(value=prev_pick.get(w["id"], False))
            self.wallet_pick[w["id"]] = var
            tk.Checkbutton(self.done_inner, variable=var,
                           text="%d 分钟 · %s" % (w["minutes"], w["name"]),
                           font=("Microsoft YaHei UI", self.sml), bg="#1a2338", fg="#e8ecf5",
                           activebackground="#1a2338", activeforeground="#e8ecf5",
                           selectcolor="#233047", anchor="w", cursor="hand2",
                           command=self._update_use_btn).pack(fill="x", padx=8, pady=2)
        self._update_use_btn()

    def _update_use_btn(self):
        if not hasattr(self, "btn_use"):
            return
        # 有未用完的奖励轮会话 → 优先显示「继续上次使用」（已支付的时间不被必做门禁挡住）
        try:
            rstate, rend = load_reward_session()
        except Exception:
            rstate, rend = None, None
        resume_min = 0
        if rstate == "paused" and rend and rend > 60:
            resume_min = int(rend // 60)           # rend = 剩余秒数（暂停语义）
        elif rstate == "restore" and rend and rend - time.time() > 60:
            resume_min = int((rend - time.time()) // 60)
        if resume_min > 0:
            self.btn_use.configure(text="▶ 继续上次使用（剩余 %d 分钟）" % resume_min, state="normal")
            self.lb_use.configure(text="上次未用完的时间还在，点击继续（不会重复扣除）")
            return
        cap = int(self.cfg.get("reward_max_minutes") or 480)
        tip = "完成必做任务后，勾选已完成的任务开始使用电脑，支持多选，时间会自动累积"
        total = sum(w["minutes"] for w in self.wallet
                    if self.wallet_pick.get(w["id"]) is not None and self.wallet_pick[w["id"]].get())
        if not getattr(self, "all_done", False):
            self.btn_use.configure(text="▶ 开始使用", state="disabled")
            self.lb_use.configure(text="先完成今日必做任务（阅读/背单词）才能使用")
        elif total <= 0:
            self.btn_use.configure(text="▶ 开始使用", state="disabled")
            self.lb_use.configure(text=tip)
        elif total > cap:
            self.btn_use.configure(text="▶ 开始使用", state="disabled")
            self.lb_use.configure(text="共 %d 分钟（单次最多 %d 分钟）" % (total, cap))
        else:
            self.btn_use.configure(text="▶ 开始使用（共 %d 分钟）" % total, state="normal")
            self.lb_use.configure(text=tip)

    def start_reward_use(self):
        # 优先恢复上次暂停/中断的奖励轮（剩余时间已在台账扣除，不重复扣）
        try:
            rstate, rend = load_reward_session()
        except Exception:
            rstate, rend = None, None
        if rstate == "paused" and rend and rend > 60:
            # 暂停语义：rend 是「剩余秒数」，从当前时间重新起算（暂停期间不计时）
            self.log.write("[reward] 继续上次未用完的时间（剩余 %s）" % self.fmt_mmss(rend))
            clear_reward_session()
            self.enter_unlock(restore_end=time.time() + rend, reward_kind=True)
            return
        if rstate == "restore" and rend and rend - time.time() > 60:
            self.log.write("[reward] 继续上次未用完的时间（剩余 %s）"
                           % self.fmt_mmss(rend - time.time()))
            self.enter_unlock(restore_end=rend, reward_kind=True)
            return
        if not getattr(self, "all_done", False):
            self.log.write("[reward] 拒绝使用：今日必做任务未完成")
            return
        picks = [(w["id"], w["name"], w["minutes"]) for w in self.wallet
                 if self.wallet_pick.get(w["id"]) is not None and self.wallet_pick[w["id"]].get()]
        if not picks:
            return
        cap = int(self.cfg.get("reward_max_minutes") or 480)
        total = sum(m for _, _, m in picks)
        if total <= 0 or total > cap:
            self.log.write("[reward] 拒绝使用：合计 %d 分钟（上限 %d）" % (total, cap))
            return
        # 选定即扣除：立刻写入本机台账（防中途杀进程/断电后重复领取）
        for tid, _, m in picks:
            self.spent[tid] = {"date": today_key(), "minutes": m}
        save_spent(self.spent)
        self.log.write("[reward] 使用奖励 %s，合计 %d 分钟（已计入台账）" %
                       ("、".join("%s(%d分钟)" % (n, m) for _, n, m in picks), total))
        used = set(p[0] for p in picks)
        self.wallet = [w for w in self.wallet if w["id"] not in used]
        self._wallet_sig = None   # 强制下次重建
        self.enter_unlock(parent=False, reward=(total * 60, sorted(used)))

    # ---- AI 评分任务（PCVoiceScore 联动） ----
    def _pause_reward_use(self):
        """⏸ 暂停奖励轮：剩余时间保留在会话文件里并锁定屏幕；
        之后点「开始使用」从剩余继续（支持重启、跨天），不重复扣台账。
        同时收起媒体面板（媒体暂停、进度保留）——否则面板盖住锁定页无法操作。"""
        if self.mode != self.MODE_UNLOCKED or self.reward_ids is None or self.end_time is None:
            return
        remaining = max(60, int(self.end_time - time.time()))
        self._close_media_panel()
        save_reward_session(self.end_time, self.unlock_start, remaining=remaining)
        self.log.write("[reward] 暂停：剩余 %s 已保留（暂停期间不计时），锁定页点「开始使用」继续"
                       % self.fmt_mmss(remaining))
        self.enter_locked(reason="pause")

    def _end_reward_use(self):
        """⏹ 提前结束奖励轮：剩余时间（整分钟）退回钱包并锁定；媒体面板同步收起。"""
        if self.mode != self.MODE_UNLOCKED or self.reward_ids is None or self.end_time is None:
            return
        rem_s = int(max(0, self.end_time - time.time()))
        rem_min = -(-rem_s // 60)            # 向上取整：剩 8:59 也退 9 分钟，一分不丢
        self._close_media_panel()
        clear_reward_session()
        if rem_min > 0:
            rid = "resume-%d" % int(time.time())
            self.ai_doc.setdefault("rewards", []).append({
                "id": "ai-%s" % rid, "task": rid, "name": "⏸ 上次剩余时间",
                "minutes": rem_min, "grantedAt": time.time()})
            save_ai_score(self.ai_doc)
        self.log.write("[reward] 提前结束：剩余 %d 分钟退回钱包" % rem_min)
        self.enter_locked(reason="pause")

    def _media_duration_ms(self, path, kind):
        """媒体总时长（毫秒）：视频用 cv2；音频先试 wave 头再用 ffmpeg 信息行。失败返回 0。"""
        if kind == "watch":
            return _video_duration_ms(path)
        try:
            import wave as _w
            with _w.open(path, "rb") as w:
                return int(w.getnframes() / w.getframerate() * 1000)
        except Exception:
            pass
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return 0
        try:
            r = subprocess.run([ff, "-i", path], capture_output=True, timeout=60)
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)",
                          r.stderr.decode("utf-8", "ignore"))
            if m:
                h, mi, s, ms = m.groups()
                return ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(ms) * 10
        except Exception:
            pass
        return 0

    def _grant_local_rewards(self):
        """本地任务完成 → 奖励入钱包（每任务只发一次）：MD 文档评分通过 / 音视频播放完成。
        完成后源文件自动归档到 media\\已完成\\（不再作为任务出现）；归档失败则用进度标记隐藏。"""
        granted = {r.get("task") for r in (self.ai_doc.get("rewards") or [])}
        prog = self._load_media_progress()
        pend = []
        for t in (getattr(self, "local_scan", {}).get("docs") or []):
            res = ((self.ai_doc.get("results") or {}).get(t["id"]) or {}).get(today_key()) or {}
            if res.get("pass") and t["id"] not in granted:
                pend.append(("🎤 " + t["name"], t["id"], t["minutes"], t.get("path")))
        for mt in (getattr(self, "local_scan", {}).get("media") or []):
            if prog.get(mt["id"], {}).get("done") and mt["id"] not in granted:
                pend.append((("🎧 " if mt["kind"] == "listen" else "📺 ") + mt["name"],
                             mt["id"], mt["minutes"], mt.get("path")))
        changed = False
        for name, tid, m, path in pend:
            if m <= 0:
                continue
            self.ai_doc.setdefault("rewards", []).append({
                "id": "ai-%s-%d" % (tid, int(time.time())), "task": tid,
                "name": name, "minutes": m, "grantedAt": time.time()})
            changed = True
            prog[tid] = {"done": True, "name": name, "archived": bool(path)}
            self._save_media_progress(prog)
            if path:
                self._archive_completed_file(path)
            self.log.write("[media] 「%s」完成，发放奖励 %d 分钟入钱包" % (name, m))
        if changed:
            save_ai_score(self.ai_doc)

    def _archive_completed_file(self, path):
        """完成的本地任务文件移入 media\\已完成\\（保持相对路径，重名加时间戳）；
        失败（文件被占用等）返回 False——进度标记仍会隐藏该任务。"""
        try:
            mdir = self._media_dir()
            rel = os.path.relpath(path, mdir)
            dest = os.path.join(mdir, "已完成", rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.exists(dest):
                base, ext = os.path.splitext(dest)
                dest = "%s_%d%s" % (base, int(time.time()), ext)
            os.rename(path, dest)
            self.log.write("[media] 已完成任务文件归档：%s" % dest)
            return True
        except Exception as e:
            self.log.write("[media] 归档失败（进度标记仍会隐藏任务）%r" % e)
            return False

    def _grant_web_earn(self, doc=None):
        """网页版赚时间（CF Worker）结果 → 奖励入钱包（按结果 ID 去重，单条上限 120 分钟）。"""
        we = self.cfg.get("web_earn") or {}
        url = str(self.cfg.get("web_earn_url") or (we.get("url") if isinstance(we, dict) else "") or "").rstrip("/")
        if not url:
            return
        if doc is None:
            now = time.time()
            if now < getattr(self, "_web_next_try", 0):
                return                          # 拉取失败后的 60 秒退避
            self._web_next_try = now + 60
            try:
                req = urllib.request.Request(url + "/api/results",
                                             headers={"User-Agent": "PCGuardian"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    doc = json.loads(r.read().decode("utf-8"))
            except Exception as e:
                self.log.write("[web] 成绩拉取失败 %r" % e)
                return
        granted = {r.get("task") for r in (self.ai_doc.get("rewards") or [])}
        changed = False
        for r in (doc.get("results") or []):
            rid = "web-" + str(r.get("id") or "")
            if not r.get("minutes") or rid in granted:
                continue
            m = min(120, max(1, int(r.get("minutes") or 0)))
            self.ai_doc.setdefault("rewards", []).append({
                "id": "ai-%s-%d" % (rid, int(time.time())), "task": rid,
                "name": "🌐 " + str(r.get("name") or "网页任务"), "minutes": m,
                "grantedAt": time.time()})
            granted.add(rid)
            changed = True
            self.log.write("[web] 网页任务「%s」完成，发放 %d 分钟入钱包" % (r.get("name"), m))
        if changed:
            save_ai_score(self.ai_doc)

    def _media_dir(self):
        return str(self.cfg.get("media_dir") or os.path.join(script_dir(), "media"))

    def _scan_local_tasks(self):
        """扫描本地媒体文件夹——按类型分文件夹存放（递归，子文件夹 = 合集）：
           media\\阅读\\  .md 朗读材料（AI 评分）
           media\\单词\\  .md 单词表（AI 评分）
           media\\视频\\  MP4/AVI/MKV/MOV/WEBM/WMV
           media\\音频\\  MP3/WAV/M4A/AAC/FLAC/OGG/WMA
           media\\已完成\\ 归档（不扫描）
        类型文件夹内类型以文件夹为准（阅读夹里的文件一律按朗读处理）；
        根目录散落文件 / 未知子文件夹按扩展名归类（兼容旧结构）。"""
        out = {"docs": [], "media": []}
        KMIN = {"read": 15, "words": 10, "watch": 15, "listen": 10}

        def add_doc(p, kind, coll):
            if os.path.basename(p).lower() == "readme.md":
                return
            t = parse_local_md(p)
            if not t:
                return
            t["kind"] = kind                    # 文件夹决定类型
            t["id"] = _local_id("md_", p)
            t["threshold"] = 80
            t["local"] = True
            t["path"] = p
            if kind == "read":
                # 阅读按字数计奖励：每 250 字 = 10 分钟（比例统计，不足 250 字按 10 分钟起）
                content = t.get("content") or ""
                if isinstance(content, list):
                    content = "\n".join(str(x) for x in content)
                chars = len(re.sub(r"\s", "", content))
                t["chars"] = chars
                t["minutes"] = max(10, int(round(chars / 250.0 * 10)))
            elif kind == "words":
                # 单词按行数计奖励：每行 = 0.5 分钟，多行汇总为任务总时长
                content = t.get("content")
                lines = len(content) if isinstance(content, list) else \
                    len([l for l in str(content).splitlines() if l.strip()])
                t["chars"] = lines                     # 行数（展示用）
                t["minutes"] = max(1, int(round(lines * 0.5)))
            else:
                t["minutes"] = KMIN[kind]
            if coll and not t["name"].startswith(coll):
                t["name"] = ("%s·%s" % (coll, t["name"]))[:30]
            out["docs"].append(t)

        def add_media(p, kind, coll):
            mid = _local_id("med_", p)
            dur = dur_cache.get(mid)
            if dur is None:                  # 时长探测较慢（cv2/ffmpeg）：按文件缓存
                dur = self._media_duration_ms(p, kind)
                dur_cache[mid] = dur
                changed_cache[0] = True
            # 奖励 = 播放总时长 ÷ 2（向上取整到分钟，保底 1 分钟）；解析失败回退固定值
            if dur > 0:
                minutes = max(1, int(dur / 120000.0 + 0.5))
            else:
                minutes = KMIN[kind]
            out["media"].append({"id": mid, "kind": kind,
                                 "name": os.path.splitext(os.path.basename(p))[0][:30],
                                 "path": p, "collection": coll, "duration_ms": dur,
                                 "minutes": minutes, "local": True})

        def scan_tree(base, kind=None, coll_name=""):
            """递归扫一个文件夹；kind 给定时以文件夹类型为准，否则按扩展名归类。
            coll_name：根目录未知子文件夹作为合集时的合集名。"""
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != "已完成"]   # 归档文件夹不再扫描
                rel = os.path.relpath(root, base)
                coll = coll_name if rel == "." else os.path.basename(rel)
                for fn in sorted(files):
                    p = os.path.join(root, fn)
                    if not os.path.isfile(p) or fn.lower() == "readme.md":
                        continue
                    ext = os.path.splitext(fn)[1].lower()
                    if ext == ".md":
                        if kind:                        # 类型文件夹：文件夹决定类型
                            add_doc(p, kind, coll)
                        else:                           # 兼容模式：按内容判定朗读/单词
                            t0 = parse_local_md(p)
                            if t0:
                                add_doc(p, t0["kind"], coll)
                    elif kind == "listen" and ext in AUDIO_EXT:
                        add_media(p, "listen", coll)
                    elif kind == "watch" and ext in VIDEO_EXT:
                        add_media(p, "watch", coll)
                    elif kind is None and ext in AUDIO_EXT:
                        add_media(p, "listen", coll)
                    elif kind is None and ext in VIDEO_EXT:
                        add_media(p, "watch", coll)

        try:
            mdir = self._media_dir()
            # 时长缓存：媒体文件探测较慢，扫描每轮都跑，按文件 ID 缓存（.cache/durations.json）
            dur_cache, changed_cache = {}, [False]
            try:
                dc_path = os.path.join(mdir, ".cache", "durations.json")
                if os.path.exists(dc_path):
                    dur_cache = json.load(open(dc_path, encoding="utf-8"))
            except Exception:
                dur_cache = {}
            if os.path.isdir(mdir):
                # 分类型文件夹：类型以文件夹为准
                for kind, dname in (("read", "阅读"), ("words", "单词"),
                                    ("watch", "视频"), ("listen", "音频")):
                    base = os.path.join(mdir, dname)
                    if os.path.isdir(base):
                        scan_tree(base, kind)
                # 根目录散落文件与未知子文件夹：按扩展名归类（兼容旧结构）
                for fn in sorted(os.listdir(mdir)):
                    p = os.path.join(mdir, fn)
                    if os.path.isdir(p) and fn not in ("阅读", "单词", "视频", "音频", "已完成"):
                        scan_tree(p, None, fn)      # 未知子文件夹：按扩展名归类，文件夹名=合集
                    elif os.path.isfile(p):
                        ext = os.path.splitext(fn)[1].lower()
                        if ext == ".md":
                            t = parse_local_md(p)
                            if t:
                                add_doc(p, t["kind"], "")
                        elif ext in AUDIO_EXT:
                            add_media(p, "listen", "")
                        elif ext in VIDEO_EXT:
                            add_media(p, "watch", "")
                try:
                    if changed_cache[0]:
                        os.makedirs(os.path.dirname(dc_path), exist_ok=True)
                        json.dump(dur_cache, open(dc_path, "w", encoding="utf-8"),
                                  ensure_ascii=False, indent=1)
                except Exception:
                    pass
        except Exception as e:
            try:
                self.log.write("[media] 扫描失败 %r" % e)
            except Exception:
                pass
        return out

    def _media_prog_path(self):
        return _audio_cfg_path().replace("audio.json", "media.progress.json")

    def _load_media_progress(self):
        try:
            with open(self._media_prog_path(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_media_progress(self, d):
        try:
            p = self._media_prog_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
        except Exception:
            pass

    def _safe_open_media(self, mt):
        """播放入口的异常保护：面板构建中途出错时降级到系统播放器面板，绝不留无法操作的死黑屏。"""
        try:
            self.open_media_player(mt)
        except Exception:
            import traceback
            self.log.write("[media] 播放面板异常，降级系统播放器 "
                           + traceback.format_exc()[-400:])
            self._close_media_panel()
            self._system_player_task(mt)

    def _next_episode(self, mt):
        """合集连播：返回同合集内下一集（未完成、按文件名排序），没有返回 None。"""
        if not mt.get("collection"):
            return None
        prog = self._load_media_progress()
        eps = sorted([m for m in getattr(self, "local_media", [])
                      if m.get("collection") == mt["collection"]
                      and not prog.get(m["id"], {}).get("done")], key=lambda x: x["name"])
        return eps[0] if eps else None

    def _extract_audio_mp3(self, path):
        """抽取视频音轨为 mp3（cv2 播放器的 MCI 音轨用）；无音轨/失败返回 None。"""
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
        cache = os.path.join(os.path.dirname(path), ".cache")
        try:
            os.makedirs(cache, exist_ok=True)
        except Exception:
            return None
        out = os.path.join(cache, _local_id("a_", path) + ".mp3")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        try:
            r = subprocess.run([ff, "-y", "-i", path, "-vn", "-c:a", "mp3", "-b:a", "128k", out],
                               capture_output=True, timeout=1800)
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
                return out
        except Exception:
            pass
        return None

    def _open_cv_player(self, mt):
        """cv2 逐帧内嵌播放（MCI 解不了的格式，如 H.264 MP4）：
        视频帧渲染到面板内黑色 Label；音轨抽成 mp3 由 MCI 单独播放（无音轨则静音播放）。
        完成/发奖/合集连播与 MCI 面板一致。失败返回 False（调用方走系统播放器兜底）。"""
        try:
            import cv2
            from PIL import Image, ImageTk
        except Exception as e:
            self.log.write("[media] cv2/PIL 不可用 %r" % e)
            return False
        cap = cv2.VideoCapture(mt["path"])
        if not cap.isOpened():
            return False
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_ms = 1000.0 / fps
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        dur_ms = int(total_frames * frame_ms)
        audio_alias = None
        amp3 = self._extract_audio_mp3(mt["path"])
        if amp3:
            a = "aud_" + re.sub(r"\W", "", mt["id"])
            if self._mci('open "%s" type mpegvideo alias %s' % (amp3, a), quiet=True) == 0:
                audio_alias = a
        prog_all = self._load_media_progress()
        prog = prog_all.get(mt["id"], {})
        panel = tk.Frame(self.lock_frame, bg="#10141f")
        panel.place(x=0, y=0, relwidth=1, relheight=1)
        self.media_panel = panel
        panel.update_idletasks()
        H = max(400, panel.winfo_height() or panel.winfo_screenheight())
        W = max(600, panel.winfo_width() or panel.winfo_screenwidth())
        kind_name = "📺 视频" if mt["kind"] == "watch" else "🎧 音频"
        tk.Label(panel, text="%s %s · %d 分钟奖励 · 完成后自动计入%s" % (
            kind_name, mt["name"], mt["minutes"],
            "" if audio_alias else "（此文件无音轨，静音播放）"),
            font=("Microsoft YaHei UI", 12, "bold"), bg="#10141f", fg="#ffe08a").pack(pady=(H // 40, 4))
        video_label = tk.Label(panel, bg="#000000")
        video_label.pack(fill="both", expand=True, padx=int(W * 0.06))
        meter = tk.Label(panel, text="", font=("Consolas", 12), bg="#10141f", fg="#7ee08a")
        meter.pack(pady=4)
        st = {"pos": min(int(prog.get("pos") or 0), max(0, dur_ms - 800)),
              "playing": True, "closed": False}
        self._media_state = {"alias": audio_alias or ("cv_" + mt["id"]),
                             "mt": mt, "length": dur_ms, "prog": prog}
        if audio_alias:
            self._mci("play %s from %d" % (audio_alias, st["pos"]))
        cap.set(cv2.CAP_PROP_POS_MSEC, st["pos"])
        img_ref = {"img": None}

        def show_frame():
            ok, frame = cap.read()
            if not ok:
                return False
            img = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            video_label.configure(image=img)
            img_ref["img"] = img                # 防 PhotoImage 被回收
            return True

        def _toggle():
            if st["closed"]:
                return
            if st["playing"]:
                if audio_alias:
                    self._mci("pause %s" % audio_alias, quiet=True)
                st["playing"] = False
                btn_pause.configure(text="▶ 继续")
            else:
                if audio_alias:
                    self._mci("play %s from %d" % (audio_alias, st["pos"]), quiet=True)
                st["playing"] = True
                btn_pause.configure(text="⏸ 暂停")
                panel.after(30, tick)

        def _close():
            st["closed"] = True
            if audio_alias:
                self._mci("pause %s" % audio_alias, quiet=True)
            cap.release()
            prog_all = self._load_media_progress()
            prog_all[mt["id"]] = {"done": False, "pos": int(st["pos"]), "name": mt["name"]}
            self._save_media_progress(prog_all)
            self._media_state = None
            p = getattr(self, "media_panel", None)
            self.media_panel = None
            try:
                p.destroy()
            except Exception:
                pass

        def tick():
            if st["closed"] or self._media_state is None:
                return
            if not st["playing"]:
                return
            if audio_alias:
                b2 = ctypes.create_unicode_buffer(64)
                ctypes.windll.winmm.mciSendStringW("status %s position" % audio_alias, b2, 64, 0)
                try:
                    st["pos"] = int(b2.value or 0)
                except ValueError:
                    pass
            else:
                st["pos"] += int(frame_ms)
            want = int(st["pos"] * fps / 1000.0)
            if abs(cap.get(cv2.CAP_PROP_POS_FRAMES) - want) > 2:
                cap.set(cv2.CAP_PROP_POS_FRAMES, want)
            if not show_frame():
                st["pos"] = dur_ms
            meter.configure(text="%d:%02d / %d:%02d（%d%%）" % (
                st["pos"] // 60000, st["pos"] // 1000 % 60,
                dur_ms // 60000, dur_ms // 1000 % 60, st["pos"] * 100 // max(1, dur_ms)))
            if st["pos"] >= dur_ms - 500:
                prog_all = self._load_media_progress()
                prog_all[mt["id"]] = {"done": True, "pos": dur_ms, "name": mt["name"]}
                self._save_media_progress(prog_all)
                if audio_alias:
                    self._mci("close %s" % audio_alias, quiet=True)
                cap.release()
                self._media_state = None
                p = getattr(self, "media_panel", None)
                self.media_panel = None
                try:
                    p.destroy()
                except Exception:
                    pass
                self.local_media = [m for m in getattr(self, "local_media", []) if m["id"] != mt["id"]]
                self._grant_local_rewards()
                self._todo_sig = None
                self.render()
                nxt = self._next_episode(mt)
                if nxt:
                    self.open_media_player(nxt)   # 合集连播
                return
            panel.after(33, tick)

        btn_pause = tk.Button(panel, text="⏸ 暂停", font=("Microsoft YaHei UI", 12, "bold"),
                              bg="#4a3a78", fg="white", activebackground="#3d3063",
                              relief="flat", cursor="hand2", padx=22, pady=5, command=_toggle)
        btn_pause.pack(pady=8)
        tk.Button(panel, text="⏹ 关闭（记住进度，下次续播）", font=("Microsoft YaHei UI", 11),
                  bg="#2a2a3a", fg="#8fa0bd", activebackground="#3a3a4a", relief="flat",
                  cursor="hand2", padx=14, pady=4, command=_close).pack(pady=4)
        tick()
        return True

    def _transcode_to_mpg(self, path):
        """MP4/H.264 等 MCI 不支持的格式 → ffmpeg 转码为 MPEG-1（MCI mpegvideo 原生可播）。
        缓存在 media/.cache/（同文件只转一次）；无 ffmpeg 或转码失败返回 None。"""
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            self.log.write("[media] 未找到 ffmpeg（pip install imageio-ffmpeg），无法转码")
            return None
        cache = os.path.join(os.path.dirname(path), ".cache")
        try:
            os.makedirs(cache, exist_ok=True)
        except Exception:
            return None
        out = os.path.join(cache, _local_id("t_", path) + ".mpg")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        try:
            r = subprocess.run([ff, "-y", "-i", path,
                                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                                "-c:v", "mpeg1video", "-b:v", "1152k",
                                "-c:a", "mp2", "-b:a", "128k", out],
                               capture_output=True, timeout=1800)
            if r.returncode == 0 and os.path.getsize(out) > 0:
                self.log.write("[media] 已转码为 MCI 可播格式：%s" % out)
                return out
            self.log.write("[media] 转码失败 rc=%d %s" % (
                r.returncode, r.stderr[-200:].decode("utf-8", "ignore")))
        except Exception as e:
            self.log.write("[media] 转码异常 %r" % e)
        return None

    def _system_player_task(self, mt):
        """MCI 不支持的格式（H.264 MP4 等）：临时隐藏锁屏 → 系统播放器全屏播放；
        屏幕顶部保留**置顶迷你条**（我看完了 / 重新打开 / 返回）。
        **防跳看**：累计观看时长（跨重新打开累加）不足 视频时长−1分钟 → 点「我看完了」不发奖，
        提示还差多久；观看期间锁屏隐藏（系统播放器是独立窗口，会被全屏锁屏压住）。"""
        kind_name = "📺 视频" if mt["kind"] == "watch" else "🎧 音频"
        self._close_media_panel()
        dur_ms = _video_duration_ms(mt["path"])
        prog0 = self._load_media_progress().get(mt["id"], {})
        watched = {"s": float(prog0.get("watched") or 0.0)}
        opened_at = {"t": None}
        need_hint = ("需观看约 %d 分钟" % round((dur_ms / 1000.0) / 60)) if dur_ms else "需观看约 20 分钟"

        def _done():
            now = time.time()
            if opened_at["t"]:
                watched["s"] += now - opened_at["t"]
            opened_at["t"] = None
            ok, remain = _watch_enough(dur_ms, watched["s"])
            if not ok:
                lbl.configure(text="⏳ 观看时长不足（还差约 %.0f 分钟）——%s，请继续观看"
                              % (remain, need_hint), fg="#e67e22")
                _save_watched()
                return
            prog = self._load_media_progress()
            prog[mt["id"]] = {"done": True, "pos": 0, "name": mt["name"],
                              "self": True, "watched": int(watched["s"])}
            self._save_media_progress(prog)
            self.local_media = [m for m in getattr(self, "local_media", []) if m["id"] != mt["id"]]
            self._grant_local_rewards()
            self._todo_sig = None
            try:
                bar.destroy()
            except Exception:
                pass
            self.root.deiconify()              # 恢复锁屏
            self.render()

        def _save_watched():
            prog = self._load_media_progress()
            p = prog.get(mt["id"], {})
            p["watched"] = int(watched["s"])
            p.setdefault("done", False)
            prog[mt["id"]] = p
            self._save_media_progress(prog)

        def _back():
            if opened_at["t"]:
                watched["s"] += time.time() - opened_at["t"]
                opened_at["t"] = None
                _save_watched()
            try:
                bar.destroy()
            except Exception:
                pass
            self.root.deiconify()              # 返回锁定页（不计完成，观看时长已累计）
            self.render()

        def _open_player():
            self.root.withdraw()               # 播放期间隐藏锁屏，让系统播放器可见
            opened_at["t"] = time.time()
            try:
                os.startfile(mt["path"])
            except Exception as e:
                self.log.write("[media] startfile 失败 %r" % e)
                if opened_at["t"]:
                    watched["s"] += time.time() - opened_at["t"]
                _back()

        bar = tk.Toplevel(self.root)
        bar.title("正在观看：" + mt["name"])
        bar.configure(bg="#10141f")
        bar.geometry("620x170+%d+40" % max(0, (bar.winfo_screenwidth() - 620) // 2))
        bar.attributes("-topmost", True)
        bar.protocol("WM_DELETE_WINDOW", _back)
        tk.Label(bar, text="%s 正在观看：%s" % (kind_name, mt["name"]),
                 font=("Microsoft YaHei UI", 12, "bold"), bg="#10141f", fg="#ffe08a").pack(pady=(12, 2))
        lbl = tk.Label(bar, text="已累计观看 %d 分钟 · %s（不足时长无法领取奖励）"
                       % (watched["s"] / 60, need_hint),
                       font=("Microsoft YaHei UI", 10), bg="#10141f", fg="#7ee08a")
        lbl.pack(pady=2)
        brow = tk.Frame(bar, bg="#10141f")
        brow.pack(pady=6)
        tk.Button(brow, text="▶ 打开/重新打开", font=("Microsoft YaHei UI", 11, "bold"),
                  bg="#1f5c8b", fg="white", activebackground="#174a70", relief="flat",
                  cursor="hand2", padx=12, pady=5, command=_open_player).pack(side="left", padx=5)
        tk.Button(brow, text="✅ 我看完了", font=("Microsoft YaHei UI", 11, "bold"),
                  bg="#2e7d32", fg="white", activebackground="#256a29", relief="flat",
                  cursor="hand2", padx=12, pady=5, command=_done).pack(side="left", padx=5)
        tk.Button(brow, text="◀ 返回", font=("Microsoft YaHei UI", 11),
                  bg="#2a2a3a", fg="#8fa0bd", activebackground="#3a3a4a", relief="flat",
                  cursor="hand2", padx=12, pady=5, command=_back).pack(side="left", padx=5)
        tk.Label(bar, text="防跳看：观看时长不足视频长度时，「我看完了」不会发放奖励",
                 font=("Microsoft YaHei UI", 9), bg="#10141f", fg="#5a6478").pack(pady=(4, 10))
        _open_player()

    def _close_media_panel(self, mark_done=False):
        """收起内嵌媒体面板：暂停 MCI、记住进度；mark_done=True 记为完成并发奖。"""
        st = getattr(self, "_media_state", None)
        self._media_state = None
        p = getattr(self, "media_panel", None)
        self.media_panel = None
        if p is not None:
            try:
                p.destroy()
            except Exception:
                pass
        if not st:
            return
        alias, mt, length = st["alias"], st["mt"], st["length"]
        b2 = ctypes.create_unicode_buffer(64)
        ctypes.windll.winmm.mciSendStringW("status %s position" % alias, b2, 64, 0)
        try:
            pos = int(b2.value or 0)
        except ValueError:
            pos = 0
        done = bool(mark_done or (length and pos >= length - 1500))
        prog_all = self._load_media_progress()
        prog_all[mt["id"]] = {"done": done or bool(st["prog"].get("done")),
                              "pos": length - 1000 if done else pos, "name": mt["name"]}
        self._save_media_progress(prog_all)
        self._mci("close %s" % alias, quiet=True)
        if done and not st["prog"].get("done"):
            self.local_media = [m for m in getattr(self, "local_media", []) if m["id"] != mt["id"]]
            self._grant_local_rewards()
            self._todo_sig = None
            self.render()

    def _mci(self, cmd, quiet=False):
        r = ctypes.windll.winmm.mciSendStringW(cmd, None, 0, 0)
        if r and not quiet:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.winmm.mciGetErrorStringW(r, buf, 256)
            self.log.write("[media] MCI %s → %s" % (cmd[:60], buf.value))
        return r

    def open_media_player(self, mt):
        """🎧/📺 本地媒体播放：锁屏页内嵌面板；视频以**子窗口**嵌入面板（不会被锁屏压住）；
        MCI 播放、断点续播、播放完成自动通过并发放奖励（合集自动接下一集）。"""
        if getattr(self, "_media_state", None):
            self._close_media_panel()          # 已有播放面板：先收起再开新的
        prog_all = self._load_media_progress()
        prog = prog_all.get(mt["id"], {})
        alias = "pcg_" + re.sub(r"\W", "", mt["id"])
        path = mt["path"]
        is_video = mt["kind"] == "watch"
        # 先建内嵌面板：视频区域要作为 MCI 的父窗口（style child），必须先于 open 存在
        panel = tk.Frame(self.lock_frame, bg="#10141f")
        panel.place(x=0, y=0, relwidth=1, relheight=1)
        self.media_panel = panel
        panel.update_idletasks()
        H = max(400, panel.winfo_height() or panel.winfo_screenheight())
        W = max(600, panel.winfo_width() or panel.winfo_screenwidth())
        kind_name = "📺 视频" if is_video else "🎧 音频"
        tk.Label(panel, text="%s %s · %d 分钟奖励 · 完成后自动计入" % (kind_name, mt["name"], mt["minutes"]),
                 font=("Microsoft YaHei UI", 13, "bold"), bg="#10141f", fg="#ffe08a").pack(pady=(H // 40, 6))
        meter = tk.Label(panel, text="", font=("Consolas", 13), bg="#10141f", fg="#7ee08a")
        meter.pack(pady=8)
        video_area = None
        if is_video:
            video_area = tk.Frame(panel, bg="#000000", height=int(H * 0.58))
            video_area.pack(fill="x", padx=int(W * 0.05))
            video_area.update_idletasks()
        state = {"paused": False}
        # 打开媒体：视频优先作为面板子窗口（style child，z 序跟随面板不会被压住）；失败逐级降级
        attempts = []
        if is_video:
            hwnd = video_area.winfo_id()
            attempts += ['open "%s" alias %s parent %d style child' % (path, alias, hwnd),
                         'open "%s" type mpegvideo alias %s parent %d style child' % (path, alias, hwnd)]
        attempts += ['open "%s" alias %s' % (path, alias),
                     'open "%s" type mpegvideo alias %s' % (path, alias),
                     'open "%s" type waveaudio alias %s' % (path, alias)]
        opened = False
        for cmd in attempts:
            if self._mci(cmd) == 0:              # 失败也记日志（排查"音频打不开"）
                opened = True
                state["child"] = "parent" in cmd
                break
        if not opened and is_video:
            # MCI 无该格式解码器（H.264 MP4 常见）→ ffmpeg 转码为 MPEG-1（MCI 原生可播），缓存复用
            mpg = self._transcode_to_mpg(path)
            if mpg:
                for cmd in ('open "%s" alias %s' % (mpg, alias),
                            'open "%s" type mpegvideo alias %s' % (mpg, alias)):
                    if self._mci(cmd) == 0:
                        opened = True
                        break
        if not opened:
            self.media_panel = None
            panel.destroy()
            if is_video and self._open_cv_player(mt):
                return                       # cv2 逐帧内嵌播放（H.264 MP4 等都支持）
            self._system_player_task(mt)     # cv2 不可用/失败 → 系统播放器 + 自报完成
            return
        self._media_state = {"alias": alias, "mt": mt, "length": 0, "prog": prog}
        buf = ctypes.create_unicode_buffer(64)
        ctypes.windll.winmm.mciSendStringW("status %s length" % alias, buf, 64, 0)
        try:
            length = int(buf.value or 0)
        except ValueError:
            length = 0
        self._media_state["length"] = length
        pos0 = min(int(prog.get("pos") or 0), max(0, length - 1500)) if length else 0
        if is_video and state.get("child"):
            self._mci("put %s client at 0 0 %d %d" % (alias, video_area.winfo_width(),
                                                      video_area.winfo_height()), quiet=True)

        def _toggle():
            if self._media_state is None:
                return
            if state["paused"]:
                self._mci("resume %s" % alias, quiet=True) or self._mci("play %s" % alias, quiet=True)
                state["paused"] = False
                btn_pause.configure(text="⏸ 暂停")
            else:
                self._mci("pause %s" % alias, quiet=True)
                state["paused"] = True
                btn_pause.configure(text="▶ 继续")

        btn_pause = tk.Button(panel, text="⏸ 暂停", font=("Microsoft YaHei UI", 12, "bold"),
                              bg="#4a3a78", fg="white", activebackground="#3d3063",
                              relief="flat", cursor="hand2", padx=22, pady=5, command=_toggle)
        btn_pause.pack(pady=10)
        tk.Button(panel, text="⏹ 关闭（记住进度，下次续播）", font=("Microsoft YaHei UI", 11),
                  bg="#2a2a3a", fg="#8fa0bd", activebackground="#3a3a4a", relief="flat",
                  cursor="hand2", padx=14, pady=4,
                  command=lambda: self._close_media_panel(False)).pack(pady=4)

        self._mci("play %s from %d" % (alias, pos0))

        def poll():
            if getattr(self, "_media_state", {}).get("alias") != alias:
                return                          # 面板已被关闭/替换：停止轮询
            if state["paused"]:
                panel.after(500, poll)
                return
            b2 = ctypes.create_unicode_buffer(64)
            ctypes.windll.winmm.mciSendStringW("status %s position" % alias, b2, 64, 0)
            try:
                pos = int(b2.value or 0)
            except ValueError:
                pos = 0
            if length:
                meter.configure(text="%d:%02d / %d:%02d（%d%%）" % (
                    pos // 60000, pos // 1000 % 60, length // 60000, length // 1000 % 60,
                    pos * 100 // length))
                if pos >= length - 1200:
                    nxt = self._next_episode(mt)
                    self._close_media_panel(mark_done=True)
                    if nxt:
                        self.open_media_player(nxt)   # 合集：本集完成自动接下一集
                    return
            panel.after(500, poll)
        poll()

    def _grant_ai_rewards(self, disp):
        """评分通过且未发放过的 AI 任务 → 奖励分钟入本机钱包台账（每任务只发一次）。"""
        granted = {r.get("task") for r in (self.ai_doc.get("rewards") or [])}
        changed = False
        for t in disp:
            if not isinstance(t, dict) or t.get("kind") not in ("read", "words"):
                continue
            tid = str(t.get("id") or "")
            res = ((self.ai_doc.get("results") or {}).get(tid) or {}).get(today_key()) or {}
            if res.get("pass") and tid not in granted:
                try:
                    m = int(t.get("minutes") or 0)
                except (TypeError, ValueError):
                    m = 0
                if m > 0:
                    self.ai_doc.setdefault("rewards", []).append({
                        "id": "ai-%s-%d" % (tid, int(time.time())), "task": tid,
                        "name": "🎤 " + str(t.get("name") or tid), "minutes": m,
                        "grantedAt": time.time()})
                    changed = True
                    self.log.write("[ai] 「%s」评分通过，发放奖励 %d 分钟入钱包" % (t.get("name"), m))
        if changed:
            save_ai_score(self.ai_doc)
            for t2 in disp:
                tid2 = str(t2.get("id") or "") if isinstance(t2, dict) else ""
                if not tid2 or t2.get("kind") not in ("read", "words"):
                    continue
                res2 = ((self.ai_doc.get("results") or {}).get(tid2) or {}).get(today_key()) or {}
                if res2.get("pass") and tid2 not in granted:
                    self._send_ai_receipt(tid2, str(t2.get("name") or tid2),
                                          int(res2.get("best") or 0), int(t2.get("minutes") or 0))

    def _send_ai_receipt(self, tid, name, score, minutes):
        """孩子端 → 家长端的成绩回执：http 数据源 POST <origin>/api/airesult；
           本地文件数据源写同目录 chore-ai-results.json。失败仅记日志（不影响评分流程）。"""
        doc = {"taskId": tid, "name": name, "score": score, "minutes": minutes,
               "date": today_key(), "passedAt": time.time()}
        url = str(self.cfg.get("gist_url") or "")
        try:
            if url[:8].lower().startswith("file://") or (len(url) > 2 and url[1] == ":") or url.startswith("\\\\"):
                path = url
                if path[:8].lower().startswith("file:///"):
                    path = path[8:]
                elif path[:7].lower().startswith("file://"):
                    path = path[7:]
                path = path.replace("/", os.sep)
                rpath = os.path.join(os.path.dirname(path), "chore-ai-results.json")
                try:
                    with open(rpath, encoding="utf-8") as f:
                        allr = json.load(f)
                except Exception:
                    allr = {"results": []}
                allr["results"] = [x for x in (allr.get("results") or [])
                                   if x.get("taskId") != tid] + [doc]
                allr["updatedAt"] = time.time()
                with open(rpath, "w", encoding="utf-8") as f:
                    json.dump(allr, f, ensure_ascii=False, indent=2)
                self.log.write("[ai] 回执已写入 %s" % rpath)
            elif url[:8].lower().startswith("https://") or url[:7].lower().startswith("http://"):
                from urllib.parse import urlparse
                p = urlparse(url)
                origin = "%s://%s" % (p.scheme, p.netloc)
                req = urllib.request.Request(origin + "/api/airesult",
                                             data=json.dumps(doc).encode("utf-8"),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5)
                self.log.write("[ai] 回执已上报 %s/api/airesult" % origin)
            else:
                self.log.write("[ai] 数据源非 http/文件，跳过回执")
        except Exception as e:
            self.log.write("[ai] 回执上报失败 %r" % e)

    def _reload_ai_score(self):
        """评分程序写盘后热加载（mtime 变化即重放最近一次云端数据）。"""
        try:
            mt = max((os.path.getmtime(p) for p in _aiscore_paths() if os.path.exists(p)), default=0)
        except OSError:
            return False
        if mt != self._ai_mtime:
            self._ai_mtime = mt
            self.ai_doc = load_ai_score()
            return True
        return False

    def _scorer_cmd(self):
        exe = os.path.join(script_dir(), "PCVoiceScore.exe")
        if os.path.exists(exe):
            return [exe]
        pyw = os.path.join(script_dir(), "PCVoiceScore.pyw")
        if not os.path.exists(pyw):
            return None
        py = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(py):
            py = sys.executable
        return [py, pyw]

    def launch_scorer(self, tid):
        """锁定页「🎤 开始任务」：在本窗口内嵌打开评分面板（不开新窗口）。"""
        if getattr(self, "scorer_active", False):
            self.log.write("[ai] 评分已在进行中")
            return
        self._close_media_panel()     # 先收起媒体面板（暂停并记住进度）
        self._run_scoring(tid)

    # ---- 自动升级（GitHub 新版本检测/下载/安装） ----
    def _update_urls(self, name):
        """返回 name 文件的候选下载地址（测试可覆盖 update_base；生产 raw 优先 jsdelivr 兜底）。"""
        base = str(self.cfg.get("update_base") or "").rstrip("/")
        if base:
            return [base + "/" + name]
        return [UPDATE_REPO_RAW + "/" + name, UPDATE_REPO_JSD + "/" + name]

    def _fetch_bytes(self, urls, timeout=15):
        for u in urls:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "PCGuardian-Update"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read()
            except Exception:
                continue
        return None

    @staticmethod
    def _sha256_bytes(b):
        import hashlib
        return hashlib.sha256(b).hexdigest()

    @classmethod
    def _sha256_file(cls, path):
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _check_update(self):
        if "--demo" in sys.argv or getattr(self, "scorer_active", False):
            return
        man_b = self._fetch_bytes(self._update_urls("version.json"))
        if not man_b:
            self.log.write("[update] 清单获取失败（网络），本轮跳过")
            return
        man = json.loads(man_b.decode("utf-8"))
        ver = str(man.get("version") or "")
        state = self._update_state
        files = man.get("files") or {}
        changed = []
        for name in UPDATE_FILES:
            want = (files.get(name) or {}).get("sha256")
            if not want:
                continue
            p = os.path.join(script_dir(), name)
            local = self._sha256_file(p) if os.path.exists(p) else None
            if local != want:
                changed.append(name)
        exe_info = man.get("exe") or {}
        exe_new_path = os.path.join(script_dir(), "PCGuardian.exe.new")
        exe_changed = False
        if getattr(sys, "frozen", False) and exe_info.get("sha256"):
            exe_changed = self._sha256_file(sys.executable) != exe_info["sha256"]
            if not exe_changed and os.path.exists(exe_new_path):
                os.remove(exe_new_path)          # 残留的已安装补丁清理
        if not changed and not exe_changed:
            if state.get("version") != ver:
                state["version"] = ver
                self.log.write("[update] 已是最新版本 %s" % ver)
            return
        # 下载有变化的文件（校验 sha256 后落盘为 .new）
        for name in changed:
            b = self._fetch_bytes(self._update_urls(name), timeout=60)
            if not b:
                self.log.write("[update] 下载失败 %s" % name)
                return
            if self._sha256_bytes(b) != (files.get(name) or {}).get("sha256"):
                self.log.write("[update] 校验失败 %s，本轮放弃" % name)
                return
            with open(os.path.join(script_dir(), name + ".new"), "wb") as f:
                f.write(b)
            self.log.write("[update] 已下载 %s（%d 字节）" % (name, len(b)))
        exe_ready = False
        if exe_changed or os.path.exists(exe_new_path):
            if not exe_changed and exe_info.get("sha256") and os.path.exists(exe_new_path) \
                    and self._sha256_file(exe_new_path) == exe_info["sha256"]:
                exe_ready = True
            else:
                url = exe_info.get("url")
                if not url:
                    self.log.write("[update] 清单未提供 exe 下载地址")
                    return
                b = self._fetch_bytes([url], timeout=300)
                if not b:
                    self.log.write("[update] exe 下载失败")
                    return
                if self._sha256_bytes(b) != exe_info.get("sha256"):
                    self.log.write("[update] exe 校验失败")
                    return
                with open(exe_new_path, "wb") as f:
                    f.write(b)
                exe_ready = True
        self._apply_update(changed, exe_ready and exe_new_path, ver)

    def _apply_update(self, changed, exe_new_path, ver):
        # .pyw 是数据文件，运行中覆盖安全；评分面板下次打开自动用新代码
        for name in changed:
            src_p = os.path.join(script_dir(), name + ".new")
            if os.path.exists(src_p):
                os.replace(src_p, os.path.join(script_dir(), name))
        self._S = None
        self._update_state["version"] = ver
        self.log.write("[update] 程序文件已更新到 %s" % ver)
        # 冻结 exe：需在锁定页安装（改名换位 + 重启），其他状态推迟到下轮
        if getattr(sys, "frozen", False) and exe_new_path:
            if self.mode != self.MODE_LOCKED:
                self.log.write("[update] 新 exe 已就绪，等待回到锁定页后自动安装")
                return
            exe = sys.executable
            old = exe + ".old"
            try:
                if os.path.exists(old):
                    os.remove(old)
            except Exception:
                pass
            try:
                os.rename(exe, old)      # 运行中的 exe 允许重命名（Windows 特性）
            except Exception as e:
                self.log.write("[update] 旧 exe 重命名失败 %r（下次启动重试）" % e)
                return
            os.replace(exe_new_path, exe)
            try:                          # 伪装副本 sysguard.exe 同步更新（若存在）
                sg = os.path.join(os.path.dirname(exe), "sysguard.exe")
                if os.path.exists(sg):
                    import shutil
                    shutil.copyfile(exe, sg)
            except Exception:
                pass
            self.log.write("[update] 新 exe 已安装，准备重启")
        # 重启应用新版本（bat 由本进程生成，路径/参数与本机安装方式一致）
        bat = os.path.join(script_dir(), "update_restart.bat")
        NL2 = chr(13) + chr(10)
        args = " ".join('"%s"' % a for a in sys.argv[1:] if a != "--demo")
        lines = ["@echo off", "timeout /t 2 /nobreak >nul"]
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            lines.append('taskkill /f /im sysguard.exe >nul 2>&1')
            lines.append('start "" "%s" %s' % (sys.executable, args))
        else:
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            lines.append('cd /d "%s"' % script_dir())
            if os.path.exists(pyw):
                lines.append('start "" "%s" "guardian.pyw" %s' % (pyw, args))
            else:
                lines.append('start "" python "guardian.pyw" %s' % args)
        lines.append("exit")
        with open(bat, "w") as f:
            f.write(NL2.join(lines) + NL2)
        import subprocess as _sp
        _sp.Popen(["cmd", "/c", bat], creationflags=0x08000000)
        self.log.write("[update] 将在 2 秒后重启以应用新版本 %s" % ver)
        try:
            self.root.after(0, self.root.destroy)   # 结束当前实例（bat 会拉起新实例）
        except Exception:
            pass

    def _update_loop(self):
        time.sleep(90)                 # 启动缓冲：等数据/界面就绪
        while not self._stop_up.is_set():
            try:
                if self.cfg.get("auto_update", True) and not getattr(self, "scorer_active", False) \
                        and self.mode in (self.MODE_LOCKED, self.MODE_UNLOCKED):
                    self._check_update()
            except Exception as e:
                try:
                    self.log.write("[update] 检查异常 %r" % e)
                except Exception:
                    pass
            hours = 6
            try:
                hours = max(1, int(self.cfg.get("update_hours") or 6))
            except Exception:
                pass
            for _ in range(hours * 3600):
                if self._stop_up.is_set():
                    return
                time.sleep(1)

    # ---- 内嵌评分（PCVoiceScore 模块加载进本进程，面板覆盖锁定页） ----
    def _score_module(self):
        if not hasattr(self, "_S") or self._S is None:
            import importlib.util
            path = os.path.join(script_dir(), "PCVoiceScore.pyw")
            spec = importlib.util.spec_from_file_location("pcvoicescore_embedded", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._S = mod
        return self._S

    def _scoring_done(self):
        """评分面板关闭：销毁面板、恢复锁定页、热加载成绩。"""
        self.scorer_active = False
        self.scorer = None
        try:
            self.score_panel.destroy()
        except Exception:
            pass
        self.score_panel = None
        self._reload_ai_score()
        if self._last_data is not None:
            try:
                self.apply_state(self._last_data)
            except Exception:
                pass
        self.render()
        self.log.write("[ai] 评分面板已关闭，回到锁定页")

    def _run_scoring(self, tid):
        S = self._score_module()
        task = next((t for t in getattr(self, "ai_tasks", []) if t["id"] == tid), None)
        if not task:
            self.log.write("[ai] 任务 %s 不存在" % tid)
            return
        max_att = int(task.get("max_attempts") or self.cfg.get("ai_max_attempts") or 5)
        day, _ = S.begin_attempt(tid, max_att)
        if day is None:
            self.log.write("[ai] 任务 %s 今日已通过或次数用完" % tid)
            return
        self.score_panel = tk.Frame(self.lock_frame, bg="#10141f")
        self.score_panel.place(x=0, y=0, relwidth=1, relheight=1)
        app = S.ScoreApp({"id": tid, "kind": task["kind"], "name": task["name"],
                          "minutes": task["minutes"], "threshold": task["threshold"],
                          "poem": task.get("poem"), "quiz": task.get("quiz"),
                          "maxAttempts": max_att},
                         None, master=self.score_panel, on_done=self._scoring_done)
        self.scorer = app
        self.scorer_active = True
        self._hide_lock_sections()
        app.message("🎤 " + task["name"],
                    ["正在连接云端语音识别…（小米 MiMo）",
                     "第 %d/%d 次尝试 · 达标线 %d 分 · 奖励 %d 分钟"
                     % (day.get("attempts"), max_att, task["threshold"], task["minutes"])])
        self.log.write("[ai] 内嵌评分面板已显示 task=%s 第 %s/%s 次" % (tid, day.get("attempts"), max_att))

        def worker():
            result_box = {}
            try:
                if task.get("local"):
                    # 本地媒体文件夹任务：不在分派数据里，任务配置直接取自扫描结果
                    task_full = {"id": tid, "kind": task["kind"], "name": task["name"],
                                 "minutes": task["minutes"], "threshold": task["threshold"],
                                 "poem": False, "quiz": False,
                                 "content": task.get("content") or ""}
                    client = S.load_ai_client(self.cfg.get("gist_url"))
                else:
                    task_full, client, _ = S.load_task_and_client(tid, self.cfg.get("gist_url"))
                app.client = client             # 供 🔊 听发音（MiMo TTS）使用
                asr_fn, lang, _note = S.make_asr_for_task(task_full, client)
                if task_full.get("quiz") and not client.chat_ok:
                    task_full = dict(task_full)
                    task_full["quiz"] = False
                app.message("🎤 %s" % task_full.get("name"),
                            ["达到 %d 分可得 %d 分钟" % (int(task_full.get("threshold") or 80),
                                                      int(task_full.get("minutes") or 0)),
                             "第 %d/%d 次尝试" % (day.get("attempts"), max_att),
                             "云端语音识别（小米 MiMo）",
                             "点击「🎤 开始阅读」开始"])
                if task_full.get("kind") == "words":
                    r = S.evaluate_words(task_full, app, asr_fn, __import__("random").Random(), client)
                else:
                    chat = client.chat if (task_full.get("quiz") and client.chat_ok) else None
                    r = S.evaluate_read(task_full, app, asr_fn, chat, __import__("random").Random())
                result_box["r"] = r
            except Exception:
                import traceback
                result_box["err"] = traceback.format_exc()
                self.log.write("[ai] 评分异常 " + result_box["err"][-500:])
                try:
                    err_last = [l for l in result_box["err"].splitlines() if l.strip()][-1]
                    app.message("⚠ 评分出错，请把本行文字发给家长",
                                [err_last[:160], "点击「关闭」回到锁定页"])
                except Exception:
                    pass
            finally:
                r = result_box.get("r") or {"score": 0, "pass": False, "recordings": 0}
                S.finish_attempt(tid, r)
                # 一次都没读 → 退还尝试次数（不浪费机会）
                if int(r.get("recordings") or 0) == 0 and not r.get("pass"):
                    if S.rollback_attempt(tid):
                        self.log.write("[ai] 本次未录音，尝试次数已退还")
                # 结算画面：分项明细（朗读/问答）
                parts = []
                if r.get("read") is not None:
                    parts.append("朗读 %s 分" % r["read"])
                if r.get("quiz") is not None:
                    parts.append("问答 %s 分" % r["quiz"])
                detail = ("（" + " · ".join(parts) + "）") if parts else ""
                ok = r.get("pass")
                app.message(("🎉 恭喜通过！总分 %s 分%s" % (r.get("score"), detail)) if ok
                            else ("💪 本次总分 %s 分，未达到 %s 分线%s"
                                  % (r.get("score"), r.get("threshold"), detail)),
                            [("奖励 %d 分钟已入钱包，回锁定页勾选使用" % int(task.get("minutes") or 0))
                             if ok else "回锁定页可再试（剩余次数见任务行）",
                             "窗口即将自动关闭…"])
                try:
                    time.sleep(6)
                except Exception:
                    pass
                try:
                    app.root.after(0, app.quit_all)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()


    # ---- 零花钱兑换电脑时长（本机记账，云端只读不动） ----
    def _redeem_open(self):
        """兑换开放：家长开启 且（默认仅周六/周日）。"""
        if not self.redeem_enabled:
            return False
        if self.cfg.get("redeem_weekend_only", True):
            return datetime.datetime.now().weekday() >= 5
        return True

    def _money_left(self):
        return round(self.money_cloud - float(self.redeem_doc.get("totalSpent") or 0), 2)

    def _redeem_spent_today(self):
        d = (self.redeem_doc.get("days") or {}).get(today_key()) or {}
        try:
            return round(float(d.get("spent") or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    def _render_redeem(self):
        bal = self._money_left()
        cap_left = max(0.0, round(self.redeem_cap - self._redeem_spent_today(), 2))
        arm = self._redeem_arm
        sig = (round(bal, 2), round(cap_left, 2), round(self.game_rate, 2),
               arm[0] if arm else 0, self._redeem_msg)
        if sig == self._redeem_sig:
            return
        self._redeem_sig = sig
        for c in self.redeem_frame.winfo_children():
            c.destroy()
        head = tk.Label(self.redeem_frame,
                        text="⏱ 可兑换的时间分布（点击选择，再点一次确认）"
                             if not self._redeem_arm else "⏱ 再点一次同样的档位即确认兑换",
                        font=("Microsoft YaHei UI", self.sml), bg="#10141f", fg="#8fa0bd")
        head.pack(anchor="w", pady=(0, 2))
        bar = tk.Frame(self.redeem_frame, bg="#10141f")
        bar.pack(pady=2)
        for m in (15, 30, 45):
            cost = round(self.game_rate * m / 60.0, 2)
            ok = cost <= bal + 1e-9 and cost <= cap_left + 1e-9
            armed = arm is not None and arm[0] == m
            b = tk.Button(bar, text=("确认兑换？" if armed else "%d分钟 ¥%.2f" % (m, cost)) if ok else "%d分钟 ¥%.2f" % (m, cost),
                          font=("Microsoft YaHei UI", self.sml, "bold"),
                          bg="#8a6d1f" if armed else ("#3d5a2e" if ok else "#2a2a3a"),
                          fg="white" if ok else "#77778f",
                          activebackground="#6f5817", activeforeground="white",
                          relief="flat", cursor="hand2" if ok else "arrow", padx=12, pady=5,
                          state="normal" if ok else "disabled", disabledforeground="#77778f",
                          command=lambda mm=m: self._on_redeem_chip(mm))
            b.pack(side="left", padx=4)
        if self._redeem_msg:
            tk.Label(self.redeem_frame, text=self._redeem_msg, font=("Microsoft YaHei UI", self.sml),
                     bg="#10141f", fg="#b9f6c8" if self._redeem_msg.startswith("✅") else "#ffb3b6"
                     ).pack(anchor="w", padx=8)

    def _on_redeem_chip(self, minutes):
        cost = round(self.game_rate * minutes / 60.0, 2)
        if self._redeem_arm is not None and self._redeem_arm[0] == minutes:
            self._redeem_arm = None
            self.redeem_minutes(minutes, cost)
        else:
            self._redeem_arm = (minutes, cost, time.time())
            self._redeem_msg = ""
            self._redeem_sig = None
            self.render()

    def redeem_minutes(self, minutes, cost):
        """零花钱兑换：本机台账记账 + 时长入钱包（同一奖励机制，选定使用时再扣除）。"""
        if not self.redeem_enabled:
            return
        bal = self._money_left()
        cap_left = max(0.0, round(self.redeem_cap - self._redeem_spent_today(), 2))
        if cost > bal + 1e-9:
            self._redeem_msg = "余额不足（当前 ¥%.2f）" % bal
            self._redeem_sig = None; self.render()
            return
        if cost > cap_left + 1e-9:
            self._redeem_msg = "超过今日兑换上限（还剩 ¥%.2f，明天再来）" % cap_left
            self._redeem_sig = None; self.render()
            return
        day = self.redeem_doc.setdefault("days", {}).setdefault(today_key(), {"spent": 0.0, "entries": []})
        day["spent"] = round(float(day.get("spent") or 0) + cost, 2)
        day.setdefault("entries", []).append({"minutes": minutes, "yuan": cost, "ts": time.time()})
        self.redeem_doc["totalSpent"] = round(float(self.redeem_doc.get("totalSpent") or 0) + cost, 2)
        save_redeem(self.redeem_doc)
        rid = "rd-%d" % int(time.time() * 1000)
        self.ai_doc.setdefault("rewards", []).append({
            "id": rid, "task": "redeem", "name": "💰 兑换 %d 分钟" % minutes,
            "minutes": minutes, "grantedAt": time.time()})
        save_ai_score(self.ai_doc)
        self.log.write("[redeem] 零花钱兑换 %d 分钟（-¥%.2f，余额 ¥%.2f → ¥%.2f）"
                       % (minutes, cost, bal, round(bal - cost, 2)))
        self._redeem_msg = "✅ 已兑换 %d 分钟，在下方「🎁 可用的奖励时间」勾选使用" % minutes
        self._redeem_sig = None
        if self._last_data is not None:
            self.apply_state(self._last_data)
        self.render()

    # ---- 右栏：待完成的任务（必做 + AI 待评任务带「开始任务」按钮 + 待家长打卡任务） ----
    def _media_todo(self, kind):
        """待完成的媒体任务：本地扫描的（带播放）+ 分派数据里的（等打卡，类型就绪前占位）"""
        prog = self._load_media_progress()
        done_ids = {t for t, p in prog.items() if p.get("done")}
        out = []
        for (tid, n, m, earned, k) in getattr(self, "dispatch_view", []):
            if k == kind and not earned:
                out.append({"type": "dispatch", "id": tid, "name": n, "minutes": m})
        for mt in getattr(self, "local_media", []):
            if mt["kind"] == kind and mt["id"] not in done_ids:
                d = dict(mt)
                d["type"] = "local"
                d["pos"] = int(prog.get(mt["id"], {}).get("pos") or 0)
                out.append(d)
        return out

    def _render_todo_pane(self):
        sig = ([(tid, self.done_map.get(tid)) for tid, _ in self.required]
               + [(t["id"], t["pass"], t["attempts"], t["best"], t["minutes"]) for t in self.ai_tasks]
               + [(tid, n, m) for (tid, n, m, earned, kind) in getattr(self, "dispatch_view", []) if not earned and not kind]
               + [(m["id"], m.get("pos", 0), m["type"]) for c in ("watch", "listen")
                  for m in self._media_todo(c)]
               + [getattr(self, "_todo_tab", "read")])
        if sig == self._todo_sig:
            return
        self._todo_sig = sig
        for c in self.todo_inner.winfo_children():
            c.destroy()
        # ---- Tab 按钮：与「📋 待完成的任务」同一行（标题左 / Tab 右） ----
        for w in self.todo_tab_bar.winfo_children():
            w.destroy()
        sml = self.sml
        tab = getattr(self, "_todo_tab", "read")
        # ---- Tab 切换：📖 阅读 / 🔤 单词 / 📺 视频 / 🎧 音频（带待办数量） ----
        todo_ai = [t for t in self.ai_tasks if not t["pass"]]
        counts = {"read": len([t for t in todo_ai if t.get("kind") == "read"]),
                  "words": len([t for t in todo_ai if t.get("kind") == "words"]),
                  "watch": len(self._media_todo("watch")),
                  "listen": len(self._media_todo("listen"))}
        for tid_, tname in (("read", "阅读"), ("words", "单词"),
                            ("watch", "视频"), ("listen", "音频")):
            active = (tab == tid_)
            n = counts[tid_]
            tk.Button(self.todo_tab_bar, text="%s %d" % (tname, n),
                      font=("Microsoft YaHei UI", sml, "bold" if active else "normal"),
                      bg="#3d3063" if active else "#1a2338",
                      fg="#ffe08a" if active else "#8fa0bd",
                      activebackground="#2a2a3a", relief="flat", cursor="hand2",
                      padx=7, pady=1,
                      command=lambda t=tid_: self._switch_todo_tab(t)).pack(side="left", padx=(4, 0))
        # ---- 必做任务（家长打卡，任何 Tab 顶部都显示） ----
        for tid, name in self.required:
            ok = self.done_map.get(tid)
            tk.Label(self.todo_inner,
                     text="%s %s（必做·家长打卡）" % ("✅" if ok else "❌", name),
                     font=("Microsoft YaHei UI", sml), bg="#1a2338",
                     fg="#7dd87f" if ok else "#e8ecf5", anchor="w",
                     justify="left").pack(fill="x", padx=8, pady=3)

        def ai_rows(items):
            for t in items:
                if t["attempts"] >= t["max_attempts"]:
                    status = "今日次数已用完（最佳 %d 分，明天再来）" % t["best"]
                elif t["attempts"]:
                    status = "最佳 %d 分 · 第 %d/%d 次 · ≥%d 分得 %d 分钟" % (
                        t["best"], t["attempts"] + 1, t["max_attempts"], t["threshold"], t["minutes"])
                else:
                    if t.get("kind") == "words" and t.get("chars"):
                        detail = "（共 %d 词）" % t["chars"]
                    elif t.get("chars"):
                        detail = "（共 %d 字）" % t["chars"]
                    else:
                        detail = ""
                    status = "🎤 AI 评分%s · ≥%d 分得 %d 分钟" % (detail, t["threshold"], t["minutes"])
                row = tk.Frame(self.todo_inner, bg="#1a2338")
                row.pack(fill="x", padx=8, pady=3)
                tk.Label(row, text="%s\n%s" % (t["name"], status),
                         font=("Microsoft YaHei UI", sml), bg="#1a2338", fg="#cfd6e4",
                         justify="left", anchor="w").pack(side="left", fill="x", expand=True, pady=4)
                btn = tk.Button(row, text="🎤 开始任务", font=("Microsoft YaHei UI", sml, "bold"),
                                bg="#4a3a78", fg="white", activebackground="#3d3063",
                                activeforeground="white", relief="flat", cursor="hand2", padx=12,
                                command=lambda tid=t["id"]: self.launch_scorer(tid))
                if t["attempts"] >= t["max_attempts"]:
                    btn.configure(state="disabled", bg="#2a2a3a", disabledforeground="#77778f")
                btn.pack(side="right", padx=4)

        def media_rows(items, kind):
            """本地媒体：单文件各自一行；同子文件夹多集 → 合集行（从当前进度集继续，连续播放）"""
            prog = self._load_media_progress()
            colls, singles = {}, []
            for it in items:
                c = it.get("collection")
                if c:
                    colls.setdefault(c, []).append(it)
                else:
                    singles.append(it)
            kind_name = "视频" if kind == "watch" else "音频"
            def add_row(title, sub, it, btn_label):
                row = tk.Frame(self.todo_inner, bg="#1a2338")
                row.pack(fill="x", padx=8, pady=3)
                tk.Label(row, text="%s\n%s" % (title, sub),
                         font=("Microsoft YaHei UI", sml), bg="#1a2338", fg="#cfd6e4",
                         justify="left", anchor="w").pack(side="left", fill="x", expand=True, pady=4)
                tk.Button(row, text=btn_label, font=("Microsoft YaHei UI", sml, "bold"),
                          bg="#1f5c8b", fg="white", activebackground="#174a70",
                          activeforeground="white", relief="flat", cursor="hand2", padx=12,
                                      command=lambda m=dict(it): self._safe_open_media(m)).pack(side="right", padx=4)
            for it in singles:
                pos = int(it.get("pos") or 0)
                dur_info = (" · 时长约 %d 分钟" % round(it["duration_ms"] / 60000.0)) \
                    if it.get("duration_ms") else ""
                add_row(it["name"],
                        "本地%s%s · %s奖励 %d 分钟" % (kind_name, dur_info,
                            ("已看 %d:%02d，" % (pos // 60000, pos // 1000 % 60)) if pos else "看完后",
                            it["minutes"]),
                        it, "▶ 继续播放" if pos else ("▶ 开始观看" if kind == "watch" else "▶ 开始收听"))
            for c in sorted(colls):
                eps = sorted(colls[c], key=lambda x: x["name"])
                all_eps = sorted([m for m in getattr(self, "local_media", [])
                                  if m.get("collection") == c], key=lambda x: x["name"])
                idx = next((i + 1 for i, e in enumerate(all_eps) if e["id"] == eps[0]["id"]), 1)
                cur = eps[0]
                pos = int(cur.get("pos") or 0)
                add_row("合集「%s」 第%d/%d集：%s" % (c, idx, len(all_eps), cur["name"]),
                        "连续播放 %d 集 · 每集完成分别计奖" % len(eps), cur,
                        "▶ 继续播放" if pos else "▶ 播放合集")

        def empty_hint():
            tk.Label(self.todo_inner, text="（该分类暂无待完成任务）", font=("Microsoft YaHei UI", sml),
                     bg="#141b2b", fg="#5a6478").pack(anchor="w", padx=12, pady=10)

        if tab == "read":
            items = [t for t in todo_ai if t.get("kind") == "read"]
            if items:
                ai_rows(items)
            else:
                empty_hint()
        elif tab == "words":
            items = [t for t in todo_ai if t.get("kind") == "words"]
            if items:
                ai_rows(items)
            else:
                empty_hint()
        elif tab == "watch":
            items = self._media_todo("watch")
            if items:
                media_rows(items, "watch")
            else:
                empty_hint()
        else:
            items = self._media_todo("listen")
            if items:
                media_rows(items, "listen")
            else:
                empty_hint()
        # 普通分派任务（等家长打卡；媒体类型在视频/音频 Tab 里）——任何 Tab 底部都显示
        for (_tid, n, m, _earned, _kind) in getattr(self, "dispatch_view", []):
            if _kind in ("watch", "listen"):
                continue
            tk.Label(self.todo_inner, text="⏳ %s · 奖励 %d 分钟（等家长打卡）" % (n, m),
                     font=("Microsoft YaHei UI", sml), bg="#1a2338", fg="#8fa0bd",
                     anchor="w", justify="left").pack(fill="x", padx=8, pady=3)
        # 切换 Tab / 重建列表后回到顶部（否则短列表停留在旧滚动位置，看起来像空列表）
        try:
            self.todo_canvas.yview_moveto(0)
        except Exception:
            pass

    def _switch_todo_tab(self, t):
        """待办列表 Tab 切换（阅读/单词/视频/音频）。"""
        self._todo_tab = t
        self._todo_sig = None
        self.render()

    # ---- 解锁 HUD（屏幕顶部居中的浮窗，无法关闭）----
    def _hook_on(self):
        return not (self.demo or self.cfg["dry_run"])   # 测试模式不真的锁键盘

    def show_unlock(self):
        self._close_media_panel()     # 解锁前收起媒体面板（暂停并记住进度，音频不会在后台继续响）
        try:
            self.root.withdraw()      # 解锁后主程序收起（键盘/任务栏交还给系统）
        except tk.TclError:
            pass
        if self.hud is not None:
            try:
                if self.hud.winfo_exists():
                    return
            except tk.TclError:
                pass
            self.hud = None
        self.hud = tk.Toplevel(self.root)
        self.hud.overrideredirect(True)
        self.hud.attributes("-topmost", True)
        self.hud.attributes("-alpha", 0.9)
        self.hud.protocol("WM_DELETE_WINDOW", lambda: None)   # Alt+F4 也关不掉
        lbl = tk.Label(self.hud, text="", font=("Microsoft YaHei UI", 14, "bold"),
                       bg="#10141f", fg="#7ed957", padx=18, pady=8,
                       highlightthickness=1, highlightbackground="#39415a")
        lbl.pack()
        # 奖励轮：提供 ⏸ 暂停（保留剩余）/ ⏹ 结束（剩余退回钱包）——长奖励可分几次用
        if self.reward_ids is not None and self.end_time is not None:
            btns = tk.Frame(self.hud, bg="#10141f")
            btns.pack(pady=(0, 6))
            tk.Button(btns, text="⏸ 暂停并锁定", font=("Microsoft YaHei UI", 9, "bold"),
                      bg="#2a2a3a", fg="#c8d4e8", activebackground="#3a3a4a",
                      activeforeground="white", relief="flat", cursor="hand2", padx=8, pady=1,
                      command=self._pause_reward_use).pack(side="left", padx=4)
            tk.Button(btns, text="⏹ 结束退回", font=("Microsoft YaHei UI", 9, "bold"),
                      bg="#2a2a3a", fg="#c8d4e8", activebackground="#3a3a4a",
                      activeforeground="white", relief="flat", cursor="hand2", padx=8, pady=1,
                      command=self._end_reward_use).pack(side="left", padx=4)
        if self.demo:
            self.hud.bind("<Double-Button-1>", lambda e: self.root.destroy())  # 测试模式：双击浮窗退出
        sw = self.hud.winfo_screenwidth()
        self.hud.update_idletasks()
        w = max(self.hud.winfo_reqwidth(), 120)
        self.hud.geometry("+%d+8" % ((sw - w) // 2))    # 屏幕顶部居中
        self.tick_unlock()

    def tick_unlock(self):
        if self.mode != self.MODE_UNLOCKED:
            return
        self._eye_tick(time.time())
        if self.eye_until is not None:
            return                    # 护眼休息中：倒计时冻结（绿幕全屏覆盖）
        try:
            alive = self.hud is not None and bool(self.hud.winfo_exists())
        except tk.TclError:
            alive = False
        if not alive:                 # 浮窗被强制销毁 → 立即重建
            self.hud = None
            self.show_unlock()
        lb = self.hud.winfo_children()[0]
        if self.end_time is None:      # 家长临时解锁
            lb.configure(text="🛠️ 家长临时解锁中", fg="#ffd166")
            if self.demo:
                self.lb_title.configure(text="🛠️ 家长临时解锁中（家长通道）", fg="#ffd166")
        else:
            left = self.end_time - time.time()
            if left <= 0:
                self.finish_timeout()
                return
            prefix = "🎁 " if self.reward_ids is not None else ""
            lb.configure(text=prefix + "⏰ 本次可用 " + self.fmt_mmss(left), fg="#7ed957" if left > 60 else "#ff9f43")
            if left <= 60:
                self.hud.attributes("-alpha", 0.97)
            if self.demo:
                self.lb_title.configure(text="🟢 已解锁 · 剩余 " + self.fmt_mmss(left), fg="#7ed957")

    def tick_shutdown(self):
        if self.shutdown_deadline and time.time() >= self.shutdown_deadline:
            self.do_shutdown()
            if self.cfg["dry_run"]:
                self.render()
            return
        self.render()

    # ---------- 开机自启（HKCU Run 键） ----------
    def install_startup(self):
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pyw):
            pyw = sys.executable
        script = os.path.abspath(sys.argv[0])
        cmd = '"%s" "%s"' % (pyw, script)
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
        print("[install] 已写入开机自启: HKCU\\%s\\%s = %s" % (RUN_KEY, RUN_VALUE, cmd))
        self.log.write("[install] HKCU Run 键已写入: " + cmd)

    def remove_startup(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, RUN_VALUE)
            print("[remove] 已移除开机自启")
        except FileNotFoundError:
            print("[remove] 本来就没有自启动项")

    def _open_mic_test(self):
        """锁定页 🎤 试音：打开麦克风试音窗口（设备/音量/底噪诊断）。"""
        try:
            self._score_module().mic_test_app()
        except Exception as e:
            self.log.write("[mic-test] 打开失败 %r" % e)

    def _poll_interval(self):
        # 锁定时每 10 秒检查打卡（尽快解锁），解锁后按约定节奏轮询
        try:
            return self.cfg["poll_seconds_locked"] if self.mode != self.MODE_UNLOCKED else self.cfg["poll_seconds"]
        except Exception:
            return self.cfg["poll_seconds"]

    # ---------- 启动 ----------
    def run(self):
        if "--mic-test" in sys.argv:
            # 麦克风试音工具：设备选择/音量诊断/回放（家长排查"识别不到孩子声音"）
            try:
                self._score_module().mic_test_app()
            except Exception:
                import traceback
                print("[mic-test] FAIL\n" + traceback.format_exc())
            return
        if "--selftest-voice" in sys.argv:
            # 打包验收：验证冻结环境内 PCVoiceScore 可加载、评分组件完整（不启动界面）
            try:
                S = self._score_module()
                lang = (S.detect_lang({"content": ["think", "apple"]}) == "en"
                        and S.detect_lang({"content": "秋天的雨是一把钥匙"}) == "zh")
                w = S.word_score("think", "sink")        # 音标结构（pronouncing）
                py = S._py("读") is not None             # 拼音容错（pypinyin）
                self.log.write("[selftest] 语音组件 OK；语言判定=%s 音标评分=%s 拼音=%s（识别统一走 MiMo 云端）"
                               % (lang, w, py))
                print("[selftest] OK lang=%s phoneme=%s pinyin=%s" % (lang, w, py))
            except Exception:
                import traceback
                self.log.write("[selftest] FAIL\n" + traceback.format_exc())
                print("[selftest] FAIL（详见 guardian.log）")
            return
        if "--install" in sys.argv:
            self.install_startup(); return
        if "--remove" in sys.argv:
            self.remove_startup(); return
        if another_instance_running():
            # 单实例对所有模式生效：两个实例会互抢会话/台账文件（演示模式同样不允许）
            self.log.write("[start] 已有实例在运行，退出（如需重开请先停止旧实例）")
            if "--demo" in sys.argv:
                try:
                    import tkinter.messagebox as _mb
                    r = tk.Tk(); r.withdraw()
                    _mb.showinfo("PCGuardian", "儿童端已经在运行了，请先停止旧实例再启动。")
                    r.destroy()
                except Exception:
                    pass
            return

        self.blocker.start()
        Poller(self.cfg, self.log, self.q, self._poll_interval).start()
        self.build_ui()

        if self.mode == self.MODE_LOCKED:
            self.blocker.set_blocked(self._hook_on())
            self.render()
        self.root.after(500, self.poll_forever_ui)
        self.log.write("[start] PCGuardian 启动 demo=%s url=%s" % (self.demo, self.cfg["gist_url"]))
        self.root.mainloop()

def main():
    try:  # DPI 感知：全屏尺寸精确、字体清晰（失败则退回虚拟化坐标，不影响功能）
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    Guardian().run()

if __name__ == "__main__":
    main()
