# -*- coding: utf-8 -*-
"""推荐算法系统：今日最佳推荐。

思路：
- 用【全部真实采集数据】为每个型号建立市场行情基准（中位数、均值、Z分数）。
- 对每一条商品计算"性价比评分"：
    * 价格相对该型号市场中位数的折扣幅度（越便宜越分高，但过低视为可疑）
    * 价格合理度：排除极端离群（明显低于/高于市场，可能为引流或整机）
- 综合打分排序，返回 Top N 推荐（而非单一"最低价"）。
- 通过折扣率与可信度加权，找出"物超所值且靠谱"的购入机会。
"""

import statistics
from datetime import datetime

import database as db


def _get_watched_models():
    """从设置读取关注型号列表。"""
    from crawler import get_settings
    cfg = get_settings()
    return {x.strip() for x in cfg.get("watched_models", "").split(",") if x.strip()}


def _compute_recommendations(limit=5, min_discount=0.10, watched=None):
    """从全量真实数据计算今日最佳推荐，返回排序后的列表。
    watched: 关注型号集合，命中则评分加权（优先推送）。"""
    rows = [r for r in db.all_prices(platform="闲鱼") if "演示" not in (r.get("title") or "")]
    if not rows:
        return []
    watched = watched or set()

    # 每个型号的行情基准
    prices_by_model = {}
    for r in rows:
        prices_by_model.setdefault(r["model"], []).append(r["price"])
    stats_by_model = {}
    for m, ps in prices_by_model.items():
        stats_by_model[m] = {"median": statistics.median(ps), "count": len(ps)}

    # 按 URL 去重，保留最新价格
    by_url = {}
    for r in rows:
        u = r.get("url")
        if not u:
            continue
        if u not in by_url or r["id"] > by_url[u]["id"]:
            by_url[u] = r
    items = list(by_url.values())

    scored = []
    for r in items:
        st = stats_by_model.get(r["model"])
        # 单样本无法形成市场基准，不能声称它是“优惠”。
        if not st or st["median"] <= 0 or st["count"] < 2:
            continue
        med = st["median"]
        price = r["price"]
        discount = (med - price) / med          # >0 便宜，<0 贵

        # 价格可信度：距离中位数越远越可疑（过低=引流，过高=整机/配件）
        median_dist = abs(price - med) / med
        price_ratio = price / med
        # 极低价常是引流/配件，极高价常是整机；保留在数据表，但不进入推荐。
        if price_ratio < 0.55 or price_ratio > 1.75:
            continue
        # 折扣奖励（便宜加分），但大量偏离市场价时扣分（可疑）
        value_score = discount * 1.0
        suspicious_penalty = 0.0
        if median_dist > 0.25:
            suspicious_penalty = (median_dist - 0.25) * 1.6
        sample_bonus = min(0.05, len(prices_by_model[r["model"]]) * 0.005)

        score = value_score - suspicious_penalty + sample_bonus
        # 关注型号加权：优先推送关注型号的低价好货
        if r["model"] in watched:
            score += 0.15

        scored.append({
            "model": r["model"],
            "series": r["series"],
            "platform": r["platform"],
            "price": price,
            "title": r["title"],
            "url": r["url"],
            "ts": r["ts"],
            "market_median": round(med, 0),
            "discount": round(discount, 3),
            "score": round(score, 3),
            "samples": len(prices_by_model[r["model"]]),
        })

    # 排序：评分降序；只保留明显低于市场价（有真实折扣）的推荐
    scored.sort(key=lambda x: x["score"], reverse=True)
    good = [s for s in scored if s["discount"] >= min_discount and s["score"] > 0]
    market_fallback = [s for s in scored if s["discount"] >= 0 and s["score"] > 0]
    return (good if good else market_fallback)[:limit]


def today_recommendations(limit=5):
    """今日最佳推荐（对外接口）。返回带推荐理由的列表，关注型号优先。"""
    watched = _get_watched_models()
    recs = _compute_recommendations(limit=limit, watched=watched)
    # 附加推荐理由
    for i, r in enumerate(recs):
        reason = "比该型号市场参考价"
        if r["discount"] > 0:
            reason += f"（¥{int(r['market_median'])}）便宜 {round(r['discount']*100)}%"
            if r["discount"] >= 0.15:
                reason += "，性价比极高"
            elif r["discount"] >= 0.08:
                reason += "，明显划算"
            else:
                reason += "，略有优惠"
        else:
            reason += "当前行情价"
        if r["model"] in watched:
            reason += "（已关注型号）"
        r["reason"] = reason
        r["rank"] = i + 1
        r["watched"] = r["model"] in watched
    return {"date": datetime.now().strftime("%Y-%m-%d"), "count": len(recs),
            "watched": sorted(watched), "recommendations": recs}
