# [public: true]
# [rule: ^央视频登录$]
# [rule: ^央视频扫码$]
# [rule: ^央视频重登$]
# [rule: ^央视频状态$]
# [rule: ^央视频查询$]
# [rule: ^央视频抽奖$]
# [rule: ^央视频抽$]
# [rule: ^央视频备注( .+)?$]
# [rule: ^央视频解绑( .+)?$]
# [rule: ^央视频菜单$]
# [rule: ^央视频帮助$]
# [title: 央视频助手]
# [author: 羽化]
# [version: 1.4.0]
# [description: 央视频扫码登录(自动写青龙变量+绑定QQ) + 抽奖查询 + 状态]
# [platform: qq,web]
# [cron: * * * * *]
# [class: 工具类]
# [priority: 10000]
# [param: {"required":false,"key":"ysp.ql_url","bool":false,"placeholder":"http://127.0.0.1:5700","name":"青龙地址","desc":"写变量的青龙面板地址"}]
# [param: {"required":false,"key":"ysp.ql_var_name","bool":false,"placeholder":"ysp","name":"青龙变量名","desc":"扫码后 CK 写入的变量名，多账号换行分隔"}]
# [param: {"required":false,"key":"ysp.task_id","bool":false,"placeholder":"2341","name":"抽奖任务ID","desc":"央视频抽奖要触发的青龙任务 ID"}]
# [param: {"required":false,"key":"ysp.admin_qq","bool":false,"placeholder":"填你的QQ号","name":"管理员QQ","desc":"可解绑他人、改配置"}]
# [param: {"required":false,"key":"ysp.qr_wait","bool":false,"placeholder":"180","name":"扫码等待秒数","desc":"二维码等待确认的最长秒数"}]
"""
央视频助手（羽化插件）
=====================
功能：
  1. 央视频登录 → 出二维码 → 手机央视频 APP 扫码 → 自动换 CK（可续期家族票）
     → 写入青龙变量 → 自动绑定到扫码人的 QQ
  2. 央视频抽奖 → 查本地抽奖记录；今天没记录就触发青龙任务跑一次 → 返回抽奖结果
  3. 央视频状态 / 解绑

依赖：纯标准库（urllib），无需 requests/第三方包
二维码：用在线 API 出图（容器内已实测可访问）
"""
import asyncio
import base64
import json
import os
import random
import string
import time
import urllib.error
import urllib.parse
import urllib.request

from sillygirl import sender

try:
    import recall_core          # 共享撤回模块（全局通用）
except Exception:
    recall_core = None

HERE = "/data/plugins"
CFG_FILE = os.path.join(HERE, ".ysp_cfg.json")
LOT_FILE = os.path.join(HERE, ".ysp_lottery.json")
STATE_FILE = os.path.join(HERE, ".ysp_state.json")
BUCKET = "ysp"

# ---------------- 央视频常量（来自 ysp.py 实测值） ----------------
H5 = "https://h5access.yangshipin.cn"
H5_QS = "vappid=59306155&vsecret=b42702bf7309a179d102f3d51b1add2fda0bc7ada64cb801&raw=1"
H5_APP_ID = "1400227916"
UA = ("Mozilla/5.0 (Linux; Android 10; MI 8 Build/QKQ1.190828.002; wv) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36")
TIMEOUT = 20

DEFAULTS = {
    "ql_url": "http://127.0.0.1:5700",
    "ql_client_id": "",
    "ql_client_secret": "",
    "ql_var_name": "YSP_COOKIE",
    "task_id": "2341",
    "admin_qq": "",
    "qr_wait": "180",
    "map": "{}",
}

# 中奖后的领取指引（只在中奖结果里追加）
LOT_TIP = "\n\n📦 领取方式：我的 → 积分 → 签到 → 右上角「我的奖品」"


# ---------------- 配置（bucket 优先，本地文件兜底） ----------------
_MW = None
try:
    import middleware as _MW  # 羽化 AutMan 中间件
except Exception:
    _MW = None

_CFG = {}


def _load_local():
    global _CFG
    try:
        _CFG = json.load(open(CFG_FILE, encoding="utf-8"))
    except Exception:
        _CFG = {}


def cfg(key):
    if _MW is not None:
        for k in (key, "ysp." + key):   # 兼容面板 param 写入的带前缀 key
            try:
                v = _MW.bucketGet(BUCKET, k)
                if v not in (None, ""):
                    return str(v)
            except Exception:
                pass
    _load_local()
    return str(_CFG.get(key, DEFAULTS.get(key, "")))


def set_cfg(key, value):
    _load_local()
    _CFG[key] = value
    try:
        json.dump(_CFG, open(CFG_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    if _MW is not None:
        try:
            _MW.bucketSet(BUCKET, key, str(value))
        except Exception:
            pass


def get_map():
    try:
        m = json.loads(cfg("map") or "{}")
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def set_map(m):
    set_cfg("map", json.dumps(m, ensure_ascii=False))


def _is_admin(uid):
    adm = cfg("admin_qq") or ""
    return str(uid).strip() in [x.strip() for x in str(adm).replace("，", ",").split(",") if x.strip()]


# ---------------- HTTP（纯 urllib） ----------------
def http_json(url, method="GET", body=None, headers=None, timeout=TIMEOUT):
    data = None
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return json.loads(raw.decode("utf-8", "ignore"))
            except Exception:
                return {"_raw": raw[:400].decode("utf-8", "ignore")}
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "ignore"))
        except Exception:
            return {"_http_error": e.code}
    except Exception as e:
        return {"_error": "%s: %s" % (type(e).__name__, e)}


def http_bytes(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------- 央视频扫码登录 ----------------
def new_guid():
    return "".join(random.choice(string.hexdigits.lower()) for _ in range(32))


def _h5_qs(guid, vplatform="3"):
    q = dict(x.split("=") for x in H5_QS.split("&"))
    q.update({"guid": guid, "vplatform": str(vplatform)})
    return urllib.parse.urlencode(q)


def ysp_get_qrcode():
    """取登录二维码 → (qrCodeID, qrCodeURL, guid) 或抛异常"""
    guid = new_guid()
    url = "%s/web/scanlogin/get_qrcode?%s" % (H5, _h5_qs(guid))
    d = http_json(url, headers={"Referer": "https://m.yangshipin.cn/login"})
    data = d.get("data") or {}
    qid = str(data.get("qrCodeID") or "")
    qurl = data.get("qrCodeURL") or ""
    if str(data.get("code")) not in ("0", "None") or not qid:
        raise RuntimeError("取二维码失败: %s" % json.dumps(d, ensure_ascii=False)[:200])
    return qid, qurl, guid


def ysp_poll_state(qid, guid):
    """轮询扫码状态 → (state, loginToken)；state: 0待扫 1已扫 2确认 3失效 4已用 5完成"""
    url = "%s/web/scanlogin/qrcode_state_poll?%s" % (H5, _h5_qs(guid))
    d = http_json(url, "POST", {"qrCodeID": qid},
                  headers={"Referer": "https://m.yangshipin.cn/login", "x-requested-with": "com.cctv.yangshipin.app.androidp"})
    data = d.get("data") or {}
    return data.get("qrCodeState"), str(data.get("loginToken") or "")


def ysp_auth_login(qid, token, guid):
    """用 loginToken 换正式票据（eTokenType=140 + appid=qrCodeID = 可续期家族票）"""
    body = {
        "curLoginTokenList": [{
            "eTokenType": 140,
            "stOauthTokenInfo": {"strAppid": qid, "strCode": token},
        }],
        "stDevInfo": {"wDevPlatType": "", "pushToken": "", "guid": guid, "devid": "", "strFromInfo": ""},
    }
    d = http_json("%s/web/auth_login?%s" % (H5, _h5_qs(guid)), "POST", body, headers={
        "Referer": "https://m.yangshipin.cn/login",
        "x-requested-with": "com.cctv.yangshipin.app.androidp",
    })
    data = d.get("data") or {}
    inner = data.get("innerToken") or {}
    ec = data.get("errCode")
    if str(d.get("ret")) not in ("0", "None") or str(ec) not in ("0", "None") or not inner.get("lUserid"):
        raise RuntimeError("换票失败 errCode=%s %s" % (ec, data.get("strErrMsg") or ""))
    return data


def canon_cookie(ck):
    """规范化 CK：别名归一 + 同名去重（后值覆盖）"""
    alias = {"accesstoken": "access_token", "refreshtoken": "refresh_token"}
    d = {}
    for seg in str(ck).replace("\n", ";").split(";"):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        k, _, v = seg.partition("=")
        k = alias.get(k.strip(), k.strip())
        if v.strip():
            d[k] = v.strip()
    return "; ".join("%s=%s" % (k, v) for k, v in d.items() if v)


def cookie_from_login(data, qid):
    """从 auth_login 响应组 CK，并强制 appid=本次 qrCodeID（否则续不了期）"""
    inner = data.get("innerToken") or {}
    oauth = (data.get("userTokenInfo") or {}).get("stOauthTokenInfo") or {}
    ck = {
        "vuserid": str(inner.get("lUserid") or ""),
        "vusession": inner.get("strVideotoken") or "",
        "access_token": oauth.get("strAccesstoken") or "",
        "refresh_token": oauth.get("strRefreshtoken") or "",
        "appid": qid,                      # ★ 必须写 qrCodeID
        "openid": oauth.get("strOpenid") or "",
        "guid": data.get("_guid") or new_guid(),
        "logintype": "140",
        "vplatform": "3",
    }
    return canon_cookie("; ".join("%s=%s" % (k, v) for k, v in ck.items() if v))


def ck_uid(ck):
    """从 CK 里取 vuserid 当账号标识"""
    for seg in ck.split("; "):
        if seg.startswith("vuserid="):
            return seg.split("=", 1)[1]
    return ""


# ---------------- 二维码图片（在线 API） ----------------
def qr_image_b64(text):
    """把任意文本/URL 转成二维码 PNG 的 base64（不带 data: 前缀）"""
    q = urllib.parse.quote(text, safe="")
    last = None
    for api in ("https://api.qrserver.com/v1/create-qr-code/?size=420x420&data=" + q,
                "https://api.pwmqr.com/qrcode/create/?url=" + q):
        try:
            raw = http_bytes(api, timeout=15)
            if raw[:4] == b"\x89PNG" or len(raw) > 800:
                return base64.b64encode(raw).decode()
        except Exception as e:
            last = e
    raise RuntimeError("二维码生成失败: %s" % last)


def to_cq_image(b64):
    return "[CQ:image,file=base64://%s]" % b64


# ---------------- 青龙 ----------------
_QL_TOK = {"v": "", "ts": 0}


def ql_token(force=False):
    if not force and _QL_TOK["v"] and time.time() - _QL_TOK["ts"] < 1800:
        return _QL_TOK["v"]
    base = cfg("ql_url").rstrip("/")
    url = "%s/open/auth/token?client_id=%s&client_secret=%s" % (
        base, cfg("ql_client_id"), cfg("ql_client_secret"))
    d = http_json(url)
    tok = ((d.get("data") or {}).get("token") or "")
    if tok:
        _QL_TOK["v"] = tok
        _QL_TOK["ts"] = time.time()
    return tok


def ql_api(method, path, body=None):
    base = cfg("ql_url").rstrip("/")
    tok = ql_token()
    if not tok:
        tok = ql_token(force=True)
    if not tok:
        raise RuntimeError("青龙鉴权失败，检查 ql_client_id / ql_client_secret")
    return http_json(base + path, method, body, headers={"Authorization": "Bearer " + tok})


def ql_set_var(name, value):
    """写/更新青龙变量（不存在则新建）"""
    d = ql_api("GET", "/open/envs?searchValue=" + urllib.parse.quote(name))
    items = d.get("data") or []
    exist = None
    for e in items:
        if str(e.get("name")) == name:
            exist = e
            break
    if exist:
        eid = exist.get("id")
        r = ql_api("PUT", "/open/envs", {"id": eid, "name": name, "value": value})
        return "updated", eid, r
    r = ql_api("POST", "/open/envs", [{"name": name, "value": value, "remarks": "央视频助手写入"}])
    eid = None
    data = r.get("data")
    if isinstance(data, list) and data:
        eid = data[0].get("id")
    elif isinstance(data, dict):
        eid = data.get("id")
    return "created", eid, r


def ql_get_var(name):
    """读青龙环境变量值（读不到返回空串）"""
    try:
        d = ql_api("GET", "/open/envs?searchValue=" + urllib.parse.quote(name))
        for e in (d.get("data") or []):
            if str(e.get("name")) == name:
                return str(e.get("value") or "")
    except Exception:
        pass
    return ""


def ql_run_task(task_id):
    # ★ 青龙 OpenAPI 收「裸数组」，不是 {"ids":[...]}（传错报 400 Validation failed）
    return ql_api("PUT", "/open/crons/run", [int(task_id)])


def ql_task_log(task_id):
    d = ql_api("GET", "/open/crons/%s/log" % task_id)
    return str(d.get("data") or "")


# ---------------- 抽奖记录 ----------------
def lot_load():
    try:
        return json.load(open(LOT_FILE, encoding="utf-8"))
    except Exception:
        return {}


def lot_save(d):
    try:
        json.dump(d, open(LOT_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def lot_today(uid_key):
    d = lot_load()
    rec = d.get(str(uid_key)) or {}
    if rec.get("date") == time.strftime("%Y-%m-%d"):
        return rec
    return None


def lot_set(uid_key, text):
    d = lot_load()
    d[str(uid_key)] = {"date": time.strftime("%Y-%m-%d"), "ts": int(time.time()), "text": text}
    lot_save(d)


def extract_lottery(log):
    """从日志提取「结论」（奖品 / 次数 / 积分变化），不给日志流水"""
    out = []
    for ln in str(log or "").split("\n"):
        t = ln.strip()
        if not t:
            continue
        # 具体结果：抽奖 3 次：宠粉券（付邮）、刮痧板+拨筋棒（实奖）、宠粉券
        m = re.search(r"抽奖\s*(\d+)\s*次[：:]\s*(.+)$", t)
        if m:
            out.append("🎁 抽了 %s 次：%s" % (m.group(1), m.group(2).strip()))
            continue
        # 次数用完 / 跳过
        m2 = re.search(r"抽奖[：:]\s*(.+)$", t)
        if m2 and ("用完" in t or "跳过" in t):
            out.append("ℹ️ 抽奖：%s" % m2.group(1).strip())
            continue
        # 积分变化
        m3 = re.search(r"积分\s*(\d+)\s*→\s*(\d+)", t)
        if m3:
            d = int(m3.group(2)) - int(m3.group(1))
            out.append("💰 积分 %s → %s（%+d）" % (m3.group(1), m3.group(2), d))
    seen = []
    for x in out:
        if x not in seen:
            seen.append(x)
    return seen[:8]


def _log_is_today(log, today):
    """青龙任务日志首行是 '## 开始执行... 2026-11-06 02:06:38'"""
    try:
        for ln in str(log or "").split("\n")[:3]:
            if today in ln:
                return True
    except Exception:
        pass
    return False


# ---------------- 业务 ----------------
async def reply(text, recall=True):
    """统一回复：登记到共享撤回队列（CQ:image 自动识别为二维码，按 qr_recall 开关）"""
    mid = await sender.reply(text)
    if recall and mid:
        try:
            if recall_core is not None:
                recall_core.notify(mid, "qr" if "[CQ:image" in str(text) else "normal")
        except Exception:
            pass
    return mid


async def do_login(uid, plat):
    await reply("🎬 正在获取央视频登录二维码…")
    try:
        qid, qurl, guid = await asyncio.to_thread(ysp_get_qrcode)
    except Exception as e:
        await reply("❌ 取二维码失败：%s" % e)
        return
    if not qurl:
        await reply("❌ 服务端没返回二维码地址")
        return
    try:
        b64 = await asyncio.to_thread(qr_image_b64, qurl)
        await reply(to_cq_image(b64))
    except Exception as e:
        await reply("二维码图片生成失败（%s），请手动打开：\n%s" % (e, qurl))
    await reply(
        "【央视频登录】\n"
        "请用手机「央视频」APP 扫上面二维码，扫完在手机上点确认。\n"
        "💡 不方便直接扫的话：**先把二维码截图保存，再打开央视频 App → 扫码 → 右上角「相册」，选中这张截图即可**。\n"
        "二维码约 3 分钟有效；回复 q 可取消等待。",
        recall=False,
    )
    set_cfg("waiting_qr", json.dumps({"uid": str(uid), "plat": plat, "qid": qid, "guid": guid, "ts": time.time()}))

    deadline = time.time() + float(cfg("qr_wait") or 180)
    last = None
    token = ""
    while time.time() < deadline:
        await asyncio.sleep(2)
        try:
            st = _state()
            if not st.get("uid"):
                return          # waiting_qr 被 q 清空 → 静默退出（q 那边已回复）
        except Exception:
            pass
        try:
            state, tok = await asyncio.to_thread(ysp_poll_state, qid, guid)
        except Exception:
            continue
        if state != last:
            last = state
            if state == 1:
                await reply("📱 已扫码，请在手机上点「确认登录」…")
            elif state == 2 or state == 5:
                token = tok
                if token:
                    break
            elif state == 3:
                set_cfg("waiting_qr", "{}")
                await reply("⌛ 二维码已失效，请重新发送「央视频登录」。")
                return
            elif state == 4:
                set_cfg("waiting_qr", "{}")
                await reply("⚠️ 二维码已被使用过，请重新发送「央视频登录」。")
                return
        if state == 2 and tok:
            token = tok
            break
    if not token:
        set_cfg("waiting_qr", "{}")
        await reply("⌛ 等超时了没拿到确认结果（最后状态 %s），请重发「央视频登录」。" % last)
        return

    try:
        data = await asyncio.to_thread(ysp_auth_login, qid, token, guid)
        data["_guid"] = guid
        ck = cookie_from_login(data, qid)
    except Exception as e:
        set_cfg("waiting_qr", "{}")
        await reply("❌ 扫码成功但换票失败：%s" % e)
        return

    # 写青龙变量
    var = cfg("ql_var_name") or "ysp"
    act, eid, r = "?", None, {}
    try:
        act, eid, r = await asyncio.to_thread(ql_set_var, var, ck)
    except Exception as e:
        await reply("⚠️ CK 已拿到但写青龙变量失败：%s\n（可手动复制，见下条）" % e)

    # 绑定到扫码人 QQ
    m = get_map()
    vuid = ck_uid(ck)
    _old = m.get(str(uid)) or {}
    m[str(uid)] = {"nick": "央视频%s" % (vuid[-6:] if vuid else "user"), "vuserid": vuid,
                   "ck": ck, "ts": int(time.time()),
                   # ★ 备注默认就是 QQ 号；重新扫码时保留用户自己改过的备注
                   "remark": _old.get("remark") or str(uid)}
    set_map(m)
    set_cfg("waiting_qr", "{}")

    await reply(
        "✅ 央视频登录成功\n"
        "🔄 票据类型：logintype=140 + appid=%s（App 家族票，可自动续期）\n"
        "🟢 已写入青龙变量：%s（%s）\n"
        "🔗 已绑定到你的 QQ：以后「央视频抽奖/状态」操作的就是这个号\n"
        "%s" % (qid[:8] + "…", var, act, ("（变量ID %s）" % eid) if eid else ""),
        recall=False,
    )

    # ★ 登录成功 → 后台启用并跑一次，不在这里发日志（用户要求：扫码后只提示稍后查询）
    try:
        tid = int(cfg("task_id") or "2341")
    except Exception:
        tid = 2341
    try:
        await asyncio.to_thread(ql_api, "PUT", "/open/crons/enable", [tid])
        await asyncio.to_thread(ql_api, "PUT", "/open/crons/run", [tid])
        await reply(
            "🎬 已在后台开始跑今日积分任务（签到 / 任务 / 领积分 / 抽奖）。\n"
            "⏳ 约 5~8 分钟跑完，**稍后发「央视频抽奖」查看结果**（只看你自己绑定的账号）。",
            recall=False,
        )
    except Exception as e:
        await reply("⚠️ 积分任务触发失败：%s\n（可到青龙面板手动运行 ID %s）" % (e, tid))


def _state():
    try:
        return json.loads(cfg("waiting_qr") or "{}")
    except Exception:
        return {}


async def do_status(uid, plat):
    m = get_map()
    rec = m.get(str(uid))
    if not rec:
        await reply("你还没有绑定央视频账号。\n发「央视频登录」扫码绑定。")
        return
    lot = lot_today(rec.get("vuserid") or str(uid))
    lines = [
        "【央视频账号状态】",
        "· 绑定 QQ：%s" % uid,
        "· 备注：%s%s" % (rec.get("remark") or uid,
                          "" if rec.get("remark") else "（默认QQ号，发「央视频备注 名字」可改）"),
        "· vuserid：%s" % (rec.get("vuserid") or "?"),
        "· 绑定时间：%s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(rec.get("ts") or 0)),
        "· CK：%s…（%d 字符）" % ((rec.get("ck") or "")[:40], len(rec.get("ck") or "")),
        "· 今日抽奖：%s" % (("已抽（%s）" % (lot.get("text") or "")[:60]) if lot else "未抽"),
    ]
    await reply("\n".join(lines), recall=False)


def _filter_own_lines(log, vuserid):
    """从任务日志里只提取属于本账号的抽奖/积分行。

    单账号：日志里没有 '账号N' 分段标记 → 直接返回抽奖相关行。
    多账号：青龙日志按 '[x/y 账号N]' 分段，找到含本 vuserid 的段，只取该段。
    """
    try:
        lines = [ln for ln in str(log or "").split("\n") if ln.strip()]
        if not lines:
            return []
        # 找分段标记
        seg_idx = [i for i, ln in enumerate(lines) if "账号" in ln and "][" in ln]
        if len(seg_idx) <= 1:
            return extract_lottery(log)          # 单账号：全归他
        # 多账号：逐段判断哪个段属于本账号
        bounds = seg_idx + [len(lines)]
        for k in range(len(seg_idx)):
            seg = lines[bounds[k]:bounds[k + 1]]
            seg_text = "\n".join(seg)
            if vuserid and str(vuserid) in seg_text:
                return extract_lottery(seg_text)
        # 日志里没有 vuserid 字样 → 保守起见只返回第一段（通常就是触发者自己的）
        return extract_lottery("\n".join(lines[bounds[0]:bounds[1]]))
    except Exception:
        return extract_lottery(log)


async def do_lottery(uid, plat):
    m = get_map()
    rec = m.get(str(uid))
    if not rec:
        await reply("你还没有绑定央视频账号。\n先发「央视频登录」扫码绑定，再来抽奖。")
        return
    key = rec.get("vuserid") or str(uid)

    # ① ★ 优先读青龙变量 YSP_LAST_LOTTERY（ysp.py 每次跑完写入，不受"最新日志"限制）
    #    格式: 2026-11-06 01:59|抽奖 3 次：xxx、yyy|45→55
    try:
        raw = await asyncio.to_thread(ql_get_var, "YSP_LAST_LOTTERY")
        if raw and raw.startswith(time.strftime("%Y-%m-%d")):
            parts = str(raw).split("|")
            if len(parts) >= 2 and parts[1].strip():
                when = parts[0].strip()
                result = parts[1].strip()
                pts = parts[2].strip() if len(parts) > 2 else ""
                text = "🎁 %s" % result
                if pts and "→" in pts:
                    a, b = pts.split("→", 1)
                    text += "\n💰 积分 %s → %s" % (a.strip(), b.strip())
                if "次：" in result:
                    text += LOT_TIP
                lot_set(key, text)
                await reply(
                    "【央视频抽奖 · 今日结果】\n%s\n\n时间：%s\n（今天已经跑过了，不会重复跑）" % (text, when),
                    recall=False,
                )
                return
    except Exception:
        pass

    # ② 插件自己记录过今天的结果 → 直接返回，不跑任务
    hit = lot_today(key)
    if hit:
        _ht = hit.get("text") or "（无详细记录）"
        if "次：" in _ht and LOT_TIP.strip() not in _ht:
            _ht += LOT_TIP
        await reply(
            "【央视频抽奖 · 今日结果】\n"
            "%s\n"
            "（今天 %s 跑的，不会重复跑）" % (_ht,
                                        time.strftime("%H:%M", time.localtime(hit.get("ts") or 0))),
            recall=False,
        )
        return

    task_id = cfg("task_id") or "2341"

    # ② 先读青龙「最新一次」日志：如果是今天跑的，直接给结果，不重复触发
    log = ""
    try:
        log = await asyncio.to_thread(ql_task_log, task_id)
    except Exception:
        log = ""
    if log and _log_is_today(log, time.strftime("%Y-%m-%d")):
        picked = _filter_own_lines(log, key)
        if picked:
            text = "\n".join(picked)
            lot_set(key, text)
            _tip = LOT_TIP if any("次：" in x for x in picked) else ""
            await reply(
                "【央视频抽奖 · 今日结果】\n%s%s\n\n（今天的任务已经跑过了，这里不重复跑）" % (text, _tip),
                recall=False,
            )
            return
        await reply(
            "📋 今天已经跑过一次了，但日志里还没有抽奖结果。\n"
            "等几分钟再发「央视频抽奖」（不会重复跑任务）。",
            recall=False,
        )
        return

    # ③ 今天确实没跑过 → 触发一次
    await reply("🎰 今天还没跑过，正在触发青龙任务（ID %s），约 1 分钟…" % task_id)
    try:
        r = await asyncio.to_thread(ql_run_task, task_id)
        if str(r.get("code")) not in ("200", "None"):
            await reply("❌ 触发失败：%s" % json.dumps(r, ensure_ascii=False)[:200])
            return
    except Exception as e:
        await reply("❌ 触发青龙任务失败：%s" % e)
        return

    log = ""
    for _ in range(30):
        await asyncio.sleep(5)
        try:
            log = await asyncio.to_thread(ql_task_log, task_id)
        except Exception:
            continue
        if "开始执行" in log and ("耗时" in log or "失败" in log or "成功" in log):
            break
    if not log:
        await reply("⚠️ 任务已触发但读不到日志，稍后到青龙面板看 ID %s 的运行结果。" % task_id)
        return

    picked = _filter_own_lines(log, key)
    text = "\n".join(picked) if picked else "（没有抽到记录，或本次未中奖）"
    lot_set(key, text)
    _tip = LOT_TIP if any("次：" in x for x in picked) else ""
    await reply(
        "【央视频抽奖 · 你绑定的账号】\n"
        "%s%s\n\n"
        "（只显示你绑定账号的结果，查不到别人的）" % (text, _tip),
        recall=False,
    )


async def do_unbind(uid, plat, arg=""):
    if not _is_admin(uid):
        await reply("❌ 无权限解绑（仅管理员可操作）。\n你可以发「央视频状态」查看自己的绑定。")
        return
    target = str(arg).strip() or str(uid)
    m = get_map()
    if target not in m:
        await reply("没有这个绑定：%s\n（发「央视频状态」可查看自己的绑定）" % target)
        return
    m.pop(target, None)
    set_map(m)
    await reply("✅ 已解绑 %s（青龙变量里的 CK 没动）。" % ("你的账号" if target == str(uid) else "QQ " + target))


async def do_menu(uid, plat):
    lines = [
        "【央视频助手】",
        "· 央视频登录 → 出二维码，手机央视频 APP 扫码（免短信免滑块）",
        "· 央视频状态 → 看绑定账号 / 今日抽奖情况",
        "· 央视频抽奖 → 今天没抽就自动跑一次，返回抽奖结果",
        "· 央视频备注 名字 → 给绑定加备注（状态里能看到）",
        "· 央视频解绑 → 解绑你的账号",
        "· q → 取消当前操作",
        "",
        "扫码成功后 CK 会自动写入青龙变量「%s」，并绑定你的 QQ。" % (cfg("ql_var_name") or "ysp"),
    ]
    await reply("\n".join(lines), recall=False)


async def _get(fn):
    v = fn()
    if asyncio.iscoroutine(v):
        v = await v
    return v


PLUGIN_ID = "ysp"   # 菜单注册表的唯一标识，别和别的插件重名


async def _register_menu():
    """把自己注册到共享菜单注册表（莹莹棒的「菜单」会自动聚合展示）

    格式（每个插件一个槽，互不覆盖）：
      yyb_bind.menu_registry = {
        "ysp": [{"text": "...", "admin": false}, ...],
        "其他插件id": [...]
      }
    """
    items = [
        {"text": "· 央视频登录 → 扫码登录央视频（自动写青龙变量+绑定QQ）", "admin": False},
        {"text": "· 央视频状态 → 查看绑定账号 / 今日抽奖", "admin": False},
        {"text": "· 央视频抽奖 → 今天没抽就自动跑一次", "admin": False},
        {"text": "· 央视频备注 名字 → 给绑定加备注（好区分）", "admin": False},
        {"text": "· 央视频解绑 [QQ号] → 解绑账号（仅管理员）", "admin": True},
    ]
    # ① 写共享文件（莹莹棒菜单直接读这个，最可靠）
    cur = {}
    try:
        mf = os.path.join(HERE, ".menu_registry.json")
        if os.path.exists(mf):
            try:
                cur = json.load(open(mf, encoding="utf-8"))
            except Exception:
                cur = {}
        if not isinstance(cur, dict):
            cur = {}
        cur[PLUGIN_ID] = items
        json.dump(cur, open(mf, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    # ② 同时写 bucket（面板可查，失败不影响）
    try:
        if _MW is not None:
            _MW.bucketSet("yyb_bind", "menu_registry", json.dumps(cur, ensure_ascii=False))
    except Exception:
        pass


async def main():
    try:
        await _register_menu()
    except Exception:
        pass
    try:
        content = str(await _get(sender.getContent) or "").strip()
        uid = str(await _get(sender.getUserId) or "")
        plat = str(await _get(sender.getPlatform) or "")
    except Exception:
        return
    if not content:
        return  # cron 触发，不做任何事（本插件无定时任务）

    if content.lower() == "q":
        st = _state()
        if st.get("uid"):
            set_cfg("waiting_qr", "{}")
            await reply("已取消央视频扫码等待。")
        return

    if content in ("央视频菜单", "央视频帮助"):
        await do_menu(uid, plat)
        return
    if content in ("央视频登录", "央视频扫码", "央视频重登"):
        await do_login(uid, plat)
        return
    if content in ("央视频状态", "央视频查询"):
        await do_status(uid, plat)
        return
    if content in ("央视频抽奖", "央视频抽"):
        await do_lottery(uid, plat)
        return
    if content == "央视频备注" or content.startswith("央视频备注 "):
        await do_remark(uid, plat, content[len("央视频备注"):].strip())
        return
    if content == "央视频解绑" or content.startswith("央视频解绑 "):
        await do_unbind(uid, plat, content[len("央视频解绑"):].strip())
        return
    return  # 其他消息静默，不抢答


async def _safe_main():
    try:
        await main()
    except Exception:
        import traceback
        try:
            with open("/data/plugins/.ysp_err.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc() + "\n")
        except Exception:
            pass
        raise


asyncio.run(_safe_main())
