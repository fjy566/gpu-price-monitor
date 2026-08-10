# -*- coding: utf-8 -*-
"""matplotlib 图表生成：价格走势 / 各系价格分布。"""
import os
import hashlib
import re
import threading
from functools import wraps

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import statistics

import database as db
from gpus import classify

CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "charts")
os.makedirs(CHART_DIR, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

COLORS = {
    "bg": "#10151d",
    "panel": "#151c26",
    "text": "#e8edf5",
    "muted": "#8e9aaa",
    "grid": "#2a3442",
    "green": "#2ee6a8",
    "blue": "#4aa3ff",
    "purple": "#a78bfa",
    "orange": "#f5a623",
}
_chart_lock = threading.Lock()


def _serialized(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with _chart_lock:
            return func(*args, **kwargs)
    return wrapper


def _trend_filename(model):
    """生成不可穿越目录、且不同型号不易冲突的趋势图文件名。"""
    raw = str(model or "model")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")[:48] or "model"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"trend_{slug}_{digest}.png"


def _figure(figsize):
    fig, ax = plt.subplots(figsize=figsize, facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["panel"])
    ax.tick_params(colors=COLORS["muted"])
    for spine in ax.spines.values():
        spine.set_color(COLORS["grid"])
    ax.xaxis.label.set_color(COLORS["muted"])
    ax.yaxis.label.set_color(COLORS["muted"])
    ax.title.set_color(COLORS["text"])
    return fig, ax


def _save(fig, name):
    path = os.path.join(CHART_DIR, name)
    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return f"charts/{name}"


@_serialized
def price_trend(model, platform=None):
    """单日用箱型+散点展示分布，多日用每日中位数和四分位区间展示趋势。"""
    # 当前工作台只展示闲鱼，避免历史库中的旧平台污染图表。
    hist = db.history(model, platform or "闲鱼")
    if not hist:
        return None
    fig, ax = _figure((9, 4.5))
    # 对旧库做防御性去重：同一自然日、同一链接（无链接时按标题）只取最后一次观察。
    daily_items = {}
    for row in hist:
        moment = datetime.fromisoformat(row["ts"])
        item_key = row.get("url") or row.get("title") or str(row.get("id"))
        daily_items[(moment.date(), item_key)] = (moment, float(row["price"]))
    by_day = {}
    for (day, _), (_, price) in daily_items.items():
        by_day.setdefault(day, []).append(price)

    if len(by_day) == 1:
        day, prices = next(iter(sorted(by_day.items())))
        box = ax.boxplot(
            prices, vert=False, widths=0.34, patch_artist=True, showmeans=True,
            boxprops={"facecolor": COLORS["blue"], "alpha": 0.24, "edgecolor": COLORS["blue"]},
            medianprops={"color": COLORS["green"], "linewidth": 2.4},
            meanprops={"marker": "D", "markerfacecolor": COLORS["purple"], "markeredgecolor": COLORS["purple"]},
            whiskerprops={"color": COLORS["muted"]}, capprops={"color": COLORS["muted"]},
            flierprops={"markeredgecolor": COLORS["orange"], "alpha": 0.7},
        )
        del box
        count = len(prices)
        jitter = [1 + ((index % 9) - 4) * 0.018 for index in range(count)]
        ax.scatter(prices, jitter, s=22, color=COLORS["green"], alpha=0.62,
                   edgecolors=COLORS["panel"], linewidths=0.35, zorder=3)
        ax.set_yticks([1])
        ax.set_yticklabels([day.strftime("%m-%d")])
        ax.set_title(f"{model} 单日价格分布 · {count} 个去重商品")
        ax.set_xlabel("价格 (元)  ·  绿点=商品 / 绿线=中位数 / 紫钻=平均价")
        ax.grid(True, axis="x", color=COLORS["grid"], alpha=0.7)
    else:
        days = sorted(by_day)
        medians = [statistics.median(by_day[day]) for day in days]
        q1 = [_percentile(by_day[day], 0.25) for day in days]
        q3 = [_percentile(by_day[day], 0.75) for day in days]
        ax.fill_between(days, q1, q3, color=COLORS["blue"], alpha=0.2, label="25%–75% 区间")
        ax.plot(days, medians, marker="o", color=COLORS["green"], linewidth=2.4,
                label="每日中位价")
        ax.set_title(f"{model} 每日价格趋势 · 已按商品/自然日去重")
        ax.set_ylabel("价格 (元)")
        ax.grid(True, color=COLORS["grid"], alpha=0.7)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        legend = ax.legend(facecolor=COLORS["panel"], edgecolor=COLORS["grid"])
        for text in legend.get_texts():
            text.set_color(COLORS["text"])
    return _save(fig, _trend_filename(model))


def _percentile(values, fraction):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


@_serialized
def series_summary():
    """用点区间图展示每个型号的价格分布、最低价、中位价和平均价。"""
    rows = db.distinct_items(platform="闲鱼")
    real = [r for r in rows if "演示" not in (r.get("title") or "")]
    pool = real if real else rows
    if not pool:
        return None
    # 按型号聚合平均价/最低价
    model_map = {}
    for r in pool:
        model_map.setdefault(r["model"], {"prices": [], "gen": r["generation"]})
        model_map[r["model"]]["prices"].append(r["price"])
    # 排序：按系列代次(50>40>30)再按型号
    gen_order = {50: 0, 40: 1, 30: 2}
    names = sorted(model_map.keys(), key=lambda m: (gen_order.get(model_map[m]["gen"], 9), m))
    avg = [round(statistics.mean(model_map[n]["prices"])) for n in names]
    low = [min(model_map[n]["prices"]) for n in names]
    median = [round(statistics.median(model_map[n]["prices"])) for n in names]
    high = [max(model_map[n]["prices"]) for n in names]

    fig, ax = _figure((11, max(4.5, len(names) * 0.42)))
    y = range(len(names))
    for index, name in enumerate(names):
        prices = model_map[name]["prices"]
        ax.hlines(index, low[index], high[index], color=COLORS["grid"], linewidth=4, zorder=1)
        point_y = [index + ((offset % 7) - 3) * 0.025 for offset in range(len(prices))]
        ax.scatter(prices, point_y, s=14, color=COLORS["blue"], alpha=0.24, zorder=2)
    ax.scatter(avg, list(y), s=72, color=COLORS["purple"], label="平均价", zorder=4)
    ax.scatter(median, list(y), s=70, marker="|", linewidths=3,
               color=COLORS["green"], label="中位价", zorder=5)
    ax.scatter(low, list(y), s=48, marker="D", color=COLORS["orange"], label="最低价", zorder=4)
    for index, (minimum, mean) in enumerate(zip(low, avg)):
        ax.annotate(f"¥{minimum:,.0f}", (minimum, index), xytext=(-7, -12),
                    textcoords="offset points", ha="right", color=COLORS["orange"], fontsize=8)
        ax.annotate(f"均 ¥{mean:,.0f}", (mean, index), xytext=(7, 7),
                    textcoords="offset points", color=COLORS["purple"], fontsize=8)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names)
    ax.set_xlabel("价格 (元)")
    ax.set_title(f"各型号价格分布 · {len(pool)} 个去重商品")
    legend = ax.legend(facecolor=COLORS["panel"], edgecolor=COLORS["grid"])
    for text in legend.get_texts():
        text.set_color(COLORS["text"])
    ax.grid(True, axis="x", color=COLORS["grid"], alpha=0.7)
    return _save(fig, "model_summary.png")


@_serialized
def per_model_chart(stats_info=None):
    """各型号平均价条形图。"""
    if not stats_info or not stats_info.get("per_model"):
        return None
    pm = stats_info["per_model"]
    # 按生成系列排序
    names = list(pm.keys())
    fig, ax = _figure((11, max(4, len(names) * 0.4)))
    means = [pm[n]["mean"] for n in names]
    y = range(len(names))
    colors = []
    gen_map = {50: COLORS["purple"], 40: COLORS["blue"], 30: COLORS["orange"],
               90: COLORS["green"], 70: COLORS["green"]}
    for n in names:
        g = classify(n)["generation"]
        colors.append(gen_map.get(g, "#888"))
    ax.barh(y, means, color=colors)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names)
    ax.set_xlabel("平均价 (元)")
    ax.set_title("各型号平均价 (真实数据)")
    ax.grid(True, axis="x", color=COLORS["grid"], alpha=0.7)
    return _save(fig, "per_model.png")
