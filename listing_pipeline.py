# -*- coding: utf-8 -*-
"""纯计算商品过滤流水线，与浏览器、数据库和 Flask 解耦。"""
from dataclasses import dataclass
import statistics

from gpus import is_desktop_gpu, listing_rejection_reason, title_matches_model


@dataclass(frozen=True, slots=True)
class FilterPolicy:
    absolute_minimum: float
    low_ratio: float
    high_ratio: float
    exclude_mobile: bool = True

    @classmethod
    def from_settings(cls, settings):
        return cls(
            absolute_minimum=float(settings["abs_min"]),
            low_ratio=float(settings["low_ratio"]),
            high_ratio=float(settings["high_ratio"]),
            exclude_mobile=settings.get("exclude_mobile", "1") == "1",
        )


@dataclass(frozen=True, slots=True)
class FilterStats:
    matched: int
    valid: int
    kept: int
    median: float
    content: int
    mobile: int
    price: int

    def as_dict(self):
        return {
            "matched": self.matched,
            "valid": self.valid,
            "kept": self.kept,
            "median": self.median,
            "content": self.content,
            "mobile": self.mobile,
            "price": self.price,
        }


class ListingFilter:
    """可复用、无共享可变状态的过滤器。"""

    __slots__ = ("policy",)

    def __init__(self, policy):
        self.policy = policy

    def filter(self, model_name, items):
        matched = []
        rejected_content = 0
        rejected_mobile = 0
        exclude_mobile = self.policy.exclude_mobile

        for title, price, url in items:
            if not price or not title_matches_model(title, model_name):
                continue
            if listing_rejection_reason(title):
                rejected_content += 1
                continue
            if exclude_mobile and not is_desktop_gpu(title):
                rejected_mobile += 1
                continue
            matched.append((title, float(price), url))

        absolute_minimum = self.policy.absolute_minimum
        valid_prices = [price for _, price, _ in matched if price >= absolute_minimum]
        market_median = statistics.median(valid_prices) if valid_prices else 0.0
        low_threshold = max(absolute_minimum, market_median * self.policy.low_ratio)
        high_threshold = (
            max(market_median, market_median * self.policy.high_ratio)
            if valid_prices else float("inf")
        )
        kept = [item for item in matched if low_threshold <= item[1] <= high_threshold]
        stats = FilterStats(
            matched=len(matched), valid=len(valid_prices), kept=len(kept), median=market_median,
            content=rejected_content, mobile=rejected_mobile,
            price=max(0, len(matched) - len(kept)),
        )
        return kept, stats
