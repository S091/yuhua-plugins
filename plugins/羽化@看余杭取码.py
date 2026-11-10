# [public: true]
# [rule: ^看余杭登录$|^看余杭发码$|^看余杭状态$|^看余杭帮助$|^看余杭\s*\d{5}$|^\d{5}$]
# [title: 看余杭取码助手]
# [author: 羽化]
# [version: 1.2]
# [description: 看余杭(app.eyh.cn) QQ对话式短信登录取token, 自动写入青龙 Look_at_Yuhang 变量]
# [platform: qq,web]
# [cron: * * * * *]
# [class: 工具类]
# [priority: 10000]
# [param: {"required":true,"key":"kyh.ql_url","bool":false,"name":"青龙地址","desc":"青龙地址, 如 http://填你的青龙IP:5700"}]
# [param: {"required":true,"key":"kyh.ql_client_id","bool":false,"name":"青龙ClientID","desc":"OpenAPI ClientID"}]
# [param: {"required":true,"key":"kyh.ql_client_secret","bool":false,"name":"青龙ClientSecret","desc":"OpenAPI ClientSecret"}]
# [param: {"required":false,"key":"kyh.env_name","bool":false,"name":"变量名","desc":"默认 Look_at_Yuhang"}]
# [param: {"required":false,"key":"kyh.admin_qq","bool":false,"name":"管理员QQ","desc":"留空不限制"}]

"""
看余杭(app.eyh.cn) · QQ 对话式短信登录取 token(羽化插件)
========================================================
【看余杭登录】两步向导(每步一行, 不用换行):
  1) 发「看余杭登录」-> 回「请发送手机号」
  2) 发 11 位手机号 -> 校验 -> 调 v2/login/sendLoginCode 发短信
  3) 回「验证码已发, 把收到的数字发我」
  4) 用户把短信验证码发来 -> 调 v2/login/codeLogin 换 token
  5) 插件把 token 直接写进青龙 Look_at_Yuhang 变量(有则 PUT/无则 POST, 带原值留底)
  6) 回显「写入成功 + 变量值前几位」, 用户去面板启用任务 2375 即可

【看余杭状态】查青龙变量是否存在/前几位(不显示全 token)
【看余杭帮助】说明

技术: 接口 app.eyh.cn/gateway/api, 伪造安卓 okhttp 客户端; 网络调用走 to_thread。
      凭据(青龙 client_id/secret)走面板 param, 不写死、不进日志。
"""
import asyncio
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error

import middleware
from sillygirl import sender

CFG_FILE = "/data/plugins/.kyh_config.json"
def cfg(key, default=""):
    # key 形如 "kyh.ql_url"; 面板 param/bucket 用短名 "ql_url"(与国网插件一致)
    short = key.split(".", 1)[-1]
    try:
        v = json.load(open(CFG_FILE, encoding="utf-8")).get(short)
        if v not in (None, ""): return str(v).strip()
    except Exception: pass
    try:
        v = middleware.bucketGet("kyh", short)
        if v not in (None, ""): return str(v).strip()
    except Exception: pass
    return os.environ.get("KYH_" + short.upper().replace(".", "_"), default)

C = lambda k, d="": cfg("kyh." + k, d)
QL = lambda: C("ql_url", "http://填你的青龙地址:5700").rstrip("/")
CID = lambda: C("ql_client_id", "")
SEC = lambda: C("ql_client_secret", "")
ENV_NAME = lambda: C("env_name", "Look_at_Yuhang") or "Look_at_Yuhang"
ADMIN = lambda: [x.strip() for x in C("admin_qq", "").split(",") if x.strip()]

STATE_FILE = "/data/plugins/.kyh_wait.json"      # 等码状态(按 QQ uid)
WAIT_TTL = 300                                   # 验证码 5 分钟
ENV_BACKUP = "/data/plugins/.kyh_env_backup.json"  # 变量原值留底(可回退)

API = "https://app.eyh.cn/gateway/api"
DEVICE = {"os":"9","deviceBrand":"samsung","deviceId":"2ac37c0ab4b8fdad",
          "equipmentId":"2ac37c0ab4b8fdad","deviceType":"Samsung SM-N9760",
          "device":"android","clientVersion":"5.0.1"}
HEADERS = {"Content-Type":"application/json; charset=utf-8","Host":"app.eyh.cn",
           "Connection":"Keep-Alive","Accept-Encoding":"gzip",
           "User-Agent":"okhttp/5.0.0-alpha.2","Accept":"application/json"}

def _load_wait():
    try: return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception: return {}
def _save_wait(d):
    try: json.dump(d, open(STATE_FILE,"w",encoding="utf-8"), ensure_ascii=False)
    except Exception: pass

def _trace():
    import random, string
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=24))

def _post_api(api, data_extra):
    body = {"traceId": _trace(), "service": "core", "api": api, "token": "",
            "data": data_extra, "userDevice": DEVICE}
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    import gzip
    req = urllib.request.Request(API, data=raw, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        buf = r.read()
    # 服务端按 Accept-Encoding:gzip 返回压缩体(requests 自动解压, urllib 需手动)
    if buf[:2] == b"\x1f\x8b":
        try: buf = gzip.decompress(buf)
        except Exception: pass
    return json.loads(buf.decode("utf-8", "replace"))

# ---------------- 青龙 OpenAPI ----------------
def _token():
    u = QL() + "/open/auth/token?client_id=" + urllib.parse.quote(CID()) + "&client_secret=" + urllib.parse.quote(SEC())
    with urllib.request.urlopen(urllib.request.Request(u), timeout=15) as r:
        return (json.load(r).get("data") or {}).get("token", "")
def _api(method, path, body=None, token=None):
    t = token or _token()
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(QL() + path, data=data, method=method,
        headers={"Authorization":"Bearer "+t,"Content-Type":"application/json",
                 "Accept":"application/json","User-Agent":"yuhua-kyh"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)
def _envs(token):
    d = _api("GET", "/open/envs", token=token).get("data") or []
    if isinstance(d, dict): d = d.get("data") or []
    return {str(e.get("name")): e for e in d if isinstance(e, dict)}

def _write_qinglong_env(name, value):
    """有则 PUT 更新/无则 POST 新建, 带原值留底。返回 (ok, msg)。"""
    tok = _token()
    es = _envs(tok)
    e = es.get(name)
    backup = {}
    if e:
        backup = {"id": e.get("id"), "value": str(e.get("value") or ""),
                  "remarks": str(e.get("remarks") or "")}
        r = _api("PUT", "/open/envs",
                 {"id": e.get("id"), "name": name, "value": value,
                  "remarks": e.get("remarks") or ""}, token=tok)
        ok = r.get("code") == 200
    else:
        r = _api("POST", "/open/envs",
                 [{"name": name, "value": value, "remarks": "看余杭token(羽化助手自动写入)"}], token=tok)
        ok = r.get("code") == 200
    try:
        json.dump({"at": time.time(), "name": name, "backup": backup},
                  open(ENV_BACKUP,"w",encoding="utf-8"), ensure_ascii=False)
    except Exception: pass
    return ok, ("✓ 已更新" if e else "✓ 已新建") + " 青龙变量 " + name

# ---------------- 羽化 SDK 封装 ----------------
async def _get(fn):
    try:
        r = fn()
        if asyncio.iscoroutine(r): r = await r
        return r
    except Exception: return ""
async def _listen(ms):
    try:
        r = sender.listen(ms)
        if asyncio.iscoroutine(r): r = await r
        if isinstance(r, str): return r.strip()
        if isinstance(r, dict): return str(r.get("content") or r.get("text") or r.get("msg") or "").strip()
        try:
            c = r.getContent() if hasattr(r, "getContent") else None
            if asyncio.iscoroutine(c): c = await c
            return str(c or "").strip()
        except Exception: return str(r or "").strip()
    except Exception: return ""
async def _reply(text):
    try:
        r = sender.reply(text)
        if asyncio.iscoroutine(r): await r
    except Exception: pass

def _phone_ok(s): return bool(re.fullmatch(r"1[3-9]\d{9}", s or ""))
def _code_ok(s): return bool(re.fullmatch(r"\d{5}", s or ""))
def _mask(v): return (v[:6] + "…" + v[-4:]) if v and len(v) > 12 else ("(空)" if not v else "***")

async def main():
    content = str(await _get(sender.getContent) or "").strip()
    uid = str(await _get(sender.getUserId) or "").strip()
    adm = ADMIN()
    if adm and uid not in adm: return

    # ---- 状态 ----
    if content == "看余杭状态":
        try:
            tok = _token(); es = _envs(tok); e = es.get(ENV_NAME())
            val = str(e.get("value") or "") if e else ""
            await _reply("【看余杭状态】\n变量 %s: %s\n值: %s\n任务2375: 禁用中(需手动启用)" %
                         (ENV_NAME(), "存在" if e else "不存在", _mask(val)))
        except Exception as ex:
            await _reply("❌ 查青龙失败: %s" % str(ex)[:80])
        return
    if content == "看余杭帮助":
        await _reply("【看余杭取码助手】\n发「看余杭登录」→ 手机号 → 短信验证码\n自动把 token 写入青龙 %s\n不用换行, 每步一行; 发 Q 取消" % ENV_NAME())
        return

    # ---- 登录向导 ----
    if content == "看余杭登录":
        await _reply("【看余杭登录】\n请发送手机号(11位):\n(发 Q 取消)")
        phone = await _listen(120000)
        if not phone or phone.lower() in ("q","取消"):
            await _reply("已取消。"); return
        if not _phone_ok(phone):
            await _reply("❌ 手机号格式不对, 请重新发「看余杭登录」。"); return
        await _reply("⏳ 正在发送短信验证码…")
        try:
            r = await asyncio.to_thread(_post_api, "v2/login/sendLoginCode", {"mobilePhone": phone})
            # ★exe字节码: 发码成功(HTTP200)取 response["data"] 作流水号(全局 code),
            #   业务 code 字段不参与判断(之前误判"1000"为失败)。data 可能是 str 或 dict。
            dv = r.get("data") if isinstance(r, dict) else None
            sn = str(dv) if (dv is not None and not isinstance(dv, dict)) else ""
            if isinstance(dv, dict):
                sn = str(dv.get("serialNum") or dv.get("data") or "")
            mmsg = str((r.get("message") or r.get("msg") or "") if isinstance(r, dict) else "")
            if not sn and ("频繁" in mmsg or "失效" in mmsg):
                await _reply("❌ 发码未成功: %s\n请稍等几分钟再发「看余杭登录」(别用旧验证码)。" % mmsg); return
            w = _load_wait(); w[uid] = {"phone": phone, "ts": time.time(), "sn": sn}; _save_wait(w)
            await _reply("📱 验证码已发%s, 把收到的 5 位数字直接发给我\n(5分钟内有效, 发 Q 取消)" %
                         ("(流水号已记录)" if sn else ""))
        except Exception as ex:
            await _reply("❌ 发码失败: %s" % str(ex)[:120])
        return

    # ---- 收到验证码 ----
    m = re.match(r"^看余杭\s*(\d{5})$", content) or re.match(r"^(\d{5})$", content)
    if m:
        sms = m.group(1)
        w = _load_wait(); st = w.get(uid) or {}
        phone = st.get("phone", "")
        if not phone or time.time() - st.get("ts", 0) > WAIT_TTL:
            await _reply("⏰ 没有进行中的登录(或已超5分钟)。发「看余杭登录」重新来。"); return
        await _reply("⏳ 正在用验证码换 token…")
        try:
            sn = st.get("sn") or ""
            # ★exe字节码: 登录 data={"serialNum": 发码响应data, "code": 短信验证码}
            if not sn:
                w.pop(uid, None); _save_wait(w)
                await _reply("❌ 缺少发码流水号(serialNum), 请重新发「看余杭登录」。"); return
            payload = {"serialNum": sn, "code": sms}
            r = await asyncio.to_thread(_post_api, "v2/login/codeLogin", payload)
            # ★exe字节码: 登录成功 token = response["data"](字符串本身)
            tok = ""
            if isinstance(r, dict):
                dv = r.get("data")
                if isinstance(dv, str) and len(dv) > 8 and " " not in dv.strip():
                    tok = dv.strip()
                elif isinstance(dv, dict):
                    for k in ("token", "accessToken", "access_token", "userToken", "ticket"):
                        v = dv.get(k)
                        if v and isinstance(v, str) and len(v) > 8: tok = v; break
            if not tok:
                w.pop(uid, None); _save_wait(w)
                await _reply("❌ 没拿到 token, 接口返回:\n%s\n\n请求: serialNum=%s code=%s\n(已重置, 发「看余杭登录」重新来)" %
                             (json.dumps(r, ensure_ascii=False)[:400], sn, sms)); return
            ok, msg = await asyncio.to_thread(_write_qinglong_env, ENV_NAME(), tok)
            w.pop(uid, None); _save_wait(w)
            if ok:
                await _reply("✅ 看余杭登录成功!\n%s\n值: %s\n去青龙启用任务 2375 即可(当前禁用)" % (msg, _mask(tok)))
            else:
                await _reply("⚠️ 拿到 token 但写青龙失败:\n%s\n值: %s\n(可手动建变量)" % (msg, _mask(tok)))
        except Exception as ex:
            await _reply("❌ 登录失败: %s" % str(ex)[:150])
        return

asyncio.run(main())
