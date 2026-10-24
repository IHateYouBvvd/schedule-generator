import os
import sys
import random
import time
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

# ====================================================================== #
#  Hardcoded course profiles                                              #
# ====================================================================== #
COURSE_PROFILES = {
    "Math":               {"default_mins": 220, "category": "math",       "load": 5},
    "English":            {"default_mins": 220, "category": "language",   "load": 4},
    "Physics":            {"default_mins": 160, "category": "science",    "load": 5},
    "Chemistry":          {"default_mins": 160, "category": "science",    "load": 4},
    "Chinese":            {"default_mins": 120, "category": "language",   "load": 4},
    "History":            {"default_mins": 160, "category": "humanities", "load": 3},
    "CS/Economy":         {"default_mins": 160, "category": "technical",  "load": 3},
    "PE":                 {"default_mins": 120, "category": "physical",   "load": 1},
    "CAS":                {"default_mins": 80,  "category": "other",      "load": 2},
    "Academic Optional":  {"default_mins": 80,  "category": "other",      "load": 2},
    "Foreign Language":   {"default_mins": 40,  "category": "language",   "load": 3},
    "Optional":           {"default_mins": 40,  "category": "other",      "load": 1},
}

LOG_NAME = "schedule_run_log.txt"

def log_path() -> str:
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        base = os.getcwd()
    return os.path.join(base, LOG_NAME)

# ====================================================================== #
#  Run Logger                                                             #
# ====================================================================== #
class RunLogger:
    """Appends one entry per program execution to a persistent text file."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or log_path()
        self.run_id = self._count_runs() + 1
        self.t0 = time.perf_counter()

    def _count_runs(self) -> int:
        if not os.path.exists(self.path):
            return 0
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if line.startswith("RUN #"))

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def record(self, status: str, mode: str, note: str = "",
               cfg: Optional[Dict[str, int]] = None,
               totals: Optional[Tuple[int, int]] = None,
               plan: Optional[Tuple[int, int]] = None,
               level: Optional[int] = None,
               attempts: Optional[int] = None,
               audit: Optional[Tuple[int, int, int]] = None,
               day_lines: Optional[List[str]] = None):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out = ["=" * 64,
               f"RUN #{self.run_id}  |  {ts}  |  mode={mode}  |  elapsed {self.elapsed():.2f}s",
               "=" * 64]
        if cfg:
            out.append("Input hours (min/week):")
            out.append("  " + ", ".join(f"{k}={v}" for k, v in cfg.items()))
        if totals:
            out.append(f"Totals: required={totals[0]}min available={totals[1]}min "
                       f"free={totals[1]-totals[0]}min")
        if plan:
            out.append(f"Block plan: {plan[0]}x60min, {plan[1]}x40min")
        out.append(f"Result: {status}")
        if note:
            out.append(f"Note: {note}")
        if level is not None:
            out.append(f"Constraint level: {['PERFECT','GOOD','FUNCTIONAL'][level]}")
        if attempts is not None:
            out.append(f"Solver attempts: {attempts}")
        if audit:
            out.append(f"Audit: same-subject adjacent={audit[0]}, "
                       f"similar-subject adjacent={audit[1]}, "
                       f"high-load after lunch={audit[2]}")
        if day_lines:
            out.append("Schedule:")
            out.extend("  " + d for d in day_lines)
        out.append("")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")

# ====================================================================== #
#  Data classes                                                           #
# ====================================================================== #
@dataclass
class ClassBlock:
    subject: str
    duration: int
    category: str
    load: int

@dataclass
class TimeSlot:
    day: int
    period: int
    duration: int
    subject: Optional[str] = None
    category: Optional[str] = None
    load: int = 0

# ====================================================================== #
#  Global block planner                                                   #
# ====================================================================== #
def plan_blocks(config: Dict[str, int], s60: int, s40: int) -> List[ClassBlock]:
    def options(minutes: int):
        opts = []
        for a in range(minutes // 60 + 1):
            rem = minutes - 60 * a
            if rem >= 0 and rem % 40 == 0:
                opts.append((a, rem // 40))
        return opts

    base_a = base_b = 0
    flexible, fixed_blocks = [], []
    for subj, mins in config.items():
        opts = options(mins)
        if not opts:
            raise ValueError(f"{subj}={mins} min cannot be split into 40/60 blocks")
        lo, hi = opts[0], opts[-1]
        base_a += lo[0]; base_b += lo[1]
        if len(opts) >= 2 and (hi[0] - lo[0]) == 2 and (lo[1] - hi[1]) == 3:
            flexible.append((subj, lo, hi))
        else:
            fixed_blocks.append((subj, lo))

    p_min = max(0, -(-(base_b - s40) // 3))
    p_max = min(len(flexible), (s60 - base_a) // 2)
    p = p_max if p_min <= p_max else 0

    promoted = set(s for s, _, _ in random.sample(flexible, p)) if p else set()

    blocks: List[ClassBlock] = []
    def emit(subj, a, b):
        pr = COURSE_PROFILES[subj]
        for _ in range(a): blocks.append(ClassBlock(subj, 60, pr["category"], pr["load"]))
        for _ in range(b): blocks.append(ClassBlock(subj, 40, pr["category"], pr["load"]))

    for subj, lo, hi in flexible:
        a, b = (hi if subj in promoted else lo)
        emit(subj, a, b)
    for subj, (a, b) in fixed_blocks:
        emit(subj, a, b)
    return blocks

# ====================================================================== #
#  Solver                                                                 #
# ====================================================================== #
class ScheduleGenerator:
    def __init__(self, days, periods, long_idx, lunch_idx, avoid_after_lunch):
        self.days, self.periods = days, periods
        self.long_idx, self.lunch_idx = long_idx, lunch_idx
        self.avoid = avoid_after_lunch
        self.blocks: List[ClassBlock] = []
        self.slots: List[TimeSlot] = []
        self.s60, self.s40 = [], []
        for d in range(days):
            for p in range(periods):
                i = d * periods + p
                dur = 60 if p in long_idx else 40
                self.slots.append(TimeSlot(d, p, dur))
                (self.s60 if dur == 60 else self.s40).append(i)

    @staticmethod
    def _cat_clash(c1, c2):
        return c1 == c2 and c1 not in ("other", "physical")

    def _ok(self, i, blk, assign, cats, level):
        s = self.slots[i]
        if s.period > 0:
            j = i - 1
            if assign[j] == blk.subject: return False
            if level == 0 and cats[j] and self._cat_clash(cats[j], blk.category): return False
        if s.period < self.periods - 1:
            j = i + 1
            if assign[j] == blk.subject: return False
            if level == 0 and cats[j] and self._cat_clash(cats[j], blk.category): return False
        if level <= 1 and s.period == self.lunch_idx and blk.category in self.avoid:
            return False
        return True

    def solve(self, attempts=20000):
        b60 = [b for b in self.blocks if b.duration == 60]
        b40 = [b for b in self.blocks if b.duration == 40]
        used = 0
        for level in range(3):
            for _ in range(attempts):
                used += 1
                assign = [None] * len(self.slots)
                cats = [None] * len(self.slots)
                for sl in self.slots:
                    sl.subject = sl.category = None; sl.load = 0
                ok = True
                for blk, pool in ((b60, self.s60), (b40, self.s40)):
                    random.shuffle(blk)
                    for b in blk:
                        valid = [i for i in pool
                                 if assign[i] is None and self._ok(i, b, assign, cats, level)]
                        if not valid:
                            ok = False; break
                        i = random.choice(valid)
                        assign[i] = b.subject; cats[i] = b.category
                        self.slots[i].subject = b.subject
                        self.slots[i].category = b.category
                        self.slots[i].load = b.load
                    if not ok: break
                if ok:
                    return True, level, used
        return False, 2, used

# ====================================================================== #
#  Audit + timeline helpers                                               #
# ====================================================================== #
def audit_schedule(gen, days, periods, lunch_idx):
    same = cat = lunch = 0
    for d in range(days):
        base = d * periods
        for p in range(periods - 1):
            a, b = gen.slots[base + p], gen.slots[base + p + 1]
            if a.subject and b.subject:
                if a.subject == b.subject:
                    same += 1
                elif gen._cat_clash(a.category, b.category):
                    cat += 1
        s = gen.slots[base + lunch_idx]
        if s.subject and s.category in gen.avoid:
            lunch += 1
    return same, cat, lunch

def day_lines(gen, days, periods, lunch_idx):
    lines = []
    for d in range(days):
        seg = gen.slots[d*periods:(d+1)*periods]
        before = "-".join((s.subject or "Free")[:4] for s in seg[:lunch_idx])
        after = "-".join((s.subject or "Free")[:4] for s in seg[lunch_idx:])
        lines.append(f"D{d+1}: {before} | LUNCH | {after}")
    return lines

def daily_timeline(day, lunch_idx, lunch_dur, short_total):
    target = datetime.strptime("12:05", "%H:%M")
    n_breaks = len(day) - 1
    morning_cls = sum(s.duration for s in day[:lunch_idx])
    start = target - timedelta(minutes=morning_cls + short_total * lunch_idx / n_breaks)
    loads = [(0 if i == lunch_idx - 1 else day[i].load + day[i+1].load) for i in range(n_breaks)]
    shorts = [i for i in range(n_breaks) if i != lunch_idx - 1]
    tot = sum(loads[i] for i in shorts) or 1
    raw = [round(loads[i] / tot * short_total) for i in shorts]
    raw[-1] += short_total - sum(raw)
    brk = {}
    for k, i in enumerate(shorts):
        brk[i] = max(5, raw[k])
    fix = short_total - sum(brk.values())
    if fix:
        big = max(shorts, key=lambda x: brk[x]); brk[big] += fix
    out, t = [], start
    for i, s in enumerate(day):
        a = t.strftime("%H:%M"); t += timedelta(minutes=s.duration); b = t.strftime("%H:%M")
        out.append((a, b, f"P{i+1}: {s.subject or 'Free / Study Hall'}", s.duration))
        if i < n_breaks:
            ba = t.strftime("%H:%M")
            d = lunch_dur if i == lunch_idx - 1 else brk[i]
            t += timedelta(minutes=d); bb = t.strftime("%H:%M")
            out.append((ba, bb, "LUNCH" if i == lunch_idx - 1 else f"Break ({d}m)", d))
    return out

# ====================================================================== #
#  Main                                                                   #
# ====================================================================== #
def main():
    # View log mode:  python schedule_generator.py --log
    if len(sys.argv) > 1 and sys.argv[1] in ("--log", "-l"):
        p = log_path()
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                print(f.read())
        else:
            print("No log file yet. Run the generator first.")
        return

    logger = RunLogger()

    print("\n" + "="*65)
    print("   AUTOMATED SCHOOL SCHEDULE GENERATOR (v9.0)")
    print("  Run logging | Correct block planning | 12:05 lunch")
    print("="*65)

    if input("\nUse default course hours? (y/n) [y]: ").strip().lower().startswith('n'):
        mode = "custom"
        cfg = {}
        print("\nEnter weekly MINUTES per course (multiple of 20; Enter = default):\n")
        for subj, pr in COURSE_PROFILES.items():
            while True:
                v = input(f"{subj:<20} [{pr['default_mins']}]: ").strip()
                if not v:
                    cfg[subj] = pr['default_mins']; break
                try:
                    m = int(v)
                    if m % 20:
                        print("  ! must be a multiple of 20"); continue
                    cfg[subj] = m; break
                except ValueError:
                    print("  ! enter a number")
    else:
        mode = "default"
        cfg = {s: p['default_mins'] for s, p in COURSE_PROFILES.items()}

    days, periods, long_idx, lunch_idx = 5, 7, [0, 4], 3
    s60 = days * len(long_idx)
    s40 = days * (periods - len(long_idx))
    avoid = sorted({p['category'] for p in COURSE_PROFILES.values() if p['load'] >= 4})

    req = sum(cfg.values())
    avail = s60 * 60 + s40 * 40

    if req > avail:
        print(f"\nX Required {req} min > available {avail} min. Reduce hours.")
        logger.record("FAILED", mode, f"capacity exceeded: {req} > {avail}",
                      cfg=cfg, totals=(req, avail))
        print(f"[log] Run #{logger.run_id} saved to {logger.path}")
        return

    gen = ScheduleGenerator(days, periods, long_idx, lunch_idx, avoid)
    try:
        gen.blocks = plan_blocks(cfg, s60, s40)
    except ValueError as e:
        print(f"\nX Invalid course time: {e}")
        logger.record("FAILED", mode, str(e), cfg=cfg, totals=(req, avail))
        print(f"[log] Run #{logger.run_id} saved to {logger.path}")
        return

    n60 = sum(1 for b in gen.blocks if b.duration == 60)
    n40 = sum(1 for b in gen.blocks if b.duration == 40)
    print(f"\nBlock plan: {n60}x60min (slots {s60}), {n40}x40min (slots {s40}), "
          f"free={avail-req}min")

    ok, lvl, used = gen.solve()

    if not ok:
        print("\nX No layout found (hours incompatible with the 40/60 grid).")
        logger.record("FAILED", mode, "no layout found",
                      cfg=cfg, totals=(req, avail), plan=(n60, n40), attempts=used)
        print(f"[log] Run #{logger.run_id} saved to {logger.path}")
        return

    tag = ["PERFECT (no similar subjects adjacent, light post-lunch)",
           "GOOD (some similar subjects adjacent)",
           "FUNCTIONAL (only identical-subject repeats avoided)"][lvl]
    print(f"\nOK  Schedule generated -> {tag}\n")

    for d in range(days):
        print("="*65); print(f"  DAY {d+1}"); print("="*65)
        print(f"  {'TIME':<12}| {'EVENT':<34}| DUR")
        for a, b, ev, du in daily_timeline(gen.slots[d*periods:(d+1)*periods], lunch_idx, 70, 50):
            pre = "~ " if ("Break" in ev or "LUNCH" in ev) else "> "
            print(f"  {a}-{b} | {pre}{ev:<32}| {du}m")

    tot: Dict[str, int] = {}
    for s in gen.slots:
        if s.subject:
            tot[s.subject] = tot.get(s.subject, 0) + s.duration
    print("\n" + "="*65); print("  WEEKLY TOTALS"); print("="*65)
    for subj in COURSE_PROFILES:
        m = tot.get(subj, 0); h, mm = divmod(m, 60)
        print(f"  {subj:<22}{m:>4} min ({h}h{mm:02d})")
    print(f"\n  Study={req}min  ShortBreaks={50*days}min  Lunch={70*days}min")

    # ---- write the run log entry ----
    audit = audit_schedule(gen, days, periods, lunch_idx)
    logger.record("SUCCESS", mode, cfg=cfg, totals=(req, avail),
                  plan=(n60, n40), level=lvl, attempts=used, audit=audit,
                  day_lines=day_lines(gen, days, periods, lunch_idx))
    print(f"\n[log] Run #{logger.run_id} saved to {logger.path}")
    print("[log] View all runs with:  python schedule_generator.py --log")

if __name__ == "__main__":
    main()
