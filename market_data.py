# -*- coding: utf-8 -*-
"""按数据库版本缓存行情快照，统一提供价格、统计和型号索引。"""
from collections import Counter, defaultdict
from dataclasses import dataclass
import statistics
import threading

import database as db


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    database_path: str
    platform: str
    revision: int
    rows: tuple
    model_prices: dict
    model_counts: dict
    stats: dict


_lock = threading.Lock()
_snapshot = None


def _build_snapshot(platform, revision):
    rows = tuple(db.distinct_items(platform=platform))
    prices = []
    model_prices = defaultdict(list)
    platform_dist = Counter()
    series_dist = Counter()
    for row in rows:
        price = row.get("price")
        if price:
            numeric_price = float(price)
            prices.append(numeric_price)
            model_prices[row["model"]].append(numeric_price)
        platform_dist[row["platform"]] += 1
        series_dist[row["series"]] += 1

    frozen_model_prices = {model: tuple(values) for model, values in model_prices.items()}
    per_model = {
        model: {
            "mean": round(statistics.fmean(values), 0),
            "median": round(statistics.median(values), 0),
            "min": round(min(values), 0),
            "max": round(max(values), 0),
            "count": len(values),
        }
        for model, values in frozen_model_prices.items()
    }
    stats = {
        "count": len(rows),
        "real_count": len(rows),
        "samples": len(prices),
        "mean": round(statistics.fmean(prices), 0) if prices else 0,
        "median": round(statistics.median(prices), 0) if prices else 0,
        "min": round(min(prices), 0) if prices else 0,
        "max": round(max(prices), 0) if prices else 0,
        "models_covered": len(frozen_model_prices),
        "platform_dist": dict(platform_dist),
        "series_dist": dict(series_dist),
        "per_model": per_model,
    }
    return MarketSnapshot(
        database_path=db.DB_PATH,
        platform=platform,
        revision=revision,
        rows=rows,
        model_prices=frozen_model_prices,
        model_counts={model: len(values) for model, values in frozen_model_prices.items()},
        stats=stats,
    )


def get_snapshot(platform="闲鱼"):
    """返回当前数据版本的不可变快照；版本未变化时不再查询和聚合全表。"""
    global _snapshot
    revision = db.catalog_revision()
    current = _snapshot
    if (current is not None and current.database_path == db.DB_PATH
            and current.platform == platform and current.revision == revision):
        return current
    with _lock:
        current = _snapshot
        if (current is None or current.database_path != db.DB_PATH
                or current.platform != platform or current.revision != revision):
            _snapshot = _build_snapshot(platform, revision)
        return _snapshot


def invalidate_snapshot():
    global _snapshot
    with _lock:
        _snapshot = None
