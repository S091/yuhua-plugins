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
# [rule: ^央视频清理( .+)?$]
# [rule: ^央视频菜单$]
# [rule: ^央视频帮助$]
# [title: 央视频助手]
# [author: 羽化]
# [version: 2.3.1]
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
    import ysp_remark   # 共享: 扫码后备注落地(会话交给莹莹棒)
except Exception:
    ysp_remark = None

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


def _migrate_map(raw):
    """旧格式 {QQ:{vuserid,ck,remark,ts}} → 新格式 {QQ:{"accounts":{vuid:{...}}}}"""
    out = {}
    for qq, rec in (raw or {}).items():
        if not isinstance(rec, dict):
            continue
        if "accounts" in rec and isinstance(rec["accounts"], dict):
            out[str(qq)] = rec
            continue
        vuid = str(rec.get("vuserid") or "")
        if not vuid:
            continue
        out[str(qq)] = {"accounts": {
            vuid: {"ck": rec.get("ck") or "", "remark": rec.get("remark") or str(qq),
                   "ts": rec.get("ts") or 0, "vuserid": vuid}
        }}
    return out


def get_map():
    """返回规范化后的绑定表：{QQ: {"accounts": {vuid: {ck,remark,ts,vuserid}}}}"""
    try:
        raw = json.loads(cfg("map") or "{}")
        if not isinstance(raw, dict):
            return {}
        return _migrate_map(raw)
    except Exception:
        return {}


def set_map(m):
    set_cfg("map", json.dumps(m, ensure_ascii=False))


def my_accounts(m, uid):
    """取某 QQ 名下所有账号 → [(vuid, rec), ...]"""
    node = m.get(str(uid)) or {}
    accs = node.get("accounts") or {}
    return sorted(accs.items(), key=lambda x: str(x[0]))


def all_accounts(m):
    """全部账号逐条展开 → [(qq, vuid, rec), ...]"""
    out = []
    for qq, node in m.items():
        for vuid, rec in (node.get("accounts") or {}).items():
            out.append((str(qq), str(vuid), rec))
    return out


def add_account(m, uid, vuid, ck, remark):
    """绑定/更新一个账号（同 vuid 覆盖，不同 vuid 追加）"""
    node = m.setdefault(str(uid), {"accounts": {}})
    accs = node.setdefault("accounts", {})
    old = accs.get(str(vuid)) if str(vuid) in accs else None
    # 保留旧备注（若用户自己改过）
    if old and old.get("remark") and old.get("remark") != str(uid):
        remark = old["remark"]
    accs[str(vuid)] = {"ck": ck, "remark": remark or str(uid),
                       "ts": int(time.time()), "vuserid": str(vuid)}
    set_map(m)
    return old is None


def del_accounts(m, uid, vuids=None):
    """删除某 QQ 的账号；vuids 为空=全删。返回被删的 vuserid 列表"""
    node = m.get(str(uid))
    if not node:
        return []
    accs = node.get("accounts") or {}
    if vuids:
        killed = [v for v in vuids if v in accs]
        for v in killed:
            accs.pop(v, None)
    else:
        killed = list(accs.keys())
        accs.clear()
    if not accs:
        m.pop(str(uid), None)
    set_map(m)
    return killed


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


def ql_merge_cookie(newck, vuid, tag):
    """把 newck 合并进 YSP_COOKIE（多行 名#cookie），按 vuserid 去重。

    ysp.py 的 parse_accounts 支持换行分隔、每行 `名#cookie`；
    同一 vuserid 重新扫码 = 替换该行，不同 vuserid = 追加一行。
    tag 用绑定人 QQ，日志段标记 [x/y QQ] 才能和绑定表对应。
    """
    var = cfg("ql_var_name") or "YSP_COOKIE"
    raw = ql_get_var(var) or ""
    lines, hit = [], False
    for ln in str(raw).splitlines():
        ln = ln.strip().strip(",;")
        if not ln:
            continue
        # 拆 名#cookie
        if "#" in ln and "=" not in ln.split("#", 1)[0]:
            name, ck = ln.split("#", 1)
        else:
            name, ck = "", ln
        # 该行 vuserid
        old_vuid = ""
        for seg in str(ck).replace("\n", ";").split(";"):
            seg = seg.strip()
            if seg.startswith("vuserid="):
                old_vuid = seg.split("=", 1)[1]
                break
        if not hit and (old_vuid == str(vuid) or (name == str(tag) and old_vuid == "")):
            lines.append("%s#%s" % (tag, newck))
            hit = True
        else:
            lines.append("%s#%s" % (name or tag, ck))
    if not hit:
        _names = [ln.split("#", 1)[0] for ln in lines if "#" in ln]
        _t, _i = str(tag), 2
        while _t in _names:
            _t, _i = "%s%d" % (tag, _i), _i + 1
        lines.append("%s#%s" % (_t, newck))
    value = "\n".join(lines)
    return ql_set_var(var, value), len(lines)


def ql_remove_cookie(vuids):
    """从 YSP_COOKIE 删掉指定 vuserid 的整行，其余保留。返回删了几行。"""
    var = cfg("ql_var_name") or "YSP_COOKIE"
    raw = ql_get_var(var) or ""
    killed, kept = 0, []
    for ln in str(raw).splitlines():
        ln = ln.strip().strip(",;")
        if not ln:
            continue
        if "#" in ln and "=" not in ln.split("#", 1)[0]:
            name, ck = ln.split("#", 1)
        else:
            name, ck = "", ln
        old_vuid = ""
        for seg in str(ck).replace("\n", ";").split(";"):
            seg = seg.strip()
            if seg.startswith("vuserid="):
                old_vuid = seg.split("=", 1)[1]
                break
        if old_vuid and str(old_vuid) in [str(v) for v in vuids]:
            killed += 1
        else:
            kept.append("%s#%s" % (name or "账号", ck))
    ql_set_var(var, "\n".join(kept))
    return killed


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

    # ★★ 扫码成功：只写一份「待备注」标记 + 弹提示，本插件即结束(会话自动让给莹莹棒总入口)
    #    备注确定后由莹莹棒调用 ysp_remark.finalize 写青龙/绑定/跑任务
    set_cfg("waiting_qr", "{}")
    vuid = ck_uid(ck) or ("q%s" % str(uid))
    try:
        if ysp_remark is not None:
            ysp_remark.pending_save({"uid": str(uid), "plat": plat, "ck": ck,
                                     "vuid": vuid, "qid": qid, "ts": time.time()})
        else:
            _set_remark_state({"uid": str(uid), "plat": plat, "ck": ck,
                               "vuid": vuid, "qid": qid, "ts": time.time()})
    except Exception:
        pass
    await reply(REMARK_HINT, recall=False)


def _state():
    """扫码等待态 waiting_qr（内容对象 {uid,plat,qid,guid,ts}）"""
    try:
        return json.loads(cfg("waiting_qr") or "{}")
    except Exception:
        return {}


def _remark_state():
    """备注等待态 waiting_remark：{uid, plat, ck, vuid, qid, ts}"""
    try:
        return json.loads(cfg("waiting_remark") or "{}")
    except Exception:
        return {}


def _set_remark_state(d):
    set_cfg("waiting_remark", json.dumps(d or {}, ensure_ascii=False))


async def finalize_login(uid, plat, ck, vuid, qid, remark):
    """★ 备注确定后才调用：合并写青龙 YSP_COOKIE + 绑定 + 触发积分任务。"""
    var = cfg("ql_var_name") or "YSP_COOKIE"
    act, eid, r, total = "?", None, {}, 0
    try:
        # tag 用备注名(每号唯一)，别用 QQ 号——多账号同名会让 ysp.py 回写 dict 撞车互相覆盖
        _tag = (remark or str(vuid))
        (act, eid, r), total = await asyncio.to_thread(ql_merge_cookie, ck, vuid, _tag)
    except Exception as e:
        await reply("⚠️ CK 已拿到但写青龙变量失败：%s" % e, recall=False)

    # 绑定（备注用本次确定的 remark）
    m = get_map()
    add_account(m, uid, vuid, ck, remark or str(uid))
    cnt = len(my_accounts(m, uid))

    await reply(
        "✅ 央视频登录成功\n"
        "🏷 备注：%s\n"
        "🟢 已合并写入青龙变量：%s（%s，现共 %d 个账号）\n"
        "🔗 已绑定到你的 QQ：你现在共绑定 %d 个央视频账号\n"
        "%s" % (remark or str(uid), var, act, total, cnt,
                ("（变量ID %s）" % eid) if eid else ""),
        recall=False,
    )

    # 后台启用 + 跑一次（不刷日志）
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
        await reply("⚠️ 积分任务触发失败：%s\n（可到青龙面板手动运行 ID %s）" % (e, tid), recall=False)


REMARK_HINT = (
    "✅ 扫码成功！请【务必回一条消息】完成登录（不回不生效）\n"
    "· 想备注：直接回名字（如「我的主号」「我妈的号」）\n"
    "· 不备注：发「回车 / 空白」即可，默认用你的 QQ 号\n"
    "· 回复 q 取消本次登录（CK 不会写入青龙）\n"
    "⏰ 30 秒内不回，会自动按你的 QQ 号完成登录\n"
    "（备注/超时确定后才会写入青龙并跑积分任务）"
)


REMARK_TIMEOUT = 30   # 秒；与共享模块 PENDING_TIMEOUT 对齐（兜底已迁到莹莹棒）


def cron_remark_timeout():
    """cron(fake) 触发时调用：残留备注态超过 REMARK_TIMEOUT 秒 → 自动默认QQ号 finalize。

    用 asyncio.run 跑一遍 finalize_login（同步上下文里唯一的 async 调用点）。
    """
    rst = _remark_state()
    if not rst or not rst.get("uid"):
        return
    if time.time() - float(rst.get("ts") or 0) < REMARK_TIMEOUT:
        return
    uid = str(rst.get("uid"))
    plat = rst.get("plat") or "qq"
    ck = rst.get("ck", "")
    vuid = rst.get("vuid", "")
    qid = rst.get("qid", "")
    _set_remark_state({})   # 先清，防止并发 cron 重复 finalize
    # cron(fake) 里 sender 不可用，不调 reply；只做「落地」：写青龙 + 绑定
    try:
        ql_merge_cookie(ck, vuid, uid)
        m = get_map()
        add_account(m, uid, vuid, ck, uid)   # 备注默认 QQ 号
    except Exception:
        pass


async def handle_remark(uid, plat, content):
    """备注态下的任意消息分发：q 取消 / 空=默认QQ / 文字=备注 → 都 finalize。"""
    rst = _remark_state()
    if not rst or str(rst.get("uid")) != str(uid):
        return False   # 不是本人的备注态 → 不处理
    # q：全局退出，清状态，不写青龙
    if str(content).strip().lower() == "q":
        _set_remark_state({})
        await reply("已取消央视频登录（本次 CK 未写入青龙）。", recall=False)
        return True
    # 空/回车/两行空白 → 默认 QQ 号
    txt = str(content).strip()
    remark = txt if txt else str(uid)
    _set_remark_state({})
    await finalize_login(uid, plat, rst.get("ck", ""), rst.get("vuid", ""),
                         rst.get("qid", ""), remark)
    return True


async def do_status(uid, plat):
    """管理员=总览全部账号；普通用户=看自己名下所有账号（多账号）。"""
    m = get_map()

    def _lot_hint(vuid, name=""):
        lot = lot_today(vuid)
        if lot:
            return "有记录 " + time.strftime("%H:%M", time.localtime(lot.get("ts") or 0))
        # ★ 按账号名从青龙多行变量里取自己那条(不再贴别人的结果)
        t, when = lot_for_account(name or vuid, vuid)
        if t:
            return "有记录 %s" % (when[11:16] if len(when) >= 16 else "")
        return "未抽"

    if _is_admin(uid):
        accs = all_accounts(m)
        if not accs:
            await reply("【央视频账号总览】\n目前没有任何绑定账号。", recall=False)
            return
        blocks = ["【央视频账号总览】共 %d 个账号 / %d 人（管理员视角）" % (len(accs), len(m))]
        for qq, vuid, rec in accs:
            blocks.append(
                "· %s（QQ %s）\n  vuserid：%s\n  今日抽奖：%s\n  绑定：%s" % (
                    rec.get("remark") or qq, qq, vuid, _lot_hint(vuid, rec.get("remark") or qq),
                    time.strftime("%m-%d %H:%M", time.localtime(rec.get("ts") or 0))))
        await reply("\n\n".join(blocks), recall=False)
        return

    mine = my_accounts(m, uid)
    if not mine:
        await reply("你还没有绑定央视频账号。\n发「央视频登录」扫码绑定。", recall=False)
        return
    blocks = ["【央视频账号状态】你共绑定 %d 个账号" % len(mine)]
    for vuid, rec in mine:
        blocks.append(
            "· 备注：%s\n  vuserid：%s\n  今日抽奖：%s\n  CK：%d 字符\n  绑定：%s" % (
                rec.get("remark") or vuid, vuid, _lot_hint(vuid, rec.get("remark") or vuid),
                len(rec.get("ck") or ""),
                time.strftime("%m-%d %H:%M", time.localtime(rec.get("ts") or 0))))
    await reply("\n\n".join(blocks), recall=False)


def _filter_own_lines(log, vuserid):
    """从任务日志里只提取属于本账号的抽奖/积分行。
    多账号日志按 '[x/y 标签]' 分段，标签=绑定QQ；本插件写入时标签就是 QQ。
    """
    try:
        lines = [ln for ln in str(log or "").split("\n") if ln.strip()]
        if not lines:
            return []
        seg_idx = [i for i, ln in enumerate(lines) if "账号" in ln and "][/" in ln.replace("[", "")]
        # 更稳：匹配 [数字/数字 xxx]
        seg_idx = [i for i, ln in enumerate(lines) if re.search(r"\[\d+/\d+ ", ln)]
        if len(seg_idx) <= 1:
            return extract_lottery(log)          # 单账号：全归他
        bounds = seg_idx + [len(lines)]
        for k in range(len(seg_idx)):
            seg_text = "\n".join(lines[bounds[k]:bounds[k + 1]])
            if vuserid and str(vuserid) in seg_text:
                return extract_lottery(seg_text)
        return extract_lottery("\n".join(lines[bounds[0]:bounds[1]]))
    except Exception:
        return extract_lottery(log)


def _lot_is_win(result):
    """真中奖: 含『抽奖 N 次：奖品』且不是状态词(已用完/未抽奖/异常)。"""
    r = str(result or "").strip()
    return (("次：" in r or "次:" in r) and "抽奖" in r
            and "已用完" not in r and "未中奖" not in r
            and "未抽奖" not in r and "异常" not in r)


def _lot_fmt(result, pts):
    """中奖=显示奖品信息; 没中奖=「ℹ️ 今日未中奖」(统一口径)。"""
    result = str(result or "").strip()
    if not _lot_is_win(result):
        return "ℹ️ 今日未中奖"
    text = "🎁 %s" % result
    if pts and "→" in str(pts):
        a, b = str(pts).split("→", 1)
        text += "\n💰 积分 %s → %s" % (a.strip(), b.strip())
    text += LOT_TIP
    return text


def lot_map_today():
    """读 YSP_LAST_LOTTERY → {账号名: (结构化文本, 时间)}。只留今天的。

    新格式每行: 名|YYYY-MM-DD HH:MM|结果|积分前→后
    旧单条: 时间|结果|积分 → 存到 '__global__' 键(兜底, 兼容老数据)
    """
    out = {}
    try:
        raw = ql_get_var("YSP_LAST_LOTTERY") or ""
    except Exception:
        return out
    today = time.strftime("%Y-%m-%d")
    for ln in str(raw).splitlines():
        ln = ln.strip()
        if not ln:
            continue
        p = ln.split("|")
        if len(p) >= 3 and p[1].strip().startswith(today):
            # 新格式
            t = _lot_fmt(p[2], p[3] if len(p) > 3 else "")
            if t:
                out[p[0].strip()] = (t, p[1].strip())
        elif len(p) >= 2 and p[0].strip().startswith(today):
            # 旧单条: 时间|结果|积分
            t = _lot_fmt(p[1], p[2] if len(p) > 2 else "")
            if t:
                out["__global__"] = (t, p[0].strip())
    return out


def lot_for_account(name, vuid=""):
    """按账号取今天的抽奖结果。找不到返回 (None, None)。

    匹配顺序: 备注名 → QQ号tag(历史) → vuserid → __global__ 兜底。
    """
    mp = lot_map_today()
    if not mp:
        return None, None
    for k in (str(name), str(vuid)):
        if k and k in mp:
            return mp[k]
    # ★ 不再回退 __global__: 那是别人的中奖/旧值, 贴过来会「全都一样」
    return None, None


def _global_lottery_text():
    """读青龙 YSP_LAST_LOTTERY → 结构化文本（今天）"""
    try:
        raw = ql_get_var("YSP_LAST_LOTTERY") or ""
        if raw and str(raw).startswith(time.strftime("%Y-%m-%d")):
            parts = str(raw).split("|")
            if len(parts) >= 2 and parts[1].strip():
                when, result = parts[0].strip(), parts[1].strip()
                pts = parts[2].strip() if len(parts) > 2 else ""
                text = "🎁 %s" % result
                if pts and "→" in pts:
                    a, b = pts.split("→", 1)
                    text += "\n💰 积分 %s → %s" % (a.strip(), b.strip())
                if "次：" in result:
                    text += LOT_TIP
                return text, when
    except Exception:
        pass
    return None, None


async def do_lottery(uid, plat):
    """先查今天有没有跑过(全局按账号读日志/变量)，有就汇总结果，不重复触发。"""
    m = get_map()
    is_adm = _is_admin(uid)
    mine = my_accounts(m, uid)

    # ① 优先：青龙变量里有今天的结果 → ★按账号各取各的那条(不再一条贴给所有人)
    lmap = await asyncio.to_thread(lot_map_today)
    if lmap:
        if is_adm:
            accs = all_accounts(m)
        else:
            if not mine:
                await reply("你还没有绑定央视频账号。\n先发「央视频登录」扫码绑定。", recall=False)
                return
            accs = [(str(uid), v, r) for v, r in mine]
        title = ("【央视频抽奖 · 全部账号】共 %d 个（管理员视角）" if is_adm
                 else "【央视频抽奖 · 我的账号】共 %d 个") % len(accs)
        blocks = [title]
        for qq, vuid, rec in accs:
            name = rec.get("remark") or qq
            t, when = await asyncio.to_thread(lot_for_account, name, vuid)
            if not t:
                t = "ℹ️ 今日未中奖"
            blocks.append("· %s（%s）\n%s" % (name, vuid[:8], t))
        await reply("\n\n".join(blocks) + "\n\n（今天已跑过，不重复跑）", recall=False)
        return

    # ② 青龙最新日志是今天跑的 → 按账号段提取
    task_id = cfg("task_id") or "2341"
    log = ""
    try:
        log = await asyncio.to_thread(ql_task_log, task_id)
    except Exception:
        pass
    if log and _log_is_today(log, time.strftime("%Y-%m-%d")):
        # 逐个账号提取（多账号日志分段）
        if is_adm:
            targets = all_accounts(m)
        else:
            targets = [(str(uid), v, r) for v, r in mine]
        if targets:
            blocks = ["【央视频抽奖 · 今日结果】"]
            for qq, vuid, rec in targets:
                picked = _filter_own_lines(log, vuid)
                txt = "\n".join(picked) if picked else "（本次无抽奖记录）"
                if any("次：" in x for x in picked):
                    txt += LOT_TIP
                blocks.append("· %s（%s）\n%s" % (rec.get("remark") or qq, vuid[:8], txt))
            await reply("\n\n".join(blocks) + "\n\n（今天已跑过，不重复跑）", recall=False)
            return
        picked = extract_lottery(log)
        if picked:
            text = "\n".join(picked)
            _tip = LOT_TIP if any("次：" in x for x in picked) else ""
            await reply("【央视频抽奖 · 今日结果】\n%s%s\n\n（今天已跑过，不重复跑）" % (text, _tip), recall=False)
            return
        await reply("📋 今天已经跑过一次了，但日志里还没有抽奖结果。\n等几分钟再发「央视频抽奖」（不会重复跑）。", recall=False)
        return

    # ③ 今天没跑过 → 触发一次
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
    if is_adm:
        targets = all_accounts(m)
    else:
        targets = [(str(uid), v, r) for v, r in mine]
    blocks = ["【央视频抽奖 · 结果】"]
    for qq, vuid, rec in targets:
        picked = _filter_own_lines(log, vuid)
        txt = "\n".join(picked) if picked else "（无抽奖记录）"
        if any("次：" in x for x in picked):
            txt += LOT_TIP
        blocks.append("· %s（QQ %s / %s）\n%s" % (rec.get("remark") or qq, qq, vuid[:8], txt))
    await reply("\n\n".join(blocks), recall=False)


async def do_remark(uid, plat, arg=""):
    """央视频备注 名字 [QQ号/vuserid] —— 给自己或(管理员)指定账号加备注。"""
    parts = str(arg or "").split()
    if not parts:
        await reply("格式：央视频备注 我的主号\n（管理员可：央视频备注 名字 QQ号 或 名字 vuserid）")
        return
    name = parts[0]
    m = get_map()
    # 定位目标
    if len(parts) >= 2 and _is_admin(uid):
        key = parts[1]
        # key 可能是 QQ 或 vuserid
        if key in m:
            accs = m[key].get("accounts") or {}
            target_vuid = sorted(accs.keys())[0] if len(accs) == 1 else None
        else:
            target_vuid = key
            for qq, node in m.items():
                if key in (node.get("accounts") or {}):
                    key = qq
                    break
    else:
        key = str(uid)
        mine = my_accounts(m, uid)
        target_vuid = (mine[0][0] if len(mine) == 1 else None)
    node = m.get(key)
    if not node:
        await reply("没有这个绑定 / 账号。\n发「央视频状态」查看。")
        return
    accs = node.get("accounts") or {}
    if target_vuid and target_vuid in accs:
        accs[target_vuid]["remark"] = name
        set_map(m)
        await reply("✅ 备注已设为：%s（vuserid %s）" % (name, target_vuid[:8]))
    else:
        await reply("你绑定了 %d 个账号，请指定要改哪个：\n央视频备注 名字 vuserid后8位" % len(accs))
        return


async def do_unbind(uid, plat, arg=""):
    """央视频解绑 [QQ/vuserid] —— 仅管理员；只删绑定，不动青龙 CK。"""
    if not _is_admin(uid):
        await reply("❌ 无权限解绑（仅管理员可操作）。")
        return
    m = get_map()
    arg = str(arg or "").strip()
    if not arg:
        await reply("格式：央视频解绑 QQ号 [vuserid]\n（只删绑定，青龙 CK 不动；要连 CK 一起删用「央视频清理」）")
        return
    parts = arg.split()
    qq = parts[0]
    vuids = [parts[1]] if len(parts) > 1 else None
    killed = del_accounts(m, qq, vuids)
    if not killed:
        await reply("没有这个绑定 / 账号：%s" % qq)
        return
    await reply("✅ 已解绑 %d 个账号（QQ %s）。\n青龙变量里的 CK 没动。" % (len(killed), qq))


async def do_clean(uid, plat, arg=""):
    """★ 管理员：央视频清理 QQ号 [vuserid] —— 删绑定 + 同步删青龙 YSP_COOKIE 对应行。"""
    if not _is_admin(uid):
        await reply("❌ 无权限清理（仅管理员可操作）。")
        return
    m = get_map()
    parts = str(arg or "").split()
    if not parts:
        await reply("格式：央视频清理 QQ号 [vuserid]\n· 不带 vuserid = 清掉该 QQ 名下所有账号\n· 带 vuserid = 只精确删那一个\n（会同时删绑定 + 青龙 YSP_COOKIE 里对应行，其他账号不受影响）")
        return
    qq = parts[0]
    vuids = [parts[1]] if len(parts) > 1 else None
    killed = del_accounts(m, qq, vuids)
    if not killed:
        await reply("没有这个绑定 / 账号：%s" % qq)
        return
    try:
        n = await asyncio.to_thread(ql_remove_cookie, killed)
        extra = "；青龙 YSP_COOKIE 同步删掉 %d 行" % n
    except Exception as e:
        extra = "；但青龙变量清理失败：%s（可手动检查）" % e
    await reply("✅ 已清理 QQ %s 的 %d 个账号%s。\n其他账号不受影响。" % (qq, len(killed), extra), recall=False)


async def do_menu(uid, plat):
    lines = [
        "【央视频助手】",
        "· 央视频登录 → 扫码绑定（一人可绑多个号，追加不覆盖）",
        "· 央视频状态 → 看自己名下所有账号 / 今日抽奖",
        "· 央视频抽奖 → 今天没抽就跑一次，汇总你的所有账号",
        "· 央视频备注 名字 → 给账号加备注",
        "· 央视频解绑 → 解绑账号（仅管理员，CK 不动）",
    ]
    if _is_admin(uid):
        lines.append("· 央视频清理 QQ号 [vuserid] → 删绑定+删青龙CK（仅管理员）")
    lines += ["· q → 取消当前操作", "",
              "扫码成功后 CK 会合并写入青龙变量「%s」。" % (cfg("ql_var_name") or "YSP_COOKIE")]
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
    # ★ cron 触发是假 sender（plat=="fake"），必须最先挡掉，
    #   否则每分钟的空消息会误触发残留的备注态 → 误推青龙
    if plat == "fake":
        try:
            await asyncio.to_thread(cron_remark_timeout)
        except Exception:
            pass
        return

    # 扫码等待期的 q → 全局取消（莹莹棒也会回一条，这里只清自己的状态，不重复回文字）
    if str(content).strip().lower() == "q":
        st = _state()
        if st.get("uid"):
            set_cfg("waiting_qr", "{}")
        return

    if not content:
        return  # 非备注态的真实空消息(回车)静默，不抢答

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
    if content == "央视频清理" or content.startswith("央视频清理 "):
        await do_clean(uid, plat, content[len("央视频清理"):].strip())
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
