# [public: true]
# [title: 莹莹棒取码助手]
# [author: 羽化]
# [version: 1.5]
# [description: 莹莹棒账号掉线主动通知 + 指令取微信登录二维码(带有效期) + 绑定管理]
# [platform: qq,web]
# [class: 工具类]
# [rule: ?]
# [param: {"required":false,"key":"yyb_bind.cmd_getcode","bool":false,"placeholder":"取码,扫码取码,重新取码","name":"取码指令","desc":"多个用英文逗号分隔；改完需与插件 rule 一致才生效"}]
# [param: {"required":false,"key":"yyb_bind.notify_text","bool":false,"placeholder":"【莹莹棒掉线提醒】账号 {nick} 已掉线","name":"掉线通知文案","desc":"支持占位符 {nick}=账号昵称、{openid}=账号标识"}]
# [param: {"required":false,"key":"yyb_bind.offshore_region_code","bool":false,"placeholder":"320000","name":"异地代理地区代码","desc":"异地取码用的短效代理地区，320000=江苏"}]
# [param: {"required":false,"key":"yyb_bind.offshore_region_name","bool":false,"placeholder":"江苏","name":"异地代理地区名","desc":"与地区代码配套显示"}]
# [param: {"required":false,"key":"yyb_bind.notify_extra","bool":false,"placeholder":"{\"X\":\"提示语\"}","name":"账号专属通知提示(JSON)","desc":"按账号昵称追加到掉线通知末尾，格式 {\"昵称\":\"追加文案\"}"}]
# [param: {"required":false,"key":"yyb_bind.recall_admin_qq","bool":false,"placeholder":"填你的QQ号","name":"撤回设置管理员QQ","desc":"只有这些QQ号能改「设置撤回」，多个用逗号分隔；留空则默认填你的QQ号"}]
# [param: {"required":false,"key":"yyb_bind.recall_seconds","bool":false,"placeholder":"60","name":"消息自动撤回(秒)","desc":"普通消息发出N分钟后自动撤回；掉线通知不撤回；填 0 关闭"}]# [param: {"required":false,"key":"yyb_bind.recall_minutes","bool":false,"placeholder":"3","name":"消息自动撤回(分钟)","desc":"普通消息发出N分钟后自动撤回；掉线通知不撤回；填 0 关闭"}]
# [param: {"required":false,"key":"yyb_bind.qr_recall","bool":false,"placeholder":"0","name":"二维码是否撤回(0不撤/1撤回)","desc":"登录二维码消息是否跟着自动撤回；默认0不撤，方便慢慢扫"}
# [param: {"required":false,"key":"yyb_bind.login_source","bool":false,"placeholder":"1","name":"默认登录渠道","desc":"1=应用宝 2=手游助手 3=微信小程序；「取码」按此渠道直出，「微信登录」会发菜单让你选"}]
# [param: {"required":false,"key":"yyb_bind.qr_valid_min","bool":false,"placeholder":"5","name":"二维码有效期(分钟)","desc":"仅用于提示文案；实际过期以微信服务端返回为准"}]
# [param: {"required":false,"key":"yyb_bind.yyb_base","bool":false,"placeholder":"http://127.0.0.1:18273","name":"莹莹棒地址","desc":"取码服务的基础地址"}]
# [param: {"required":false,"key":"yyb_bind.admin_qq","bool":false,"placeholder":"留空=不限","name":"管理员QQ","desc":"允许改配置的QQ号，多个用逗号分隔；留空则所有人可改"}]
# [param: {"required":false,"key":"yyb_bind.map","bool":false,"placeholder":"{\"openid或昵称\": {\"qq\": \"123456\", \"nick\": \"孙\"}}","name":"账号绑定表(JSON)","desc":"账号→QQ 的绑定关系，也可用QQ指令「设置绑定 昵称=QQ号」自动写入"}]
# [cron: * * * * *]
# [priority: 9999]
# [priority: 999]
# [disable: false]

# ============================================================================
# [PURE_LOGIN] 瘦身为纯微信登录中转: 通知判定/绑定/推送统一到莹莹棒服务端
# 莹莹棒取码助手（羽化面板 Python 插件 / from sillygirl import sender）
# NapCat 反向 WS 接羽化，QQ 消息进本插件；逻辑全在羽化，绕开 NapCat 插件白名单。
#
# 实测要点（勿改）：
#  1) 发图：本 SDK 无 replyImage → await _reply("[CQ:image,file=base64://<纯b64>]")
#     莹莹棒 /api/login/start 的 qrcodeDataUrl = "data:image/png;base64,xxx"，去前缀即成。
#  2) 主动私聊：sender 无 push/sendMsg → 原生 socket 手写 WS 连 NapCat 3001 发
#     send_private_msg（零依赖，2026-11-05 实测 NapCat 日志「发送 -> 私聊」成功）。
#  3) 配置：本地 JSON（不用 middleware.bucketGet），QQ 指令即可改。
#  4) cron：面板用 imtype=='fake' 假 sender 重跑脚本 → plat=='fake'/空 content 分流。
#  5) listen() 本机不可用 → 状态文件 + 多 rule 状态机；轮询用 asyncio.sleep + to_thread。
#  6) 验收：web_chat 的 data 常为空（异步），要看日志 [reply] 才算真回复。
# ============================================================================

import asyncio
import base64
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request

from sillygirl import sender

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, ".yyb_bind_config.json")
STATE_DIR = os.path.join(HERE, ".yyb_bind_states")
os.makedirs(STATE_DIR, exist_ok=True)

DEFAULTS = {
    "qq_user": "",
    "cmd_getcode": "取码,扫码取码,重新取码",
    "cmd_bind": "我的绑定,查绑定",
    "notify_text": "【莹莹棒掉线提醒】账号 {nick} 已掉线，回复 微信登录 可获取登录二维码重新扫码。",
    "qr_valid_min": "5",
    "yyb_base": "http://127.0.0.1:18273",
    "admin_qq": "填你的QQ号",
    "login_source": "1",
    "recall_minutes": "1",
    "qr_recall": "0",
    "notify_extra": "{\"X\": \"⚠️ 请提前打开 Tailscale 客户端并启用 VPN 连接后再扫码。\"}",
}

# 登录渠道: 1=应用宝(YYB) 2=手游助手(SYZS) 3=微信小程序(WMPF)
CHANNELS = {"1": (1, "应用宝"), "2": (2, "手游助手"), "3": (3, "微信小程序")}
CHANNEL_DEFAULT = "1"

BUCKET = "yyb_bind"

try:
    import middleware as _mw
except Exception:
    _mw = None

try:
    import recall_core          # 共享撤回模块（全局通用）
except Exception:
    recall_core = None

_CFG = None


def _load():
    global _CFG
    if _CFG is None:
        try:
            _CFG = json.load(open(CONFIG_FILE, encoding="utf-8"))
        except Exception:
            _CFG = {}
    return _CFG


def cfg(key):
    """优先读面板配置（bucket，[param:] 声明的项），空则回退本地文件/默认值。"""
    if _mw is not None:
        try:
            v = _mw.bucketGet(BUCKET, key)
            if v not in (None, ""):
                return str(v)
        except Exception:
            pass
    return _load().get(key) or DEFAULTS.get(key, "")


def set_cfg(key, value):
    """双写：面板 bucket + 本地文件（保证面板可改、QQ 指令也能改）。"""
    ok = False
    if _mw is not None:
        try:
            _mw.bucketSet(BUCKET, key, str(value))
            ok = True
        except Exception:
            pass
    d = _load()
    d[key] = str(value)
    try:
        json.dump(d, open(CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False)
        ok = True
    except Exception:
        pass
    return ok


def get_map():
    try:
        return json.loads(cfg("map") or "{}")
    except Exception:
        return {}


def set_map(m):
    return set_cfg("map", json.dumps(m, ensure_ascii=False))


# ---------------- 莹莹棒 HTTP（内网免鉴权） ----------------

def http_json(path, method="GET", body=None, timeout=15):
    base = cfg("yyb_base").rstrip("/")
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def list_accounts():
    try:
        return http_json("/api/accounts").get("accounts", []) or []
    except Exception:
        return []


def start_login(login_source=1, proxy_meta=None):
    # 注意：莹莹棒 /api/login/start 读的是【顶层】proxyMode/proxyRegionCode 字段
    # （shortproxy.resolve_params 解析请求体本身），不是嵌套 proxyMeta。
    body = {"loginSource": login_source}
    if proxy_meta:
        body.update(proxy_meta)
    return http_json("/api/login/start", "POST", body)


def poll_status(session_id):
    q = urllib.parse.urlencode({"sessionId": session_id})
    return http_json("/api/login/status?" + q)


def stop_login(session_id):
    try:
        return http_json("/api/login/stop", "POST", {"sessionId": session_id})
    except Exception:
        return {}


# ---------------- 主动私聊：原生 socket WS → NapCat 3001 ----------------

def onebot_send_private(user_id, message):
    """零依赖连 NapCat OneBot 正向 WS 发私聊。2026-11-05 实测可用。"""
    host, port = "127.0.0.1", 3001
    s = socket.socket()
    s.settimeout(6)
    try:
        s.connect((host, port))
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET / HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (host, port, key)
        )
        s.sendall(req.encode())
        if b"101" not in s.recv(4096):
            return False
        payload = json.dumps(
            {"action": "send_private_msg", "params": {"user_id": int(user_id), "message": message}},
            ensure_ascii=False,
        ).encode("utf-8")
        mask = os.urandom(4)
        n = len(payload)
        head = b"\x81"
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


# ---------------- 账号代理（异地号专用） ----------------

def offshore_proxy_meta():
    """异地号取码用的短效代理（默认 51池 江苏），可在数据存储里改。"""
    return {
        "proxyMode": "short",
        "proxyRegionCode": cfg("offshore_region_code") or "320000",
        "proxyRegionName": cfg("offshore_region_name") or "江苏",
    }


def _snapshot_proxies():
    """扫码前快照每个账号的代理配置，用于扫码后恢复（防被 direct 覆盖）。"""
    snap = {}
    for a in list_accounts():
        oid = a.get("openid") or ""
        if oid:
            snap[oid] = {
                "mode": a.get("proxyMode") or "direct",
                "code": a.get("proxyRegionCode") or "",
                "name": a.get("proxyRegionName") or "",
            }
    return snap


def _restore_proxy(openid, snap):
    """扫码成功后：账号原本有代理、却被写成 direct 的，写回原值。"""
    old = snap.get(openid) or {}
    mode = old.get("mode") or "direct"
    if not openid or mode in ("", "direct"):
        return False
    try:
        http_json("/api/accounts/proxy", "POST", {
            "openid": openid,
            "proxyMode": mode,
            "proxyRegionCode": old.get("code") or "",
            "proxyRegionName": old.get("name") or "",
        })
        return True
    except Exception:
        return False


# ---------------- 自动撤回 ----------------

RECALL_FILE = os.path.join(HERE, ".yyb_recall_queue.json")


def _q_load():
    try:
        return json.load(open(RECALL_FILE, encoding="utf-8"))
    except Exception:
        return []


def _q_save(lst):
    try:
        json.dump(lst[-300:], open(RECALL_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def onebot_delete_msg(message_id):
    """原生 socket WS 调 NapCat delete_msg。返回 True=服务器确认成功。"""
    host, port = "127.0.0.1", 3001
    try:
        mid = int(str(message_id).strip())
    except Exception:
        return False  # 非纯数字（web_xxx）不处理
    s = socket.socket()
    s.settimeout(8)
    try:
        s.connect((host, port))
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET / HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (host, port, key))
        s.sendall(req.encode())
        if b"101" not in s.recv(4096):
            return False
        payload = json.dumps({"action": "delete_msg", "params": {"message_id": mid}, "echo": "del"}).encode()
        mask = os.urandom(4)
        n = len(payload)
        head = b"\x81"
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        # 读响应，确认 retcode（跳过事件帧）
        for _ in range(6):
            hdr = s.recv(2)
            if len(hdr) < 2:
                return False
            ln = hdr[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", s.recv(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", s.recv(8))[0]
            data = b""
            while len(data) < ln:
                chunk = s.recv(ln - len(data))
                if not chunk:
                    break
                data += chunk
            try:
                r = json.loads(data.decode("utf-8", "ignore"))
            except Exception:
                continue
            if isinstance(r, dict) and "retcode" in r:
                return r.get("retcode") == 0
        return False
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


async def _reply(content, recall=True):
    """统一回复入口：登记到共享撤回队列 recall_core（掉线通知不走这里）。"""
    mid = await sender.reply(content)
    if recall and mid:
        try:
            if recall_core is not None:
                recall_core.notify(mid, "qr" if "[CQ:image" in str(content) else "normal")
            else:
                q = _q_load()
                q.append({"mid": str(mid), "ts": time.time()})
                _q_save(q)
        except Exception:
            pass
    return mid


def cron_recall():
    """cron：交给共享模块统一撤回（跨插件队列）。"""
    if recall_core is not None:
        try:
            recall_core.cron_recall(get_cfg=cfg, delete_fn=onebot_delete_msg)
            return
        except Exception:
            pass
    # 兜底：共享模块不可用时走旧逻辑
    try:
        minutes = int(float(cfg("recall_minutes") or "3"))
    except Exception:
        minutes = 3
    if minutes <= 0:
        return
    now = time.time()
    q = _q_load()
    if not q:
        return
    keep, due = [], []
    for it in q:
        if now - it.get("ts", 0) >= minutes * 60:
            due.append(it)
        else:
            keep.append(it)
    for it in due:
        ok = onebot_delete_msg(it.get("mid"))
        if not ok:
            it["tries"] = int(it.get("tries") or 0) + 1
            # QQ 撤回窗口 2 分钟：超时或重试 3 次就放弃，避免无限堆积
            age = now - it.get("ts", 0)
            if it["tries"] < 3 and age < 150:
                keep.append(it)
    _q_save(keep)


# ---------------- 状态文件（listen 不可用） ----------------

def _state_file(plat, uid):
    safe = "".join(c if c.isalnum() else "_" for c in "%s_%s" % (plat, uid))
    return os.path.join(STATE_DIR, safe + ".json")


def _get_state(plat, uid):
    f = _state_file(plat, uid)
    if os.path.exists(f):
        try:
            d = json.load(open(f, encoding="utf-8"))
            if time.time() - d.get("ts", 0) > 600:
                return {}
            return d
        except Exception:
            return {}
    return {}


def _set_state(plat, uid, **kw):
    d = dict(kw)
    d["ts"] = time.time()
    try:
        json.dump(d, open(_state_file(plat, uid), "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _clear_state(plat, uid):
    f = _state_file(plat, uid)
    if os.path.exists(f):
        try:
            os.remove(f)
        except Exception:
            pass


async def _get(fn):
    v = fn()
    if asyncio.iscoroutine(v):
        v = await v
    return v


# ---------------- 掉线主动通知（cron 每2分钟） ----------------

def _qq_for(openid, nick, account=None):
    """只在有绑定时返回 QQ；未绑定返回空 → 不推送。
    优先级：① 莹莹棒账号自带的 notifyQq（面板可设）② 本插件 map 的 openid 精确匹配
    ③ map 里「昵称当 key」的旧数据。绝不模糊包含匹配
    （2026-11-05 踩过：v.nick in nick 会让未绑定账号误命中别人的绑定）。"""
    if isinstance(account, dict):
        nq = str(account.get("notifyQq") or "").strip()
        if nq:
            return nq
    m = get_map()
    rec = m.get(openid) or {}
    if rec.get("qq"):
        return str(rec["qq"])
    rec = m.get(str(nick or "")) or {}
    if rec.get("qq") and rec.get("nick") in (None, "", str(nick)):
        return str(rec["qq"])
    return ""


def cron_check_accounts():
    # cron 现在每分钟触发（为了撤回精度），掉线检查节流到约 2 分钟一次
    try:
        last = float(cfg("last_check_ts") or 0)
    except Exception:
        last = 0
    if time.time() - last < 110:
        return
    set_cfg("last_check_ts", str(int(time.time())))
    prev = {}
    try:
        prev = json.loads(cfg("last_status") or "{}")
    except Exception:
        prev = {}
    now = {}
    notify = cfg("notify_text")
    try:
        for a in list_accounts():
            oid = a.get("openid", "")
            st = a.get("status", "")
            nick = a.get("nickname", "") or oid[:8]
            now[oid] = st
            old = prev.get(oid)
            if old in (None, "active") and st in ("error", "unknown"):
                qq = _qq_for(oid, nick, a)
                if qq:  # 只推已绑定；未绑定/管理员都不推
                    try:
                        msg = notify.replace("{nick}", nick).replace("{openid}", oid)
                        # 按账号追加专属提示（notify_extra 为 JSON: {"昵称": "追加文案"}）
                        try:
                            extra = json.loads(cfg("notify_extra") or "{}")
                            if isinstance(extra, dict):
                                tip = extra.get(nick) or extra.get(oid)
                                if tip:
                                    msg = msg + "\n" + str(tip)
                        except Exception:
                            pass
                        onebot_send_private(qq, msg)
                    except Exception:
                        pass  # 单条失败不影响其它账号
    finally:
        # 无论推送成功与否都要记状态，否则下一轮会重复轰炸
        set_cfg("last_status", json.dumps(now, ensure_ascii=False))


# ---------------- 取码 ----------------

def _cmd_list(key):
    return [x.strip() for x in cfg(key).replace("，", ",").split(",") if x.strip()]


def _is_recall_admin(uid):
    """撤回设置专用管理员：未单独配置时回退 admin_qq（默认 填你的QQ号）。"""
    adm = cfg("recall_admin_qq") or cfg("admin_qq") or "填你的QQ号"
    return str(uid).strip() in [x.strip() for x in str(adm).replace("，", ",").split(",") if x.strip()]


def _is_admin(uid):
    adm = cfg("admin_qq")
    return (not adm) or (str(uid) in [x.strip() for x in adm.split(",")])


def _to_base64_cq(data_url):
    if data_url.startswith("data:") and "," in data_url:
        return "[CQ:image,file=base64://%s]" % data_url.split(",", 1)[1]
    return "[CQ:image,file=%s]" % data_url


async def do_getcode(uid, plat, source=None, channel_name=None, proxy_meta=None):
    # 防重复①：本用户已有等待扫码的二维码 → 不再生成新的
    try:
        st0 = _get_state(plat, uid)
        if st0.get("waiting_qr") and st0.get("sid"):
            valid_sec = int(float(cfg("qr_valid_min") or 5)) * 60
            age = time.time() - float(st0.get("ts") or 0)
            left = int(valid_sec - age)
            if left > 15:
                await _reply(
                    "⏳ 你已有一个二维码在等待扫码（约剩 %d 秒），不再重复生成。\n"
                    "扫完我会自动通知你；想换渠道请先发 q 取消。" % left,
                    recall=False,
                )
                return
            _clear_state(plat, uid)
    except Exception:
        pass
    if source is None:
        source = int(cfg("login_source") or CHANNEL_DEFAULT)
        channel_name = CHANNELS.get(str(source), (source, "应用宝"))[1]
    tag = channel_name + ("·代理" if proxy_meta else "")
    await _reply("正在生成【%s】渠道的微信登录二维码，请稍候…" % tag)
    snap = await asyncio.to_thread(_snapshot_proxies)
    try:
        r = await asyncio.to_thread(start_login, source, proxy_meta)
    except Exception as e:
        await _reply("生成二维码失败：%s" % e)
        return
    if not r.get("success") or not r.get("qrcodeDataUrl"):
        await _reply("生成二维码失败：%s" % str(r)[:200])
        return

    sid = r.get("sessionId", "")
    valid_min = cfg("qr_valid_min") or "5"
    gen_time = time.strftime("%H:%M:%S")
    tip = (
        "【莹莹棒 登录二维码 · %s】\n"
        "生成时间：%s\n"
        "有效期：约 %s 分钟（以微信服务端为准，过期会提示「二维码已过期」）\n"
        "请用微信扫下面这张图，扫码后在手机上点「确认登录」。\n"
        "扫码成功后我会自动通知你；回复 q 可取消等待。" % (tag, gen_time, valid_min)
    )
    await _reply(tip)
    await _reply(_to_base64_cq(r["qrcodeDataUrl"]), recall=(str(cfg("qr_recall") or "0") == "1"))
    _set_state(plat, uid, waiting_qr=True, sid=sid, ts=time.time())

    deadline = time.time() + 300
    while time.time() < deadline:
        await asyncio.sleep(5)
        st = _get_state(plat, uid)
        if not st.get("waiting_qr"):
            return
        try:
            s = await asyncio.to_thread(poll_status, sid)
        except Exception:
            continue
        status = s.get("status")
        if status == "scanned":
            await _reply("已扫码，请在微信上点「确认登录」…")
            continue
        if status == "success":
            acc = s.get("account") or {}
            acct = acc.get("nickname", "")
            oid = acc.get("openid", "")
            restored = False
            if oid:
                restored = await asyncio.to_thread(_restore_proxy, oid, snap)
            # [PURE_LOGIN] 扫码成功 = 自动把当前 QQ 登记为莹莹棒该账号 notifyQq
            bound = False
            if oid and uid and str(uid).isdigit():
                try:
                    _r = await asyncio.to_thread(
                        http_json, "/api/accounts/notify", "POST",
                        {"openid": oid, "notifyQq": str(uid)})
                    bound = bool(_r and _r.get("success"))
                except Exception:
                    bound = False
            tip2 = "（账号代理已恢复）" if restored else ""
            tip3 = "（已登记失效通知 QQ）" if bound else ""
            await _reply("✅ 扫码登录成功%s，账号已恢复。%s%s" % ("（%s）" % acct if acct else "", tip2, tip3))
            _clear_state(plat, uid)
            return
        if status in ("expired", "cancelled", "rejected", "error"):
            await _reply("⚠️ 二维码已失效（%s）。需要重新登录请再发一次取码指令。" % status)
            _clear_state(plat, uid)
            return
        if s.get("running") is False and status is None:
            await _reply("⚠️ 扫码会话已结束。需要请再发一次取码指令。")
            _clear_state(plat, uid)
            return
    await _reply("⏰ 二维码等待超时（约5分钟），需要请再发一次取码指令。")
    _clear_state(plat, uid)


# ---------------- 主入口 ----------------

async def main():
    try:
        content = str(await _get(sender.getContent) or "").strip()
        uid = str(await _get(sender.getUserId) or "")
        plat = str(await _get(sender.getPlatform) or "")
    except Exception:
        content, uid, plat = "", "", ""

    # ★★ 30 秒超时兜底：任何消息进来(cron 或真人)都先扫一遍过期 pending,
    #    自动按 QQ 号 finalize + 主动私聊通知本人, 不依赖用户再发消息
    try:
        import ysp_remark
        for _uid, _msg in await asyncio.to_thread(ysp_remark.pending_sweep):
            try:
                onebot_send_private(_uid, "⏰ " + _msg)
            except Exception:
                pass
    except Exception:
        pass

    # [PURE_LOGIN] cron 触发（假 sender）——通知判定/推送已统一到莹莹棒服务端,
    # 羽化不再轮询掉线; 仅保留跨插件通用的撤回清理。
    if plat == "fake":
        cron_recall()
        return

    # ★★ 央视频「待备注」会话(按 QQ 分槽, 多人互不覆盖)：
    #    q=取消 / 空(回车/空白/两行空白)=默认QQ号 / 非空文字=备注
    #    处理完清自己这一槽 = 会话自动退出, 之后所有指令恢复正常
    try:
        import ysp_remark
        pend = ysp_remark.pending_get(uid)
        if pend:
            c = str(content).strip()
            if c.lower() == "q":
                ysp_remark.pending_clear(uid)
                await _reply("已取消央视频登录（本次 CK 未写入青龙）。", recall=False)
                return
            remark = c if c else str(uid)   # 空/回车/空白 → 默认 QQ 号
            msg = await asyncio.to_thread(
                ysp_remark.finalize, str(uid), plat,
                pend.get("ck", ""), pend.get("vuid", ""),
                pend.get("qid", ""), remark)
            ysp_remark.pending_clear(uid)
            await _reply(msg, recall=False)
            return
    except Exception:
        pass

    if not content:
        return  # 无备注态的真实空消息静默, 不抢答

    if content.lower() == "q":
        # ★ 央视频扫码等待中也要能取消（跨插件状态，读 ysp bucket）
        try:
            _ys = {}
            for _k in ("waiting_qr", "ysp.waiting_qr"):
                try:
                    _v = _mw.bucketGet("ysp", _k)
                    if _v:
                        _ys = json.loads(_v) if isinstance(_v, str) else (_v or {})
                        break
                except Exception:
                    continue
            if isinstance(_ys, dict) and _ys.get("uid") == str(uid):
                _mw.bucketSet("ysp", "ysp.waiting_qr", "{}")
                await _reply("已取消央视频扫码等待。", recall=False)
                return
        except Exception:
            pass
        st_q = _get_state(plat, uid)
        # 没进行中的操作 → 静默，交给别的插件（如酷狗登录的 q 退出）
        if not st_q:
            return
        if st_q.get("waiting_qr") and st_q.get("sid"):
            try:
                await asyncio.to_thread(stop_login, st_q["sid"])
            except Exception:
                pass
        _clear_state(plat, uid)
        await _reply("已退出当前操作。")
        return

    st = _get_state(plat, uid)

    if content in ("菜单", "功能", "取码帮助", "帮助"):
        tip = (
            "【莹莹棒取码助手】\n"
            "· 微信登录 → 选渠道：1应用宝 2手游助手 3微信小程序\n"
            "· 登录后会自动把当前 QQ 登记为该账号的失效通知接收人\n"
            "· q → 退出当前操作"
        )
        # 其他插件自动注册的指令（读 /data/plugins/.menu_registry.json，多插件共存）
        try:
            _lines = []
            _mf = os.path.join(HERE, ".menu_registry.json")
            if os.path.exists(_mf):
                _reg = json.load(open(_mf, encoding="utf-8"))
                if isinstance(_reg, dict):
                    for _pid in sorted(_reg.keys()):
                        _items = _reg.get(_pid)
                        if not isinstance(_items, list):
                            continue
                        for it in _items:
                            if isinstance(it, dict):
                                if it.get("admin") and not _is_admin(uid):
                                    continue
                                _t = str(it.get("text") or "")
                            else:
                                _t = str(it)
                            if _t and _t not in _lines:
                                _lines.append(_t)
            if _lines:
                tip += "\n\n【其他功能】\n" + "\n".join(_lines)
        except Exception:
            pass
        if _is_admin(uid):
            tip += (
                "\n\n【管理员】\n"
                "· 异地取码 → 走代理出码（外地号用）\n"
                "· 设置指令 取码 / 设置撤回 60（秒）\n"
                "· 设置二维码撤回 1 → 二维码是否跟着撤回（默认0不撤）\n"
                "\n当前：默认渠道 %s ｜ 撤回 %s 分钟 ｜ 管理员 %s"
            ) % (
                CHANNELS.get(str(cfg("login_source") or CHANNEL_DEFAULT), (1, "应用宝"))[1],
                ("%d 秒" % recall_core.get_seconds(cfg)) if recall_core is not None else (cfg("recall_minutes") or "1"),
                cfg("admin_qq") or "填你的QQ号",
            )
        await _reply(tip, recall=False)
        return

    # 「微信登录」→ 发渠道菜单
    if content == "微信登录":
        d = cfg("login_source") or CHANNEL_DEFAULT
        dn = CHANNELS.get(str(d), (d, "应用宝"))[1]
        _set_state(plat, uid, wait_channel=True)
        await _reply(
            "【选择微信登录渠道】\n"
            "1. 应用宝（推荐）\n"
            "2. 手游助手\n"
            "3. 微信小程序\n\n"
            "回复数字选择对应渠道的二维码；回复 q 退出。\n"
            "（默认渠道：%s）" % dn
        )
        return

    # 菜单状态收到数字 → 出对应渠道二维码；非菜单状态静默（不抢其他插件）
    if content in CHANNELS:
        if st.get("wait_channel"):
            _clear_state(plat, uid)
            src, cn = CHANNELS[content]
            await do_getcode(uid, plat, src, cn)
        return

    # 「异地取码」→ 应用宝 + 短效代理（给外地登录的号用，如 X）
    if content in ("异地取码", "代理取码", "外地取码"):
        await do_getcode(uid, plat, 1, "应用宝", offshore_proxy_meta())
        return

    # 「取码」等 → 按配置的默认渠道直出
    if content in _cmd_list("cmd_getcode"):
        await do_getcode(uid, plat)
        return

    # [PURE_LOGIN] 绑定/通知类指令已作废(统一到莹莹棒面板 notifyQq)


    # 其它消息静默，交给别的插件


asyncio.run(main())
