# -*- coding: utf-8 -*-
"""Flask 入口：统一 Web 控制台。"""
import os
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request

import database as db
import charts
from crawler import ACTIVE_PLATFORM_LABEL, ACTIVE_PLATFORM_NAMES, crawler, get_settings
from gpus import (PLATFORM_API_DOC, detect_5090_variant, is_desktop_gpu,
                  listing_rejection_reason)

app = Flask(__name__)
db.init_db()
db.reclassify_5090_rows(detect_5090_variant)
_startup_settings = get_settings()
db.purge_legacy_noise(
    lambda title: listing_rejection_reason(title) or ("移动版显卡" if not is_desktop_gpu(title) else ""),
    float(_startup_settings["abs_min"]), float(_startup_settings["low_ratio"]),
    float(_startup_settings["high_ratio"]),
)
ACTIVE_PLATFORM_KEY = ACTIVE_PLATFORM_NAMES[0]
ACTIVE_PLATFORM_KEYS = frozenset(ACTIVE_PLATFORM_NAMES)


@app.before_request
def protect_local_mutations():
    """拒绝来自其它网页 Origin 的本地写操作，降低 localhost CSRF 风险。"""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    origin = request.headers.get("Origin")
    if not origin:
        return None
    parsed = urlparse(origin)
    expected = f"{request.scheme}://{request.host}"
    if f"{parsed.scheme}://{parsed.netloc}" != expected:
        return jsonify({"ok": False, "msg": "拒绝跨站写操作"}), 403
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
    )
    return response


@app.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------- 控制开关
@app.route("/api/control/start", methods=["POST"])
def control_start():
    """启动浏览器（本项目专用持久化 profile，复用已登录会话）。"""
    from crawler import manager, CHROME_PATH, PROFILE_DIR, get_settings
    if manager.is_ready:
        return jsonify({"ok": True, "msg": "浏览器已在运行"})
    import threading
    def _boot():
        try:
            mode = get_settings().get("browser_mode", "minimized")
            ok, res = manager.start_browser(
                headless=(mode == "silent"), minimized=(mode == "minimized")
            )
            if not ok:
                crawler.last_error = str(res.get("msg", ""))
                crawler.status = "error"
                return
            crawler.status = "browser_ready"
            # 自动校验各平台登录态（复用持久化profile里的登录状态）
            for pc in crawler.platforms:
                if pc.name not in ACTIVE_PLATFORM_KEYS:
                    continue
                try:
                    ok2, res2 = manager.check(pc)
                    pc.logged_in = bool(res2) if ok2 else False
                except Exception:
                    pc.logged_in = False
        except Exception as e:
            crawler.last_error = str(e)
            crawler.status = "error"
    threading.Thread(target=_boot, daemon=True).start()
    return jsonify({"ok": True, "msg": "正在启动浏览器（使用本项目专用持久化登录profile，非无痕）…",
                    "chrome": CHROME_PATH, "profile": PROFILE_DIR})


@app.route("/api/login/<platform>", methods=["POST"])
def login_platform(platform):
    """在已启动的浏览器中，手动打开指定平台的登录页供扫码。
    完全手动：只打开该平台，不会自动跳其它平台。"""
    from crawler import manager
    if platform not in ACTIVE_PLATFORM_KEYS:
        return jsonify({"ok": False, "msg": "当前仅支持闲鱼"}), 404
    if not manager.is_ready:
        return jsonify({"ok": False, "msg": "请先点击「启动浏览器」"}), 400
    ok, res = manager.open_login_page(platform)
    if not ok:
        return jsonify({"ok": False, "msg": res.get("msg", "打开失败")}), 500
    return jsonify({
        "ok": True,
        "msg": res["msg"],
        "url": res["url"],
        "qr": res.get("qr"),
        "screenshot": res.get("screenshot"),
    })


@app.route("/api/control/check", methods=["POST"])
def control_check():
    """校验平台登录状态。可传 {platform} 只校验单个；不传则校验全部。
    检测页打开后立即关闭，不留多余页面。"""
    from crawler import manager
    if not manager.is_ready:
        return jsonify({"ok": False, "msg": "浏览器未启动"}), 400
    data = request.get_json(silent=True) or {}
    only = data.get("platform")
    if only and only not in ACTIVE_PLATFORM_KEYS:
        return jsonify({"ok": False, "msg": "当前仅支持闲鱼"}), 400
    targets = [p for p in crawler.platforms if p.name in ACTIVE_PLATFORM_KEYS and (not only or p.name == only)]
    try:
        results = []
        for pc in targets:
            try:
                ok, res = manager.check(pc)   # worker 内检测并关闭检测页
                pc.logged_in = bool(res) if ok else False
            except Exception:
                pc.logged_in = False
            results.append({"name": pc.name, "label": pc.label, "logged_in": pc.logged_in})
        return jsonify({"ok": True, "platforms": results})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/api/control/start_crawl", methods=["POST"])
def control_start_crawl():
    """开始一次闲鱼采集；专用浏览器由采集线程自动启动并在结束后退出。"""
    ok = crawler.start()
    return jsonify({"ok": ok, "status": crawler.status, "mode": "browser",
                    "platform": ACTIVE_PLATFORM_KEY,
                    "msg": "正在启动浏览器并采集" if ok else "已有采集任务正在运行"}), (200 if ok else 409)


@app.route("/api/control/pause", methods=["POST"])
def control_pause():
    ok = crawler.pause()
    return jsonify({"ok": ok, "status": crawler.status,
                    "msg": "已暂停" if ok else "当前任务不可暂停"}), (200 if ok else 409)


@app.route("/api/control/resume", methods=["POST"])
def control_resume():
    ok = crawler.resume()
    return jsonify({"ok": ok, "status": crawler.status,
                    "msg": "已继续" if ok else "当前任务不可继续"}), (200 if ok else 409)


@app.route("/api/control/stop", methods=["POST"])
def control_stop():
    crawler.stop()
    # 未处于采集线程时，“停止”也负责关闭用户手动提前打开的专用浏览器。
    from crawler import manager
    import threading
    threading.Thread(target=manager.close, daemon=True).start()
    return jsonify({"ok": True, "status": crawler.status, "msg": "采集已停止，浏览器正在退出"})


@app.route("/api/status")
def status():
    login_state = []
    for pc in crawler.platforms:
        if pc.name not in ACTIVE_PLATFORM_KEYS:
            continue
        login_state.append({"platform": pc.name, "logged_in": pc.logged_in,
                            "label": pc.label})
    return jsonify({
        "status": crawler.status,
        "rounds": crawler.rounds,
        "items_found": db.current_price_count(ACTIVE_PLATFORM_LABEL),
        "session_items_found": crawler.items_found,
        "last_run": crawler.last_run,
        "last_error": crawler.last_error,
        "api_docs": {},
        "login_state": login_state,
        "browser_ready": __import__("crawler").manager.is_ready,
        "progress": round(float(crawler.progress or 0), 3),
        "browser_mode": __import__("crawler").get_settings().get("browser_mode", "silent"),
        "crawl_mode": "browser",
        "api_ready": [],
        "platform": ACTIVE_PLATFORM_KEY,
        "platform_label": ACTIVE_PLATFORM_LABEL,
    })


@app.route("/api/api_config", methods=["GET", "POST"])
def api_config():
    """官方开放平台 API 配置的读写。"""
    import api_panel
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        public = api_panel.save_api_config(data)
    else:
        public = api_panel.public_api_config()
    return jsonify({"ok": True, **public, "api_docs": PLATFORM_API_DOC})


# ------------------------------------------------------- 数据
@app.route("/api/prices")
def prices():
    """全部真实商品，按型号归类（去重，非仅最低价）。"""
    rows = db.distinct_items(platform=ACTIVE_PLATFORM_LABEL)
    grouped = {"RTX 50 系": [], "RTX 40 系": [], "RTX 30 系": []}
    for r in rows:
        grouped.setdefault(r["series"], []).append(r)
    return jsonify({"data": rows, "grouped": grouped})


@app.route("/api/trend")
def trend():
    model = request.args.get("model", "").strip()
    platform = request.args.get("platform") or ACTIVE_PLATFORM_LABEL
    if not model or len(model) > 40 or any(ch in model for ch in "\r\n\x00"):
        return jsonify({"ok": False, "msg": "请选择有效型号"}), 400
    if platform != ACTIVE_PLATFORM_LABEL:
        return jsonify({"ok": False, "msg": "平台无效"}), 400
    rel = charts.price_trend(model, platform)
    return jsonify({"chart": rel})


@app.route("/api/series_chart")
def series_chart():
    rel = charts.series_summary()
    return jsonify({"chart": rel})


@app.route("/api/cheapest")
def cheapest():
    """今日最佳推荐（简版）：返回最划算的一条。推荐算法见 recommend.py。"""
    import recommend
    rec = recommend.today_recommendations(limit=1)
    if not rec["recommendations"]:
        return jsonify({"cheapest": None})
    top = rec["recommendations"][0]
    return jsonify({"cheapest": top, "is_demo": False,
                    "deal": {"median": top["market_median"], "discount": top["discount"]}})


@app.route("/api/recommend")
def recommend_api():
    """今日最佳推荐（完整版）：返回 Top N + 理由。"""
    import recommend
    return jsonify(recommend.today_recommendations(limit=5))


@app.route("/api/stats")
def stats():
    """统计指标：用【去重后的当前真实商品】计算（避免多轮采集重复计数）。"""
    import statistics
    rows = db.distinct_items(platform=ACTIVE_PLATFORM_LABEL)   # 闲鱼去重真实商品
    prices = [r["price"] for r in rows if r.get("price")]
    stats_info = {
        "count": len(rows),
        "real_count": len(rows),
        "samples": len(prices),
        "mean": round(statistics.mean(prices), 0) if prices else 0,
        "median": round(statistics.median(prices), 0) if prices else 0,
        "min": round(min(prices), 0) if prices else 0,
        "max": round(max(prices), 0) if prices else 0,
        "models_covered": len(set(r["model"] for r in rows)),
        "models_total": len(__import__("gpus").get_all_models()),
        "platform_dist": {},
        "series_dist": {},
    }
    for r in rows:
        stats_info["platform_dist"][r["platform"]] = stats_info["platform_dist"].get(r["platform"], 0) + 1
        stats_info["series_dist"][r["series"]] = stats_info["series_dist"].get(r["series"], 0) + 1
    per_model = {}
    for r in rows:
        per_model.setdefault(r["model"], []).append(r["price"])
    stats_info["per_model"] = {
        m: {"mean": round(statistics.mean(ps), 0), "median": round(statistics.median(ps), 0),
            "min": round(min(ps), 0), "max": round(max(ps), 0), "count": len(ps)}
        for m, ps in per_model.items()
    }
    stats_info["chart"] = charts.per_model_chart(stats_info)
    return jsonify(stats_info)


@app.route("/api/export")
def export_csv():
    """导出全部真实价格数据为 CSV。"""
    import csv, io
    items = db.distinct_items(platform=ACTIVE_PLATFORM_LABEL)
    def safe_cell(value):
        text = "" if value is None else str(value)
        return "'" + text if text.startswith(("=", "+", "-", "@")) else text
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["型号", "系列", "平台", "价格", "标题", "链接", "采集时间"])
    for r in items:
        w.writerow([safe_cell(r["model"]), safe_cell(r["series"]), safe_cell(r["platform"]),
                    r["price"], safe_cell(r.get("title", "")), safe_cell(r.get("url", "")),
                    safe_cell(r.get("ts", ""))])
    from flask import Response
    return Response(
        "\ufeff" + buf.getvalue(),           # BOM 让 Excel 正确识别 UTF-8
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=gpu_prices.csv"},
    )


@app.route("/api/clear", methods=["POST"])
def clear_data():
    """清空所有价格数据（保留配置）。"""
    if crawler.status in {"running", "paused"}:
        return jsonify({"ok": False, "msg": "请先停止采集再清空数据"}), 409
    db.clear_price_data()
    crawler.items_found = 0
    return jsonify({"ok": True, "msg": "已清空全部价格数据"})


@app.route("/api/log")
def crawl_log():
    """返回采集日志：实时任务日志 + 各型号统计。"""
    from gpus import get_all_models
    logs = []
    for g in get_all_models():
        gname = g["name"]
        key = f"debug_{ACTIVE_PLATFORM_KEY}_{gname.replace(' ', '')}"
        val = db.get_state(key)
        if val:
            logs.append({"model": gname, "platform": ACTIVE_PLATFORM_LABEL, "info": val,
                         "ts": db.get_state("last_run", "")})
    logs.sort(key=lambda x: (x["model"], x["platform"]))
    return jsonify({
        "logs": logs,
        "task_log": getattr(crawler, "task_history", []),
        "current_task": getattr(crawler, "current_task", ""),
        "status": crawler.status,
        "progress": round(float(crawler.progress or 0), 3),
    })



@app.route("/api/history")
def history():
    model = request.args.get("model", "")
    rows = db.history(model, ACTIVE_PLATFORM_LABEL)
    return jsonify({"data": rows})


@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    from crawler import get_settings, save_settings
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        try:
            saved = save_settings(data)
        except ValueError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 400
        return jsonify({"ok": True, "settings": saved})
    return jsonify({"ok": True, "settings": get_settings()})


@app.route("/api/browser/mode", methods=["POST"])
def browser_mode():
    """切换浏览器模式并重启：silent=静默无头 | visible=可视化(登录) | minimized=可视化+最小化(推荐)。"""
    from crawler import manager, save_settings, get_settings
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "")
    if mode not in ("silent", "visible", "minimized"):
        return jsonify({"ok": False, "msg": "模式必须是 silent / visible / minimized"}), 400
    was_running = crawler.status == "running"
    if was_running:
        crawler.pause()
    save_settings({"browser_mode": mode})
    headless = (mode == "silent")
    minimized = (mode == "minimized")
    import threading
    def _reboot():
        try:
            ok, res = manager.reboot(headless, minimized)
            if not ok:
                crawler.last_error = str(res.get("msg", ""))
            elif was_running:
                crawler.resume()
        except Exception as e:
            crawler.last_error = str(e)
    threading.Thread(target=_reboot, daemon=True).start()
    label = {"silent": "静默", "visible": "可视化", "minimized": "最小化"}[mode]
    return jsonify({"ok": True, "msg": f"浏览器已切换为{label}模式", "mode": mode})


@app.route("/api/models", methods=["GET", "POST", "DELETE", "PATCH"])
def models_api():
    """型号管理：支持添加/删除自定义型号，以及隐藏/恢复内置型号。"""
    from gpus import (
        get_all_models, get_hidden_builtin_models, add_custom_model,
        remove_custom_model, hide_builtin_model, restore_builtin_model,
    )
    if request.method == "GET":
        models = get_all_models()
        rows = db.distinct_items(platform=ACTIVE_PLATFORM_LABEL)
        has_data = {}
        for r in rows:
            has_data.setdefault(r["model"], 0)
            has_data[r["model"]] += 1
        for m in models:
            m["has_data"] = has_data.get(m["name"], 0)
        return jsonify({"ok": True, "models": models,
                        "hidden_models": get_hidden_builtin_models()})
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        ok, msg = add_custom_model(data.get("name", ""))
        return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)
    if request.method == "DELETE":
        data = request.get_json(silent=True) or {}
        ok, msg = remove_custom_model(data.get("name", ""))
        return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)
    if request.method == "PATCH":
        data = request.get_json(silent=True) or {}
        action = str(data.get("action", "")).strip().casefold()
        name = data.get("name", "")
        if action == "hide":
            ok, msg = hide_builtin_model(name)
        elif action == "restore":
            ok, msg = restore_builtin_model(name)
        else:
            return jsonify({"ok": False, "msg": "action 必须是 hide 或 restore"}), 400
        if ok and action == "hide":
            # 隐藏后从采集范围和关注列表移除，避免配置引用不可见型号。
            from crawler import get_settings, save_settings
            cfg = get_settings()
            hidden_name = next((part.strip() for part in str(name).split(",") if part.strip()), str(name).strip())
            selected = [part.strip() for part in cfg["selected_models"].split(",") if part.strip()]
            watched = [part.strip() for part in cfg["watched_models"].split(",") if part.strip()]
            selected = [part for part in selected if part.casefold() != hidden_name.casefold()]
            watched = [part for part in watched if part.casefold() != hidden_name.casefold()]
            save_settings({"selected_models": ",".join(selected), "watched_models": ",".join(watched)})
        return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)


@app.route("/api/debug/paginate", methods=["POST"])
def debug_paginate():
    """诊断接口：跑一遍闲鱼翻页，返回抓到多少卡片（验证翻页效果）。"""
    from crawler import manager
    if not manager.is_ready:
        return jsonify({"ok": False, "msg": "浏览器未启动"}), 400
    data = request.get_json(silent=True) or {}
    kw = data.get("kw", "RTX5080")
    ok, res = manager.debug_paginate(kw)
    return jsonify({"ok": ok, "result": res})


@app.route("/api/debug/fetch", methods=["POST"])
def debug_fetch():
    """诊断接口：返回闲鱼搜索页少量商品卡片样本，不写入价格库。"""
    from crawler import GoofishCrawler, manager
    if not manager.is_ready:
        return jsonify({"ok": False, "msg": "浏览器未启动"}), 400
    data = request.get_json(silent=True) or {}
    kw = str(data.get("kw", "RTX 5090")).strip()[:80]
    if not kw:
        return jsonify({"ok": False, "msg": "关键词不能为空"}), 400
    ok, res = manager.debug_fetch(GoofishCrawler(), kw)
    return jsonify({"ok": ok, "result": res})


if __name__ == "__main__":
    db.init_db()
    # 初始化演示用系列信息（保证 series 归类的兜底）
    from gpus import GPU_MODELS
    seen = set()
    for g in GPU_MODELS:
        if g["generation"] in seen:
            continue
        seen.add(g["generation"])
    # 生产/本地均通过本入口启动，避免 `flask run` 的 asyncio 适配层与
    # crawler.py 使用的 Playwright Sync API 冲突；端口可按需覆盖。
    host = os.environ.get("GPU_MONITOR_HOST", "127.0.0.1")
    port = int(os.environ.get("GPU_MONITOR_PORT", "5000"))
    app.run(host=host, port=port, debug=False, use_reloader=False)
