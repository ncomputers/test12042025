class TrendAggregator:
    def __init__(self):
        self.factors = {}

    def update(self, factor_name: str, data: dict):
        self.factors[factor_name] = data
        return self._evaluate()

    def _evaluate(self) -> str:
        signals = [v["trend"] for v in self.factors.values() if v is not None and v.get("trend") in ("up", "down")]
        if not signals:
            return "NEUTRAL (0%)"
        up_count = signals.count("up")
        down_count = signals.count("down")
        total = up_count + down_count
        strength = int((abs(up_count - down_count) / total) * 100) if total > 0 else 0
        if up_count > down_count:
            return f"STRONG_UP ({strength}%)"
        elif down_count > up_count:
            return f"STRONG_DOWN ({strength}%)"
        else:
            return f"NEUTRAL ({strength}%)"
