import random
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

# ====================================================================== #
#  Hardcoded course profiles (system owns names/categories/loads)         #
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
#  Global block planner  (THE FIX)                                        #
# ====================================================================== #
def plan_blocks(config: Dict[str, int], s60: int, s40: int) -> List[ClassBlock]:
    """
    Split each subject's weekly minutes into 60/40 blocks so that the TOTAL
    number of 60-blocks <= s60 and 40-blocks <= s40.  Uses a one-step
    promotion model: every flexible subject can swap its minimum-60 split
    for its maximum-60 split (delta = +2 sixty-blocks, -3 forty-blocks).
    """
    # Per-subject feasible (a=#60, b=#40) options for 60a+40b = minutes
    def options(minutes: int):
        opts = []
        for a in range(minutes // 60 + 1):
            rem = minutes - 60 * a
            if rem >= 0 and rem % 40 == 0:
                opts.append((a, rem // 40))
        return opts  # sorted ascending by a

    base_a = base_b = 0
    flexible = []          # subjects that have a min->max promotion
    fixed_blocks = []      # subjects with only one option
    for subj, mins in config.items():
        opts = options(mins)
        if not opts:
            raise ValueError(f"{subj}: {mins} min cannot be made from 40/60 blocks")
        lo = opts[0]                 # fewest 60-blocks
        hi = opts[-1]                # most 60-blocks
        base_a += lo[0]; base_b += lo[1]
        if len(opts) >= 2 and (hi[0] - lo[0]) == 2 and (lo[1] - hi[1]) == 3:
            flexible.append((subj, lo, hi))
        else:
            fixed_blocks.append((subj, lo))

    # Choose how many flexible subjects to promote (p).
    # sum_a = base_a + 2p ; sum_b = base_b - 3p ; need sum_a<=s60, sum_b<=s40, sum_b>=0
    p_min = max(0, -(-(base_b - s40) // 3))      # ceil((base_b-s40)/3)
    p_max = min(len(flexible), (s60 - base_a) // 2)
    if p_min > p_max:
        # Cannot satisfy inventory -> fall back to base (extra 60-slots stay free)
        p = 0
    else:
        p = p_max                                 # use as many long slots as possible

    promoted = set(subj for subj, _, _ in random.sample(flexible, p)) if p else set()

    blocks: List[ClassBlock] = []
    def emit(subj, a, b, prof):
        for _ in range(a): blocks.append(ClassBlock(subj, 60, prof["category"], prof["load"]))
        for _ in range(b): blocks.append(ClassBlock(subj, 40, prof["category"], prof["load"]))

    for subj, lo, hi in flexible:
        a, b = (hi if subj in promoted else lo)
        emit(subj, a, b, COURSE_PROFILES[subj])
    for subj, (a, b) in fixed_blocks:
        emit(subj, a, b, COURSE_PROFILES[subj])

    return blocks

# ====================================================================== #
#  Solver (multi-level greedy, fast)                                      #
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
        # level 0 = same+category+post-lunch ; 1 = same+post-lunch ; 2 = same only
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
        for level in range(3):
            for _ in range(attempts):
                assign = [None] * len(self.slots)
                cats = [None] * len(self.slots)
                for sl in self.slots: sl.subject = sl.category = None; sl.load = 0
                ok = True
                for blk, pool in ((b60, self.s60), (b40, self.s40)):
                    random.shuffle(blk)
                    for b in blk:
                        valid = [i for i in pool if assign[i] is None and self._ok(i, b, assign, cats, level)]
                        if not valid: ok = False; break
                        i = random.choice(valid)
                        assign[i] = b.subject; cats[i] = b.category
                        self.slots[i].subject, self.slots[i].category, self.slots[i].load = b.subject, b.category, b.load
                    if not ok: break
                if ok: return True, level
        return False, 2

# ====================================================================== #
#  Clock + dynamic breaks                                                 #
# ====================================================================== #
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
    print("\n" + "="*65)
    print("   AUTOMATED SCHOOL SCHEDULE GENERATOR (v8.0)")
    print("  Correct block planning | Dynamic breaks | 12:05 lunch")
    print("="*65)
    if input("\nUse default course hours? (y/n) [y]: ").strip().lower().startswith('n'):
        cfg = {}
        print("\nEnter weekly MINUTES per course (multiple of 20; Enter = default):\n")
        for subj, pr in COURSE_PROFILES.items():
            while True:
                v = input(f"{subj:<20} [{pr['default_mins']}]: ").strip()
                if not v: cfg[subj] = pr['default_mins']; break
                try:
                    m = int(v)
                    if m % 20: print("  ! multiple of 20"); continue
                    cfg[subj] = m; break
                except ValueError: print("  ! number")
    else:
        cfg = {s: p['default_mins'] for s, p in COURSE_PROFILES.items()}

    days, periods, long_idx, lunch_idx = 5, 7, [0, 4], 3
    s60 = days * len(long_idx); s40 = days * (periods - len(long_idx))
    avoid = sorted({p['category'] for p in COURSE_PROFILES.values() if p['load'] >= 4})

    req = sum(cfg.values()); avail = s60*60 + s40*40
    if req > avail:
        print(f"\nX Required {req} min > available {avail} min. Reduce hours."); return

    gen = ScheduleGenerator(days, periods, long_idx, lunch_idx, avoid)
    gen.blocks = plan_blocks(cfg, s60, s40)
    n60 = sum(1 for b in gen.blocks if b.duration == 60)
    n40 = sum(1 for b in gen.blocks if b.duration == 40)
    print(f"\nBlock plan: {n60}x60min (slots {s60}), {n40}x40min (slots {s40}), free={avail-req}min")

    ok, lvl = gen.solve()
    if not ok:
        print("\nX No layout found (hours likely incompatible with 40/60 grid)."); return
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
        if s.subject: tot[s.subject] = tot.get(s.subject, 0) + s.duration
    print("\n" + "="*65); print("  WEEKLY TOTALS"); print("="*65)
    for subj in COURSE_PROFILES:
        m = tot.get(subj, 0); h, mm = divmod(m, 60)
        print(f"  {subj:<22}{m:>4} min ({h}h{mm:02d})")
    print(f"\n  Study={req}min  ShortBreaks={50*days}min  Lunch={70*days}min")

if __name__ == "__main__":
    main()
