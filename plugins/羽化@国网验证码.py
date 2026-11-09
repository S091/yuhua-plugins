# [public: true]
# [rule: ^国网登录$|^国网发码$|^国网状态$|^国网帮助$|^国网\s*\d{6}$|^\d{6}$]
# [title: 国网验证码助手]
# [author: 羽化]
# [version: 1.1]
# [description: 网上国网QQ对话登录+收码+风控分类(公开脱敏版)]
# [platform: qq,web]
# [cron: * * * * *]
# [class: 工具类]
# [priority: 10000]
# [param: {"required":true,"key":"sgcc.ql_url","bool":false,"name":"青龙地址","desc":"青龙地址"}]
# [param: {"required":true,"key":"sgcc.ql_client_id","bool":false,"name":"青龙ClientID","desc":"ClientID"}]
# [param: {"required":true,"key":"sgcc.ql_client_secret","bool":false,"name":"青龙ClientSecret","desc":"ClientSecret"}]
# [param: {"required":false,"key":"sgcc.ql_cron_id","bool":false,"name":"辅助任务ID","desc":"默认2352"}]
# [param: {"required":false,"key":"sgcc.admin_qq","bool":false,"name":"管理员QQ","desc":"留空不限制"}]

"""
网上国网 · QQ 对话式登录/验证码助手(羽化插件, 半自动)
=====================================================
【国网登录】两步向导(每步一行, 不用换行):
  1) 发「国网登录」 -> 回「请发送账号(手机号)」
  2) 发 11 位手机号 -> 校验 -> 回「请输入密码(App登录密码)」
  3) 发密码 -> 校验非空 -> 同步进青龙 SGCC_ACCOUNT / SGCC_PASSWORD(带原值留底)
  4) 随即跑一次国网脚本(all)
  5) 若撞到 4006 新设备校验 -> 回「准备接收验证码, 收到短信把6位数字发我」并进入等码
  6) 用户把手机收到的 6 位数字发来 -> --code 完成登录、缓存令牌、回显

【国网发码】单独触发 --send-sms(老入口, 保留)
【国网状态】--status(纯本地) 【国网帮助】说明

技术: 青龙 task 包装会 clear_env 掉新变量, 故 command = source preload/env.sh + 直接 python3
     (preload/env.sh 由青龙后端在 PUT /open/envs 后即时刷新, 实测 ✓)。
凭据不进 command/不进日志; 网络调用走 to_thread。
"""
import asyncio
import json
import os
import re
import shlex
import time
import urllib.parse
import urllib.request
import urllib.error

import middleware  # 羽化 SDK: 配置读取(bucketGet)
from sillygirl import sender  # 羽化 SDK: 消息对象(getContent/getUserId/reply/listen)

# ---------------- 配置读取(本机 JSON 优先, 面板 bucket 兜底) ----------------
CFG_FILE = "/data/plugins/.sgcc_config.json"
def cfg(key, default=""):
    short = key.split(".", 1)[1] if "." in key else key
    try:
        d = json.load(open(CFG_FILE, encoding="utf-8"))
        v = d.get(short)
        if v not in (None, ""):
            return str(v).strip()
    except Exception:
        pass
    try:
        v = middleware.bucketGet("sgcc", short)
        if v not in (None, ""):
            return str(v).strip()
    except Exception:
        pass
    return os.environ.get("SGCC_" + short.upper(), default)

def C(k, d=""):
    return cfg("sgcc." + k, d)

QL = lambda: C("ql_url", "http://填你的青龙地址:5700").rstrip("/")
CID = lambda: C("ql_client_id", "")
SEC = lambda: C("ql_client_secret", "")
CRON_ID = lambda: int(C("ql_cron_id", "2352") or 2352)
ADMIN = lambda: [x.strip() for x in C("admin_qq", "").split(",") if x.strip()]

STATE_FILE = "/data/plugins/.sgcc_wait.json"   # 等码状态(按 QQ uid)
WAIT_TTL = 300  # sgcc codeKey 5 分钟
ENV_BACKUP = "/data/plugins/.sgcc_env_backup.json"  # 变量原值留底(可回退)

def _load_wait():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {}

def _save_wait(d):
    try:
        json.dump(d, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass

# ---------------- 青龙 OpenAPI ----------------
def _token():
    u = QL() + "/open/auth/token?client_id=" + urllib.parse.quote(CID()) + "&client_secret=" + urllib.parse.quote(SEC())
    with urllib.request.urlopen(urllib.request.Request(u), timeout=15) as r:
        return (json.load(r).get("data") or {}).get("token", "")

def _api(method, path, body=None, token=None):
    t = token or _token()
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(QL() + path, data=data, method=method,
        headers={"Authorization": "Bearer " + t, "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "yuhua-sgcc"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def _envs(token):
    d = _api("GET", "/open/envs", token=token).get("data") or []
    if isinstance(d, dict):
        d = d.get("data") or []
    return {str(e.get("name")): e for e in d if isinstance(e, dict)}

def _sync_qinglong_env(acc, pwd):
    """把账号密码写进青龙 SGCC_ACCOUNT/SGCC_PASSWORD(带原值留底)。返回提示串。"""
    tok = _token()
    es = _envs(tok)
    backup = {}
    msgs = []
    for name, val in (("SGCC_ACCOUNT", acc), ("SGCC_PASSWORD", pwd)):
        e = es.get(name)
        if not e:
            msgs.append("❌ 青龙里没有变量 %s，请先建好再登录" % name)
            continue
        backup[name] = {"id": e.get("id"), "value": str(e.get("value") or ""),
                        "remarks": str(e.get("remarks") or "")}
        r = _api("PUT", "/open/envs", {"id": e.get("id"), "name": name,
                  "value": val, "remarks": e.get("remarks") or ""}, token=tok)
        msgs.append("%s %s" % ("✓" if r.get("code") == 200 else "✗", name))
    try:
        json.dump({"at": time.time(), "env": backup},
                  open(ENV_BACKUP, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return "  ".join(msgs)

def _set_command_and_run(args):
    """改辅助任务 command -> 触发 run -> 轮询日志 -> 还原 command。返回日志文本。"""
    cid = CRON_ID()
    tok = _token()
    cur = _api("GET", "/open/crons/%d" % cid, token=tok).get("data") or {}
    orig = cur.get("command") or "task 我的/sgcc-checkin/sgcc_checkin.py"
    # 青龙 task 包装 clear_env -> 直接 python3 + source 青龙现成 preload/env.sh
    script = "我的/sgcc-checkin/sgcc_checkin.py"
    new = ("bash -lc '. /ql/shell/preload/env.sh; cd /ql/data/scripts && "
           "python3 %s %s'" % (script, args))
    _api("PUT", "/open/crons", {
        "id": cid, "name": cur.get("name") or "网上国网签到辅助",
        "command": new, "schedule": cur.get("schedule") or "47 7 * * *",
        "labels": cur.get("labels") or []}, token=tok)
    try:
        _api("PUT", "/open/crons/run", [cid], token=tok)
        last = ""
        for _ in range(20):
            time.sleep(2)
            try:
                d = _api("GET", "/open/crons/%d/log" % cid, token=tok)
                log = d.get("data") or ""
                if isinstance(log, dict):
                    log = json.dumps(log, ensure_ascii=False)
            except Exception:
                continue
            seg = str(log).split("## 开始执行")[-1]
            if len(seg) > len(last):
                last = seg
            if "## 完成" in str(log) or "令牌已缓存" in str(log) or "❌" in str(log) or "验证码已发送" in str(log):
                time.sleep(1)
                try:
                    d = _api("GET", "/open/crons/%d/log" % cid, token=tok)
                    log = d.get("data") or ""
                    if isinstance(log, dict):
                        log = json.dumps(log, ensure_ascii=False)
                    last = str(log).split("## 开始执行")[-1]
                except Exception:
                    pass
                break
        return last.strip() or "(日志为空)"
    finally:
        try:
            _api("PUT", "/open/crons", {
                "id": cid, "name": cur.get("name") or "网上国网签到辅助",
                "command": orig, "schedule": cur.get("schedule") or "47 7 * * *",
                "labels": cur.get("labels") or []}, token=tok)
        except Exception:
            pass

def _brief(log):
    lines = [l.strip() for l in log.splitlines() if l.strip()]
    keys = ("验证码已发送", "✅", "❌", "未配置", "登录成功", "令牌已缓存", "新设备",
            "userId", "剩余", "📭", "🔑", "失败", "4006", "先 --send-sms", "签到", "积分")
    picked = [l for l in lines if any(k in l for k in keys)]
    return "\n".join(picked[-8:]) if picked else (lines[-1] if lines else "(无输出)")

# ---------------- 风控码识别 → 分类提示 ----------------
def classify_risk(log):
    """按日志里的返回码/关键字, 把国网返回归到几类, 给『该怎么办』。
    返回 (类型标签, 建议文本) 或 None(没识别出风控/异常)。"""
    s = str(log or "")
    has = lambda *ks: any(k in s for k in ks)
    # 1) 新设备校验(不是坏风控, 已由收码流程兜住)
    if has("4006", "新设备", "先 --send-sms", "logindevice"):
        return ("新设备校验", "服务端认生, 不是封号。按提示把手机短信 6 位发回来即可过, 之后缓存令牌很久不再要。")
    # 2) 今日已签/次数用完(不是风控)
    if has("102007", "次数已用完", "已签到", "今日已"):
        return ("今日已签", "不是风控, 是今天已经签过/次数用完。别重登, 明天到点自动跑即可。")
    # 3) 设备指纹 / 出口IP 假值 被判 RK001
    if has("RK001", "app_guid", "全 0", "devciceIp", "127.0.0.1"):
        return ("设备指纹/IP假值", "AppGuid 或 SGCC_DEVICE_IP 是假值(现在默认 127.0.0.1), 服务端判 RK 错误。"
                " 别高频重跑; 填真实出口 IP、别删 .state/app_guid.txt, 隔一阵再试。")
    # 4) 令牌过期(正常续期, 非风控)
    if has("令牌已过期", "token 已过期", "重新获取"):
        return ("令牌过期", "正常到期, 不是风控。重发「国网登录」走一遍, 或等每天定时自动续。")
    # 5) 出口 IP 被记账 / 疑似整体被限(最麻烦, 收码救不了)
    if has("超时", "timed out", "Connection", "500", "502", "503", "504", "[Errno", "请求异常", "整体"):
        return ("疑似出口IP被记账", "接口整体超时/5xx, 像家宽出口 IP 被风控(参考习酒那次)。"
                " 收验证码/重登都救不了; 这脚本 requests 直连、没代理支持, 不能套 XJ_PROXY_API。"
                " 要么停一两天等它自己放出来, 要么换出口(真挂代理只给本脚本专用变量、任务内联, 绝不配青龙全局)。别连点重跑越描越黑。")
    # 6) 登录被拒但未带明确码 → 通用, 提示别连打
    if has("登录失败", "❌ 未配置", "校验未通过", "密码", "no_password"):
        return ("登录未通过", "看上面日志的关键行: 多半是账号/密码没填对或验证码错/过期。"
                " 别短时间连发, 等一下重发「国网登录」; 验证码 5 分钟内有效。")
    return None

def _risk_hint(log):
    """把分类结果拼成一小段人话, 没有识别到就返回空串。"""
    r = classify_risk(log)
    if not r:
        return ""
    tag, advice = r
    return "\n\n【判定】%s\n👉 %s" % (tag, advice)

# ---------------- 主逻辑 ----------------
async def _get(fn):
    try:
        r = fn()
        if asyncio.iscoroutine(r):
            return await r
        return r
    except Exception:
        return ""

async def _listen(ms):
    """等一条后续消息, 统一取文本。失败/超时返回 ''。"""
    try:
        r = sender.listen(ms)
        if asyncio.iscoroutine(r):
            r = await r
        # listen 可能返回 str 或对象(取 getContent/content)
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

async def _reply(text, recall=True):
    try:
        r = sender.reply(text)
        if asyncio.iscoroutine(r):
            await r
    except Exception:
        pass

async def main():
    content = str(await _get(sender.getContent) or "").strip()
    uid = str(await _get(sender.getUserId) or "").strip()
    low = content.lower()
    adm = ADMIN()
    if adm and uid not in adm:
        return

    # ---- 国网登录: 两步式对话向导 ----
    if low == "国网登录":
        await _reply("【网上国网登录】\n请发送账号（手机号）：\n（发 Q 取消）")
        acc = await _listen(120000)  # 2 分钟
        if not acc or low in ("q", "取消"):
            await _reply("已取消。")
            return
        # 校验: 11 位手机号
        if not re.fullmatch(r"1[3-9]\d{9}", acc):
            await _reply("❌ 账号格式不对，应是 11 位手机号。请重新发「国网登录」再来一次。")
            return
        await _reply("✅ 收到账号。\n请输入密码（App 登录密码）：\n（发 Q 取消，密码不会显示在群/日志）")
        pwd = await _listen(120000)
        if not pwd or pwd.lower() in ("q", "取消"):
            await _reply("已取消。")
            return
        await _reply("⏳ 正在同步账号密码到青龙变量…")
        sync = await asyncio.to_thread(_sync_qinglong_env, acc, pwd)
        if "✗" in sync or "❌" in sync:
            await _reply("❌ 同步青龙变量失败：\n" + sync)
            return
        await _reply("✅ 变量已同步：" + sync + "\n⏳ 现在跑一次国网签到…")
        log = await asyncio.to_thread(_set_command_and_run, "")  # all = 默认完整流程
        # 撞到 4006 新设备校验 -> 提示收码并进入等码
        if "4006" in log or "新设备" in log or "先 --send-sms" in log or "send-sms" in log:
            w = _load_wait()
            w[uid] = time.time()
            _save_wait(w)
            await _reply(
                "📱 需要新设备校验，验证码已发送到你手机（5分钟内有效）\n"
                "收到短信后，把 6 位数字直接发给我（或「国网 123456」）\n"
                + _brief(log))
        elif ("登录成功" in log or "令牌已缓存" in log or "✅ 完成" in log
              or ("签到结果：✅" in log and "❌" not in log)):
            await _reply("✅ 国网登录/签到完成，令牌已缓存\n" + _brief(log) + "\n之后签到任务会自动复用，不用再收码。")
        else:
            await _reply("国网跑完了，结果如下：\n" + _brief(log) + _risk_hint(log))
        return

    # ---- 国网状态 ----
    if low == "国网状态":
        await _reply("⏳ 查询本地令牌缓存…")
        log = await asyncio.to_thread(_set_command_and_run, "--status")
        await _reply("【国网令牌状态】\n" + _brief(log) + _risk_hint(log))
        return

    # ---- 国网帮助 ----
    if low == "国网帮助":
        await _reply(
            "【国网登录/验证码助手】\n"
            "· 国网登录 → 两步输入手机号+App密码 → 同步青龙 → 跑一次 → 需要就收码\n"
            "· 国网发码 → 单独触发新设备校验短信(发到你手机)\n"
            "· 手机收到 6 位数字后，直接发这个数字(或「国网 123456」)\n"
            "· 国网状态 → 查本地令牌缓存剩余天数\n"
            "⚠️ 验证码在你手机, QQ 机器人收不到短信; 需你手工把码发回\n"
            "⚠️ 仅首次/令牌过期撞 4006 才需要; 5 分钟内有效\n"
            "⚠️ 有令牌缓存时国网登录也不会再要验证码")
        return

    # ---- 国网发码(老入口) ----
    if low == "国网发码":
        await _reply("⏳ 正在触发国网新设备校验短信…")
        log = await asyncio.to_thread(_set_command_and_run, "--send-sms")
        if "验证码已发送" in log:
            w = _load_wait()
            w[uid] = time.time()
            _save_wait(w)
            await _reply("📱 验证码已发到你手机(5分钟内有效)\n把 6 位数字直接发给我，或发「国网 123456」\n" + _brief(log))
        else:
            await _reply("❌ 发码未成功：\n" + _brief(log) + _risk_hint(log))
        return

    # ---- 收到 6 位码(国网登录向导 或 国网发码 之后都走这里) ----
    # rule 里 \d{6} 会放进纯数字; 必须先用「等码态」闸住, 否则会抢别的插件消息
    m = re.fullmatch(r"(?:国网\s*)?(\d{6})", content)
    if not m:
        return
    code = m.group(1)
    w = _load_wait()
    at = float(w.get(uid, 0) or 0)
    if not at or time.time() - at > WAIT_TTL:
        # 不在等码态: 纯数字静默返回(不抢消息); 带「国网」前缀的才提示
        if content.startswith("国网"):
            await _reply("⏰ 没有进行中的登录/发码(或已超5分钟)。发「国网登录」或「国网发码」重新来。")
        return
    await _reply("⏳ 正在用验证码完成登录并缓存令牌…")
    log = await asyncio.to_thread(_set_command_and_run, "--code " + code)
    w.pop(uid, None)
    _save_wait(w)
    if "登录成功" in log or "令牌已缓存" in log:
        await _reply("✅ 国网登录成功，令牌已缓存\n" + _brief(log) + "\n之后签到任务会自动复用，不用再收码。")
    else:
        await _reply("❌ 校验未通过：\n" + _brief(log) + _risk_hint(log) + "\n可重新「国网登录」或「国网发码」再试。")

asyncio.run(main())
