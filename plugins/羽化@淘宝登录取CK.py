# [public: true]
# [rule: ^淘宝登录$|^淘宝登录状态$|^淘宝登录帮助$]
# [title: 淘宝登录取CK]
# [author: hermes]
# [version: 1.0]
# [description: QQ对话式 h5.ele.me 短信登录取CK, 自动提交18275(备注=手机号, 归属=QQ)]
# [platform: qq,web]
# [class: 工具类]
# [priority: 10000]
# [param: {"required":false,"key":"tbdl.browser","bool":false,"name":"浏览器容器地址","desc":"默认 http://填你的NAS地址:7788"}]
# [param: {"required":false,"key":"tbdl.ck_base","bool":false,"name":"18275地址","desc":"默认 http://填你的NAS地址:18275"}]
# [param: {"required":false,"key":"tbdl.ck_token","bool":false,"name":"提交令牌","desc":"18275 admin_config ck_submit_token"}]
# [param: {"required":false,"key":"tbdl.admin_qq","bool":false,"name":"管理员QQ","desc":"留空不限制"}]
# [disable: false]

"""淘宝登录取CK(羽化插件) — QQ 对话半自动向导
================================================
发「淘宝登录」→ 问手机号 → 你回 11 位手机号 → 容器打开 h5.ele.me/login 发短信
→ 问验证码 → 你回 6 位短信码 → 容器登录完成取全 CK
→ 自动 POST 18275 /api/ck/submit: text=手机号#ck(备注=手机号), account=QQ号(归属)
备注用手机号(天然区分多号); 归属QQ存 taobao_ck.account。
短信码到的是本人手机, 必须手动转发; 中途滑块由容器侧 7777 兜。
凭据/令牌走面板配置或本机 JSON, 不写死在代码里。
"""
import asyncio
import json
import os
import re
import urllib.request
import middleware
from sillygirl import sender

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(HERE, ".tbdl_config.json")
DEFAULTS = {
    "browser": "http://填你的NAS地址:7788",
    "ck_base": "http://填你的NAS地址:18275",
    "ck_token": "",
    "admin_qq": "",
}

def cfg(key, default=""):
    try:
        d = json.load(open(CFG_FILE, encoding="utf-8"))
        v = d.get(key)
        if v not in (None, ""):
            return str(v).strip()
    except Exception:
        pass
    try:
        v = middleware.bucketGet("tbdl", key)
        if v not in (None, ""):
            return str(v).strip()
    except Exception:
        pass
    return DEFAULTS.get(key, default)

async def _get(fn):
    try:
        r = fn()
        if asyncio.iscoroutine(r):
            return await r
        return r
    except Exception:
        return ""

async def _listen(ms):
    """等一条后续消息, 统一取文本。"""
    try:
        r = sender.listen(ms)
        if asyncio.iscoroutine(r):
            r = await r
        if isinstance(r, str):
            return r.strip()
        if isinstance(r, dict):
            return str(r.get("content") or r.get("text") or r.get("msg") or "").strip()
        try:
            c = r.getContent() if hasattr(r, "getContent") else None
            if asyncio.iscoroutine(c):
                c = await c
            return str(c or "").strip()
        except Exception:
            return str(r or "").strip()
    except Exception:
        return ""

async def _reply(text):
    try:
        r = sender.reply(text)
        if asyncio.iscoroutine(r):
            await r
    except Exception:
        pass

def _post_json(base, path, obj, timeout=120):
    req = urllib.request.Request(
        base.rstrip("/") + path, method="POST",
        data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _submit_ck(ck, phone, qq):
    base = cfg("ck_base")
    tok = cfg("ck_token")
    if not tok:
        return {"ok": False, "error": "未配置提交令牌(tbdl.ck_token)"}
    req = urllib.request.Request(
        base.rstrip("/") + "/api/ck/submit", method="POST",
        data=json.dumps({"text": "%s#%s" % (phone, ck),
                         "account": qq}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-CK-Token": tok})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

async def main():
    content = str(await _get(sender.getContent) or "").strip()
    uid = str(await _get(sender.getUserId) or "").strip()
    adm = cfg("admin_qq")
    if adm and uid not in [x.strip() for x in adm.split(",")]:
        return

    if content == "淘宝登录状态":
        await _reply("【状态】浏览器容器: %s\n18275: %s" % (cfg("browser"), cfg("ck_base")))
        return
    if content == "淘宝登录帮助":
        await _reply("发「淘宝登录」→ 按提示发手机号 → 收到短信后把 6 位验证码发我 → 自动取CK进18275。\n备注=手机号, 归属=你的QQ。")
        return
    if content != "淘宝登录":
        return

    # 步骤1: 手机号
    await _reply("【淘宝登录取CK】\n请发送手机号(11位)：\n（发 Q 取消）")
    phone = await _listen(120000)
    if not phone or phone.lower() in ("q", "取消"):
        await _reply("已取消。")
        return
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        await _reply("❌ 手机号格式不对，请重新发「淘宝登录」。")
        return

    # 步骤2: 让浏览器容器打开登录页并发短信
    browser = cfg("browser")
    await _reply("⏳ 正在打开登录页并发送短信…")
    try:
        r1 = await asyncio.to_thread(_post_json, browser, "/start", {"phone": phone})
    except Exception as e:
        await _reply("❌ 浏览器容器连不上：%s" % str(e)[:120])
        return
    if not r1.get("ok"):
        await _reply("❌ 发短信失败：%s" % str(r1.get("error") or r1)[:150])
        return

    # 步骤3: 等验证码
    await _reply("📩 短信已发。\n请把收到的 6 位验证码发我：\n（发 Q 取消）")
    code = await _listen(180000)
    if not code or code.lower() in ("q", "取消"):
        await _reply("已取消。")
        return
    digits = re.sub(r"\D", "", code)
    if not digits:
        await _reply("❌ 验证码得是数字，重新发「淘宝登录」再来。")
        return

    # 步骤4: 填码登录 + 取CK
    await _reply("⏳ 正在登录并提取 Cookie…")
    try:
        r2 = await asyncio.to_thread(_post_json, browser, "/verify",
                                     {"phone": phone, "code": digits}, 150)
    except Exception as e:
        await _reply("❌ 登录请求失败：%s" % str(e)[:120])
        return
    ck = r2.get("ck") if isinstance(r2, dict) else None
    if not ck:
        diag = (r2.get("diag") or {}) if isinstance(r2, dict) else {}
        brief = ""
        if diag:
            brief = ("\n页面URL:%s\n有cookie2:%s\n可见框:%s\n页面文字:%s" % (
                diag.get("url", "?"), diag.get("has_cookie2", "?"),
                str(diag.get("inputs", "?"))[:220], str(diag.get("text", "?"))[:120]))
        await _reply("❌ 没取到 Cookie：%s%s" % (str(r2.get("error") or r2)[:150], brief))
        return

    # 步骤5: 提交 18275(备注=手机号, account=QQ)
    try:
        res = await asyncio.to_thread(_submit_ck, ck, phone, uid)
    except Exception as e:
        await _reply("❌ 提交18275失败：%s" % str(e)[:120])
        return
    if res.get("ok"):
        await _reply("✅ 登录成功，CK 已入库\n备注(手机号)：%s\n归属QQ：%s\n新增%s 更新%s" % (
            phone, uid, res.get("added", 0), res.get("updated", 0)))
    else:
        await _reply("⚠️ 取到CK但入库失败：%s" % str(res.get("error") or res)[:150])


# 羽化 Python 插件执行入口(国网插件范本: 末尾必须 asyncio.run(main()))
asyncio.run(main())
