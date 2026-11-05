# -*- coding: utf-8 -*-
"""羽化插件 · 共享消息撤回模块（全局通用）

放在 /data/plugins/recall_core.py，任何插件都能 import。

接入方式（3 行）：
    import recall_core

    # ① 发消息后登记（kind: normal=普通 / qr=二维码 / never=永不撤）
    mid = await sender.reply(text)
    recall_core.notify(mid, "normal")

    # ② cron 里执行撤回（只让主插件跑，避免重复）
    recall_core.cron_recall(get_cfg=cfg, delete_fn=onebot_delete_msg)

    # ③ 指令处理（主插件处理即可，其他插件不用管）
    tip = recall_core.handle_command(content, uid, get_cfg=cfg,
                                     is_admin=_is_recall_admin, set_cfg=set_cfg)
    if tip is not None:
        await reply(tip, recall=False)
        return

共享配置（bucket 由调用方决定，本机用 yyb_bind）：
    recall_minutes   普通消息 N 分钟后撤回，0=关闭，默认 1
    qr_recall        登录二维码是否跟着撤回，0=不撤（默认）/ 1=撤

队列文件 /data/plugins/.recall_queue.json（跨插件共享）
"""
import json
import os
import time

QUEUE_FILE = "/data/plugins/.recall_queue.json"
MAX_ITEMS = 500
QQ_RECALL_LIMIT = 150          # QQ 私聊撤回硬上限约 2 分钟，留 30 秒余量


# ---------------- 队列 ----------------
def _load():
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            v = json.load(f)
            return v if isinstance(v, list) else []
    except Exception:
        return []


def _save(lst):
    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(lst)[-MAX_ITEMS:], f, ensure_ascii=False)
    except Exception:
        pass


def notify(mid, kind="normal"):
    """登记一条待撤回消息。kind: normal / qr / never"""
    if not mid or str(kind) == "never":
        return False
    try:
        q = _load()
        q.append({"mid": str(mid), "kind": str(kind), "ts": time.time()})
        _save(q)
        return True
    except Exception:
        return False


def stats():
    """队列概况（调试用）"""
    q = _load()
    now = time.time()
    return {
        "total": len(q),
        "normal": len([x for x in q if x.get("kind", "normal") == "normal"]),
        "qr": len([x for x in q if x.get("kind") == "qr"]),
        "oldest_sec": int(now - min([float(x.get("ts") or now) for x in q])) if q else 0,
    }


# ---------------- 配置判断 ----------------
def should_recall(get_cfg, kind="normal"):
    """这类消息该不该撤回"""
    if str(kind) == "never":
        return False
    try:
        if str(kind) == "qr":
            return str(get_cfg("qr_recall") or "0") == "1"
        return float(get_cfg("recall_minutes") or "1") > 0
    except Exception:
        return False


# ---------------- cron 撤回 ----------------
def cron_recall(get_cfg, delete_fn, log_fn=None):
    """撤回超时的消息。delete_fn(message_id) -> bool

    返回实际撤回条数。
    """
    try:
        minutes = float(get_cfg("recall_minutes") or "1")
    except Exception:
        minutes = 1.0
    q = _load()
    if not q:
        return 0

    qr_on = str(get_cfg("qr_recall") or "0") == "1"
    now = time.time()
    keep, done, dropped = [], 0, 0

    for it in q:
        mid = str(it.get("mid") or "")
        try:
            ts = float(it.get("ts") or 0)
        except Exception:
            ts = 0
        kind = str(it.get("kind") or "normal")
        age = now - ts

        # 二维码：开关关着就永远不撤，直接丢弃
        if kind == "qr" and not qr_on:
            dropped += 1
            continue
        # 普通消息：总开关关着就丢弃
        if kind == "normal" and minutes <= 0:
            dropped += 1
            continue
        # 还没到时间
        if age < minutes * 60:
            keep.append(it)
            continue
        # 超窗放弃（二维码不限，普通按 QQ 2 分钟硬窗口）
        if kind == "normal" and age > QQ_RECALL_LIMIT:
            dropped += 1
            continue
        # 只处理 QQ 数字 message_id
        if not mid.isdigit():
            dropped += 1
            continue
        try:
            if delete_fn(mid):
                done += 1
            else:
                keep.append(it)
        except Exception:
            keep.append(it)

    _save(keep)
    if log_fn and (done or dropped):
        try:
            log_fn("recall_core: 撤回 %d 条，丢弃 %d 条，剩余 %d" % (done, dropped, len(keep)))
        except Exception:
            pass
    return done


# ---------------- 指令 ----------------
def handle_command(content, uid, get_cfg, is_admin, set_cfg=None):
    """处理「设置撤回 / 设置二维码撤回」。命中返回回复文本，否则返回 None。"""
    c = str(content or "").strip()
    if not (c.startswith("设置撤回") or c.startswith("设置二维码撤回")):
        return None
    if not is_admin(uid):
        return "无权限修改撤回设置（仅限指定管理员 QQ）。"

    parts = c.split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    if c.startswith("设置二维码撤回"):
        if arg not in ("0", "1"):
            return ("格式：设置二维码撤回 1（撤回）/ 0（不撤回，默认）\n"
                    "二维码约 3 分钟有效，建议保持不撤回。")
        if set_cfg:
            set_cfg("qr_recall", arg)
        return "✅ 登录二维码消息：%s" % (
            "会跟随自动撤回" if arg == "1" else "不撤回（推荐，方便慢慢扫）")

    if not arg.isdigit():
        return "格式：设置撤回 1\n（分钟，填 0 关闭；掉线通知永远不撤回）"
    if set_cfg:
        set_cfg("recall_minutes", arg)
    warn = ""
    if int(arg) > 2:
        warn = "\n⚠️ 注意：QQ 撤回上限是 2 分钟，设超过 2 分钟会撤不回来（实测 185 秒失败）"
    return "✅ 消息自动撤回已设为 %s 分钟%s%s" % (
        arg, "（已关闭）" if arg == "0" else "", warn)


# ---------------- 帮助 ----------------
HELP_LINES = [
    "设置撤回 1           普通消息 N 分钟后撤回（0=关，QQ 上限 2 分钟）",
    "设置二维码撤回 0     登录二维码是否跟着撤回（0=不撤，默认）",
]
