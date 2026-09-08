"""
rubric.py — scoring, medals, and career aggregation.

Pure logic. No file I/O beyond reading config.yaml, no workbook access, no network. Everything
here is driven by the `rubric:` block in config.yaml so a category, a maximum or a medal band can
change without touching code.

Two things here are easy to get wrong and are covered by tests:

  * Medal rounding is HALF-UP, not Python's default. round(88.5) is 88 in Python (banker's
    rounding), which would quietly cost you a Gold. decimal.ROUND_HALF_UP is used instead.
  * The career score is the mean of the counted sittings' totals. The sum of the *unrounded*
    per-category means equals that exactly. The sum of the *displayed* (1-decimal) category means
    does NOT — ten independent roundings can drift by up to 0.5, and with the four example
    sittings they really do: displayed categories sum to 86.0 while the true mean of totals is
    85.8. So the headline is always the mean of totals, the category figures are display values,
    and the UI must never print a total under the category column inviting the reader to add it up.
    The invariant is asserted on the exact values.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import yaml


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    max: int
    question: str = ""      # the ? tooltip on each row, from config (SPEC.md §3)


@dataclass(frozen=True)
class Band:
    name: str
    min: int


def half_up(value, places=0):
    """Round half away from zero. round() uses banker's rounding and would drop 88.5 to 88."""
    q = Decimal(1) if places == 0 else Decimal(1).scaleb(-places)
    d = Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP)
    return int(d) if places == 0 else float(d)


class Rubric:
    def __init__(self, spec: dict):
        self.name = spec.get("name", "rubric")
        self.version = int(spec.get("version", 1))
        self.decimals = int(spec.get("decimals", 1))
        self.categories = [Category(c["key"], c["label"], int(c["max"]), c.get("question", ""))
                           for c in spec["categories"]]
        self.bands = sorted((Band(b["name"], int(b["min"])) for b in spec["medals"]),
                            key=lambda b: b.min, reverse=True)
        if not self.categories:
            raise ValueError("rubric has no categories")
        if self.bands[-1].min > 0:
            raise ValueError("rubric medals must cover down to 0")

    # -- shape ---------------------------------------------------------------
    @property
    def keys(self):
        return [c.key for c in self.categories]

    @property
    def max_total(self):
        return sum(c.max for c in self.categories)

    def category(self, key) -> Category:
        for c in self.categories:
            if c.key == key:
                return c
        raise KeyError(key)

    def as_config(self) -> dict:
        """Everything the front end needs to render the sheet — config-driven, so a category,
        a maximum or a band changes here and nowhere else. Bands are returned high-to-low."""
        return {
            "name": self.name,
            "version": self.version,
            "decimals": self.decimals,
            "max_total": self.max_total,
            "categories": [{"key": c.key, "label": c.label, "max": c.max, "question": c.question}
                           for c in self.categories],
            "bands": [{"name": b.name, "min": b.min} for b in self.bands],
        }

    # -- one scorecard -------------------------------------------------------
    def validate(self, scores: dict) -> list[str]:
        """Returns a list of human-readable problems. Empty list means the card is scoreable."""
        problems = []
        for c in self.categories:
            if c.key not in scores or scores[c.key] is None:
                problems.append(f"{c.label} is not scored")
                continue
            v = scores[c.key]
            if isinstance(v, bool) or not isinstance(v, int):
                problems.append(f"{c.label} must be a whole number, got {v!r}")
            elif not 0 <= v <= c.max:
                problems.append(f"{c.label} must be 0–{c.max}, got {v}")
        for extra in set(scores) - set(self.keys):
            problems.append(f"unknown category {extra!r}")
        return problems

    def total(self, scores: dict) -> int:
        problems = self.validate(scores)
        if problems:
            raise ValueError("; ".join(problems))
        return sum(int(scores[c.key]) for c in self.categories)

    def medal(self, score) -> str:
        """Medal for a total or a career mean. The mean is rounded half-up first, so 89.5 is
        Diamond and 89.4 is Gold."""
        n = half_up(score)
        for b in self.bands:
            if n >= b.min:
                return b.name
        return self.bands[-1].name

    def points_to_next_band(self, score):
        """(band_name, points_needed) for the next band up, or None at the top."""
        n = half_up(score)
        higher = [b for b in self.bands if b.min > n]
        if not higher:
            return None
        target = min(higher, key=lambda b: b.min)
        return target.name, target.min - n

    # -- many sittings -------------------------------------------------------
    def career(self, sittings: list[dict]) -> dict:
        """Aggregate counted sittings into the number shown everywhere else.

        `sittings` is a list of {"scores": {...}, "include_in_average": bool, ...}.
        Excluded sittings are reported but never influence the mean.
        """
        counted = [s for s in sittings if s.get("include_in_average", True)]
        result = {
            "n": len(counted),
            "n_total": len(sittings),
            "n_excluded": len(sittings) - len(counted),
            "categories": {},
            "mean_total": None,
            "medal": None,
            "range": None,
            "next_band": None,
        }
        if not counted:
            return result

        for c in self.categories:
            vals = [int(s["scores"][c.key]) for s in counted]
            exact = sum(vals) / len(vals)
            result["categories"][c.key] = {
                "label": c.label,
                "max": c.max,
                "mean": round(exact, self.decimals),   # for display
                "mean_exact": exact,                   # for arithmetic
                "min": min(vals),
                "max_seen": max(vals),
                "varies": min(vals) != max(vals),
                "n": len(vals),
            }

        totals = [self.total(s["scores"]) for s in counted]
        mean_total_exact = sum(totals) / len(totals)
        mean_total = round(mean_total_exact, self.decimals)
        result["mean_total"] = mean_total
        result["mean_total_exact"] = mean_total_exact
        result["medal"] = self.medal(mean_total)
        result["range"] = (min(totals), max(totals))
        result["totals"] = totals
        result["next_band"] = self.points_to_next_band(mean_total)

        # Exact invariant: sum of unrounded category means IS the mean of totals.
        exact_sum = sum(v["mean_exact"] for v in result["categories"].values())
        if abs(exact_sum - mean_total_exact) > 1e-9:
            raise AssertionError(
                f"category means sum to {exact_sum} but mean of totals is {mean_total_exact}")

        # Displayed values can drift by up to 0.05 per category. Surface it rather than hide it,
        # so the UI knows not to render an addable column total.
        display_sum = round(sum(v["mean"] for v in result["categories"].values()), self.decimals)
        result["display_sum"] = display_sum
        result["display_sum_matches"] = abs(display_sum - mean_total) < 10 ** -self.decimals
        return result


def load_rubric(config_path="config.yaml") -> Rubric:
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return Rubric(cfg["rubric"])


if __name__ == "__main__":
    r = load_rubric()
    print(f"{r.name} v{r.version} — {len(r.categories)} categories, max {r.max_total}")
    for c in r.categories:
        print(f"   {c.label:<14} /{c.max}")
    print("   bands:", ", ".join(f"{b.name} {b.min}+" for b in r.bands))
