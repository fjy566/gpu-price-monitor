# -*- coding: utf-8 -*-
"""采集配置仓储：集中校验、批量读写并提供线程安全的进程内缓存。"""
import math
import threading

import database as db


DEFAULT_SETTINGS = {
    "abs_min": "500",
    "low_ratio": "0.55",
    "high_ratio": "3.0",
    "keep_min": "1",
    "exclude_mobile": "1",
    "browser_mode": "minimized",
    "crawl_mode": "browser",
    "selected_platforms": "goofish",
    "selected_models": "",
    "watched_models": "",
}

_cache_lock = threading.Lock()
_cache_key = None
_cache_value = None
_cache_generation = 0


def _database_identity():
    return db.DB_PATH


def invalidate_settings_cache():
    global _cache_key, _cache_value, _cache_generation
    with _cache_lock:
        _cache_key = None
        _cache_value = None
        _cache_generation += 1


def get_settings():
    """读取当前配置；稳定配置直接命中内存，写入后显式失效。"""
    global _cache_key, _cache_value
    while True:
        identity = _database_identity()
        with _cache_lock:
            if _cache_key == identity and _cache_value is not None:
                return dict(_cache_value)
            generation = _cache_generation
        settings = db.get_states(DEFAULT_SETTINGS, prefix="cfg_")
        settings["crawl_mode"] = "browser"
        settings["selected_platforms"] = "goofish"
        with _cache_lock:
            if generation != _cache_generation:
                continue
            _cache_key = identity
            _cache_value = dict(settings)
            return dict(settings)


def save_settings(data):
    """校验并原子化保存设置；无效输入不会留下部分更新。"""
    normalized = {}
    for key, raw_value in (data or {}).items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key == "crawl_mode":
            value = str(raw_value).strip()
            if value != "browser":
                raise ValueError("当前仅支持模拟浏览器采集")
        elif key == "browser_mode":
            value = str(raw_value).strip()
            if value not in {"silent", "visible", "minimized"}:
                raise ValueError("浏览器模式无效")
        elif key in {"abs_min", "low_ratio", "high_ratio"}:
            try:
                number = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 必须是数字") from exc
            if not math.isfinite(number):
                raise ValueError(f"{key} 必须是有限数字")
            if key == "abs_min" and not (0 <= number <= 1_000_000):
                raise ValueError("绝对下限必须在 0 到 1000000 之间")
            if key == "low_ratio" and not (0 < number <= 1):
                raise ValueError("低价比例必须大于 0 且不超过 1")
            if key == "high_ratio" and not (1 <= number <= 20):
                raise ValueError("高价倍数必须在 1 到 20 之间")
            value = format(number, "g")
        elif key in {"keep_min", "exclude_mobile"}:
            value = "1" if raw_value in (True, 1, "1", "true", "True", "on") else "0"
        elif key == "selected_platforms":
            parts = [part.strip() for part in str(raw_value or "").split(",") if part.strip()]
            if any(part != "goofish" for part in parts):
                raise ValueError("当前仅支持闲鱼平台")
            value = "goofish"
        else:
            parts = [part.strip() for part in str(raw_value or "").split(",") if part.strip()]
            if any(len(part) > 40 or any(ch in part for ch in "\r\n\x00") for part in parts):
                raise ValueError("型号设置包含无效值")
            value = ",".join(dict.fromkeys(parts))
        normalized[key] = value
    db.set_states(normalized, prefix="cfg_")
    invalidate_settings_cache()
    return get_settings()
