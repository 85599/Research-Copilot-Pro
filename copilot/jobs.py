"""Background runs. State is persisted to runs/<id>/state.json so history survives a restart."""
import datetime as dt
import json
import os
import pathlib
import re
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from .config import build_config, DEPTHS
from .graph import Cancelled, run_pipeline
from .llm import LLM, Usage
from .memory import DecisionLog

AGENT_ORDER = ["market", "sentiment", "news", "fundamentals", "bull", "bear", "research_manager", "trader",
               "aggressive", "conservative", "neutral", "portfolio_manager"]


class Job:
    def __init__(self, run_id, req, cfg, base):
        self.id, self.req, self.cfg = run_id, req, cfg
        self.dir = pathlib.Path(base) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.status, self.error, self.result, self.cancelled = "queued", None, None, False
        self.agents = {a: "pending" for a in AGENT_ORDER if a in req["analysts"] or a not in ("market", "sentiment", "news", "fundamentals")}
        self.messages, self.provenance, self.usage = [], [], Usage()
        self.ysym, self.name, self.report_path = None, None, None
        self.started, self.finished, self.current = dt.datetime.now().isoformat(timespec="seconds"), None, "Queued"
        self.l, self.sl = threading.Lock(), threading.Lock()

    # called from the pipeline ---------------------------------------
    def phase(self, text):
        self.current = text
        self.save()

    def agent(self, a, state):
        with self.l:
            self.agents[a] = state
        self.save()

    def say(self, agent, label, text, kind="report"):
        with self.l:
            self.messages.append({"i": len(self.messages), "agent": agent, "label": label, "text": text, "kind": kind,
                                  "at": dt.datetime.now().strftime("%H:%M:%S")})
        self.save()

    # state ------------------------------------------------------------
    def snapshot(self, since=0):
        with self.l:
            return {"id": self.id, "status": self.status, "phase": self.current, "error": self.error, "symbol": self.req["symbol"],
                    "exchange": self.req["exchange"], "ysym": self.ysym, "name": self.name, "trade_date": self.req["trade_date"],
                    "params": {k: self.req.get(k) for k in ("analysts", "depth")} | {k: self.cfg.get(k) for k in (
                        "llm_provider", "deep_think_llm", "quick_think_llm", "output_language", "max_debate_rounds", "max_risk_discuss_rounds")},
                    "agents": dict(self.agents), "messages": self.messages[since:], "n_messages": len(self.messages),
                    "result": self.result, "usage": self.usage.as_dict(), "provenance": self.provenance,
                    "started": self.started, "finished": self.finished, "has_report": bool(self.report_path)}

    def save(self):
        """Atomic write under a lock: parallel analysts save at once and Windows refuses concurrent writers."""
        data = json.dumps(self.snapshot(), ensure_ascii=False)
        with self.sl:
            try:
                tmp = self.dir / "state.json.tmp"
                tmp.write_text(data, encoding="utf-8")
                os.replace(tmp, self.dir / "state.json")
            except OSError:
                pass


class JobManager:
    def __init__(self, base, data_dir, make_llm=LLM):
        self.base, self.make_llm = pathlib.Path(base), make_llm
        self.log = DecisionLog(data_dir)
        self.jobs, self.pool = {}, ThreadPoolExecutor(2)
        self.base.mkdir(parents=True, exist_ok=True)

    def submit(self, body):
        sym = (body.get("symbol") or "").strip().upper().replace(".NS", "").replace(".BO", "")
        if not re.fullmatch(r"[A-Z0-9&\-\^=]{1,25}", sym):
            raise ValueError("Symbol looks invalid (letters, digits, & and - only)")
        ex = body.get("exchange", "NSE")
        depth = body.get("depth", "shallow")
        rounds = DEPTHS.get(depth) or int(body.get("rounds") or 1)
        cfg = build_config({"llm_provider": body.get("provider"), "deep_think_llm": body.get("deep_model"),
                            "quick_think_llm": body.get("quick_model"), "output_language": body.get("language"),
                            "max_debate_rounds": rounds, "max_risk_discuss_rounds": rounds, "backend_url": body.get("backend_url") or None})
        req = {"symbol": sym, "exchange": ex, "analysts": body.get("analysts") or ["market", "sentiment", "news", "fundamentals"],
               "depth": depth, "trade_date": body.get("trade_date") or dt.date.today().isoformat()}
        run_id = f"{sym}_{req['trade_date']}_{dt.datetime.now().strftime('%H%M%S')}"
        job = Job(run_id, req, cfg, self.base)
        self.jobs[run_id] = job
        job.save()
        self.pool.submit(self._run, job)
        return job

    def _run(self, job):
        job.status = "running"
        try:
            job.result = run_pipeline(job, job.req, job.cfg, self.log, self.make_llm)
            job.status, job.current = "done", "Complete"
        except Cancelled:
            job.status, job.current = "cancelled", "Cancelled"
        except Exception as e:  # noqa: BLE001
            job.status, job.error, job.current = "error", f"{type(e).__name__}: {e}", "Failed"
            (job.dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        job.finished = dt.datetime.now().isoformat(timespec="seconds")
        job.save()

    @staticmethod
    def valid_id(run_id):
        return bool(re.fullmatch(r"[A-Za-z0-9&_\-\^=]+", run_id or ""))

    def get(self, run_id, since=0):
        if not self.valid_id(run_id):
            return None
        if run_id in self.jobs:
            return self.jobs[run_id].snapshot(since)
        p = self.base / run_id / "state.json"
        if p.exists():
            s = json.loads(p.read_text(encoding="utf-8"))
            s["messages"] = s["messages"][since:]
            if s["status"] in ("running", "queued"):  # server restarted mid-run
                s["status"], s["error"] = "error", "Server restarted while this run was in progress"
            return s
        return None

    def history(self, limit=30):
        rows = []
        for p in sorted(self.base.glob("*/state.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
            try:
                s = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            live = self.jobs.get(s["id"])
            rows.append({"id": s["id"], "symbol": s["symbol"], "exchange": s["exchange"], "trade_date": s["trade_date"],
                         "status": live.status if live else ("error" if s["status"] in ("running", "queued") else s["status"]),
                         "rating": (s.get("result") or {}).get("rating"), "started": s["started"]})
        return rows

    def cancel(self, run_id):
        j = self.jobs.get(run_id)
        if j and j.status in ("queued", "running"):
            j.cancelled = True
            return True
        return False

    def report_file(self, run_id):
        if not self.valid_id(run_id):
            return None
        p = self.base / run_id / "complete_report.md"
        return p if p.exists() else None
