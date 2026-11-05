# -*- coding: utf-8 -*-
"""共享模块：央视频「扫码后备注 → 落地」。莹莹棒(总入口)调用。

落地 = 合并写青龙 YSP_COOKIE + 绑定表按 vuserid 挂账号 + 触发积分任务。
状态文件 /data/plugins/.ysp_pending.json：扫码成功写入，备注确定后删除(=会话结束)。
"""
import json, os, time, urllib.parse, urllib.request

HERE = "/data/plugins"
PENDING = os.path.join(HERE, ".ysp_pending.json")
CFG = os.path.join(HERE, ".ysp_cfg.json")


def _cfg(key, default=""):
    """读配置：优先 middleware bucket(带 ysp. 前缀，青龙凭据在这里)，回退本地文件。"""
    try:
        import middleware
        for k in (key, "ysp." + key):
            try:
                v = middleware.bucketGet("ysp", k)
                if v not in (None, ""):
                    return str(v)
            except Exception:
                pass
    except Exception:
        pass
    try:
        d = json.load(open(CFG, encoding="utf-8"))
        return str(d.get(key, default))
    except Exception:
        return default


def _http(url, method="GET", body=None, headers=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ql_tok():
    base = (_cfg("ql_url") or "http://127.0.0.1:5700").rstrip("/")
    cid, sec = _cfg("ql_client_id"), _cfg("ql_client_secret")
    d = _http("%s/open/auth/token?client_id=%s&client_secret=%s" % (base, cid, sec))
    return ((d.get("data") or {}).get("token") or "")


def _ql(method, path, body=None):
    base = (_cfg("ql_url") or "http://127.0.0.1:5700").rstrip("/")
    return _http(base + path, method, body, {"Authorization": "Bearer " + _ql_tok()})


def _ql_get_var(name):
    try:
        for e in (_ql("GET", "/open/envs?searchValue=" + urllib.parse.quote(name)).get("data") or []):
            if str(e.get("name")) == name:
                return str(e.get("value") or "")
    except Exception:
        pass
    return ""


def _ql_set_var(name, value):
    d = _ql("GET", "/open/envs?searchValue=" + urllib.parse.quote(name))
    for e in (d.get("data") or []):
        if str(e.get("name")) == name:
            r = _ql("PUT", "/open/envs", {"id": e["id"], "name": name, "value": value})
            return "updated", e["id"], r
    r = _ql("POST", "/open/envs", [{"name": name, "value": value, "remarks": "央视频助手写入"}])
    return "created", None, r


def _merge_cookie(newck, vuid, tag):
    """按 vuserid 去重合并到 YSP_COOKIE（多行 名#cookie）。返回 (act,eid,total)"""
    var = _cfg("ql_var_name") or "YSP_COOKIE"
    raw = _ql_get_var(var) or ""
    lines, hit = [], False
    for ln in str(raw).splitlines():
        ln = ln.strip().strip(",;")
        if not ln:
            continue
        if "#" in ln and "=" not in ln.split("#", 1)[0]:
            name, ck = ln.split("#", 1)
        else:
            name, ck = "", ln
        old = ""
        for seg in str(ck).replace("\n", ";").split(";"):
            seg = seg.strip()
            if seg.startswith("vuserid="):
                old = seg.split("=", 1)[1]; break
        if not hit and old == str(vuid):
            lines.append("%s#%s" % (tag, newck)); hit = True
        else:
            lines.append("%s#%s" % (name or tag, ck))
    if not hit:
        _names = [ln.split("#", 1)[0] for ln in lines if "#" in ln]
        _t, _i = str(tag), 2
        while _t in _names:
            _t, _i = "%s%d" % (tag, _i), _i + 1
        lines.append("%s#%s" % (_t, newck))
    act, eid, _ = _ql_set_var(var, "\n".join(lines))
    return act, eid, len(lines)


def _load_map():
    try:
        m = json.loads(_cfg("map") or "{}")
        if not isinstance(m, dict): return {}
        out = {}
        for qq, node in m.items():
            if isinstance(node, dict) and "accounts" in node:
                out[str(qq)] = node
            elif isinstance(node, dict) and node.get("vuserid"):
                v = str(node["vuserid"])
                out[str(qq)] = {"accounts": {v: {"ck": node.get("ck", ""),
                    "remark": node.get("remark", str(qq)), "ts": node.get("ts", 0), "vuserid": v}}}
        return out
    except Exception:
        return {}


def _save_map(m):
    try:
        d = json.load(open(CFG, encoding="utf-8"))
    except Exception:
        d = {}
    d["map"] = json.dumps(m, ensure_ascii=False)
    json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False)
    # 同步写 bucket（带前缀，面板可查）
    try:
        import middleware
        middleware.bucketSet("ysp", "map", d["map"]); middleware.bucketSet("ysp", "ysp.map", d["map"])
    except Exception:
        pass


def _ck_uid(ck):
    for seg in str(ck).replace("\n", ";").split(";"):
        if seg.strip().startswith("vuserid="):
            return seg.strip().split("=", 1)[1]
    return ""


def pending_load():
    try:
        return json.load(open(PENDING, encoding="utf-8"))
    except Exception:
        return {}


def pending_save(d):
    try:
        json.dump(d or {}, open(PENDING, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def pending_clear():
    try:
        if os.path.exists(PENDING): os.remove(PENDING)
    except Exception:
        pass


def finalize(uid, plats, ck, vuid, qid, remark):
    """备注确定 → 落地（同步，供莹莹棒 main 用）。返回给用户的汇总文案。"""
    # ★ tag 必须每号唯一：用备注名，别用 QQ 号(否则 ysp.py 回写把多号刷成同一个)
    tag = remark or str(vuid)
    act, eid, total = _merge_cookie(ck, vuid, tag)
    m = _load_map()
    node = m.setdefault(str(uid), {"accounts": {}})
    accs = node.setdefault("accounts", {})
    old = accs.get(str(vuid))
    if old and old.get("remark") and old["remark"] != str(uid):
        remark = old["remark"]     # 保留用户自定义备注
    accs[str(vuid)] = {"ck": ck, "remark": remark or str(uid),
                       "ts": int(time.time()), "vuserid": str(vuid)}
    _save_map(m)
    cnt = len(accs)
    # 触发积分任务
    tid = _cfg("task_id") or "2341"
    try:
        _ql("PUT", "/open/crons/enable", [int(tid)])
        _ql("PUT", "/open/crons/run", [int(tid)])
        run = "🎬 已在后台开始跑今日积分任务，稍后发「央视频抽奖」查看结果。"
    except Exception as e:
        run = "⚠️ 积分任务触发失败：%s" % e
    return ("✅ 央视频登录成功\n"
            "🏷 备注：%s\n"
            "🟢 已合并写入青龙变量：YSP_COOKIE（%s，现共 %d 个账号）\n"
            "🔗 已绑定到你的 QQ：你现在共绑定 %d 个央视频账号\n%s" % (
                remark or str(uid), act, total, cnt, run))
