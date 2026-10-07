"""Decision log with outcome tracking (TradingAgents' 'persistent decision log'): every finished run is stored;
on the next run for the same stock, old decisions older than the holding period are scored (return and alpha vs
Nifty/Sensex) and a short LLM reflection is stored. Lessons are injected into the Portfolio Manager prompt."""
import json
import pathlib
import threading

import pandas as pd

from . import agents, market as mk
from .dataflows import _bench

_lock = threading.Lock()


class DecisionLog:
    def __init__(self, data_dir):
        self.path = pathlib.Path(data_dir) / "decisions.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def all(self):
        if not self.path.exists():
            return []
        with _lock:
            return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def _save(self, recs):
        with _lock:
            self.path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")

    def add(self, rec):
        recs = [r for r in self.all() if r["id"] != rec["id"]]
        self._save(recs + [rec])

    def resolve(self, ysym, llm, cfg, today=None):
        """Score pending decisions for this stock whose holding window has passed. Best effort, never raises."""
        recs, changed, n = self.all(), False, int(cfg.get("holding_period_days", 5))
        for r in recs:
            if r["ysym"] != ysym or r.get("status") != "pending":
                continue
            try:
                df = mk.history(ysym, period="2y")["Close"]
                bsym, _ = _bench(ysym)
                bm = mk.history(bsym, period="2y")["Close"]
                d0 = pd.Timestamp(r["trade_date"])
                i0 = df.index.searchsorted(d0, side="right") - 1
                j0 = bm.index.searchsorted(d0, side="right") - 1
                if i0 < 0 or j0 < 0 or i0 + n >= len(df) or j0 + n >= len(bm):
                    continue  # window not complete yet
                ref = r.get("ref_price") or float(df.iloc[i0])
                raw = float(df.iloc[i0 + n]) / ref - 1
                alpha = raw - (float(bm.iloc[j0 + n]) / float(bm.iloc[j0]) - 1)
                outcome = f"{raw * 100:+.1f}% over {n} trading days, {alpha * 100:+.1f} pts vs benchmark"
                r.update(status="resolved", raw_return=raw, alpha=alpha, outcome=outcome)
                try:
                    r["reflection"] = agents.reflect(llm, r.get("decision", "")[:1500], outcome, cfg)
                except Exception:  # noqa: BLE001
                    r["reflection"] = ""
                changed = True
            except Exception:  # noqa: BLE001
                continue
        if changed:
            self._save(recs)

    def lessons(self, ysym, n_same=3, n_other=3):
        done = [r for r in self.all() if r.get("status") == "resolved"]
        same = [r for r in done if r["ysym"] == ysym][-n_same:]
        other = [r for r in done if r["ysym"] != ysym and r.get("reflection")][-n_other:]
        lines = [f"- {r['trade_date']} {r['ysym']} {r['rating']}: {r['outcome']}. {r.get('reflection', '')}" for r in same + other]
        return "\n".join(lines)
