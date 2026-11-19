"""Ten sprints of MOCK Jira tasks for a fictional digital team.

    python -m sprintdash.mockdata          # writes data/sprint_tasks.csv

Team sizes: 15 UX/UI, 10 iOS, 10 Android, 4 React, 2 HTML, 6 Services.
One row per task per sprint: a task that is not finished appears again in the next
sprint with carried_over = Yes, as in Jira's sprint reports.

Stories planted for the dashboard to find:
  Sprint 35-36   iOS release with bugs: bug share and production support jump for iOS
  Sprint 37      public holiday week: everyone has less capacity
  Sprint 38-39   sales campaign: Sales requests rise, roadmap work drops, carry-over rises
  all sprints    HTML (2 people) is overloaded: highest load per person, most carry-over
  all sprints    change requests wait longest in the backlog (longest lead time)
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

TEAMS = {  # people, points per person per sprint, typical points per task, issue types
    "UX/UI": (15, 9, [1, 2, 3, 5], {"Story": 5, "Task": 4, "Spike": 1}),
    "iOS": (10, 10, [1, 2, 3, 5, 8], {"Story": 6, "Bug": 3, "Task": 2}),
    "Android": (10, 10, [1, 2, 3, 5, 8], {"Story": 6, "Bug": 3, "Task": 2}),
    "React": (4, 11, [1, 2, 3, 5, 8], {"Story": 6, "Bug": 2, "Task": 2}),
    "HTML": (2, 12, [1, 2, 3, 5], {"Story": 4, "Bug": 2, "Task": 4}),
    "Services": (6, 10, [2, 3, 5, 8], {"Story": 5, "Task": 3, "Bug": 2, "Spike": 1}),
}
ROLE = {"UX/UI": "UX Designer", "iOS": "iOS Dev", "Android": "Android Dev", "React": "React Dev",
        "HTML": "HTML Dev", "Services": "Services Dev"}  # fmt: skip
EPICS = ["Recharge revamp", "Bill payment", "Online shop checkout", "Roaming bundles", "Consent & privacy",
         "App search", "Onboarding", "Loyalty rewards"]  # fmt: skip
WORK = {
    "UX/UI": ["Design {e} screens", "Prototype {e} flow", "Usability test: {e}", "Design system update for {e}"],
    "iOS": ["iOS: build {e} screen", "iOS: {e} API integration", "iOS: fix {e} crash", "iOS: {e} tracking events"],
    "Android": ["Android: build {e} screen", "Android: {e} API integration", "Android: fix {e} layout",
                "Android: {e} tracking events"],
    "React": ["Web: {e} page", "Web: {e} form validation", "Web: {e} checkout step", "Web: {e} A/B variant"],
    "HTML": ["Landing page: {e}", "Email template: {e}", "Campaign banner: {e}", "Static page update: {e}"],
    "Services": ["API: {e} endpoint", "Service: {e} integration", "Batch job: {e}", "Service: {e} performance"],
}  # fmt: skip
PRIORITY = {"Highest": 0.04, "High": 0.18, "Medium": 0.58, "Low": 0.2}
FIRST_START = date(2026, 6, 29)
N_SPRINTS = 10


def _sprints():
    out = []
    for i in range(N_SPRINTS):
        start = FIRST_START + timedelta(days=14 * i)
        out.append((f"Sprint {31 + i}", start, start + timedelta(days=11)))  # Mon to Fri of week 2
    return out


def _source(rng: random.Random, team: str, s: int, issue_type: str) -> str:
    if issue_type == "Bug":
        return "Production Support" if rng.random() < 0.7 else "Roadmap"
    sales = 0.32 if s in (7, 8) else 0.09  # sprints 38-39
    cr = 0.12 if team != "UX/UI" else 0.06
    r = rng.random()
    return "Sales" if r < sales else "Change Request" if r < sales + cr else "Roadmap"


def _ts(d: date, rng: random.Random, lo: int = 9, hi: int = 18) -> datetime:
    return datetime(d.year, d.month, d.day, rng.randint(lo, hi - 1), rng.choice([0, 15, 30, 45]))


def generate(seed: int = 7) -> pd.DataFrame:
    rng = random.Random(seed)
    rows, key = [], 1000
    carry: dict[str, list[dict]] = {}  # person -> unfinished tasks
    for s, (sprint, start, end) in enumerate(_sprints()):
        holiday = 0.7 if s == 6 else 1.0  # sprint 37
        for team, (people, cap, sizes, types) in TEAMS.items():
            for p in range(1, people + 1):
                person = f"{ROLE[team]} {p:02d}"
                planned = cap * rng.uniform(0.9, 1.1)
                commit = planned * (1.35 if team == "HTML" else 1.0)
                capacity = planned * holiday * (0.9 if team == "HTML" else 1.0)
                tasks = [dict(t, carried_over="Yes", added_mid_sprint="No") for t in carry.pop(person, [])]
                load = sum(t["story_points"] for t in tasks)
                while load < commit:
                    itype = rng.choices(list(types), list(types.values()))[0]
                    if team == "iOS" and s in (4, 5) and rng.random() < 0.5:  # sprints 35-36
                        itype = "Bug"
                    src = _source(rng, team, s, itype)
                    if src == "Change Request":
                        itype = "Change Request"
                    pts = rng.choice(sizes[:3]) if itype == "Bug" else rng.choice(sizes)
                    mid = itype == "Bug" and rng.random() < 0.5 or rng.random() < 0.06
                    wait = rng.expovariate(1 / (38 if itype == "Change Request" else 3 if itype == "Bug" else 16))
                    created = (start + timedelta(days=rng.randint(1, 8))) if mid else start - timedelta(days=wait)
                    epic = rng.choice(EPICS)
                    key += 1
                    tasks.append({
                        "issue_key": f"APP-{key}",
                        "summary": rng.choice(WORK[team]).format(e=epic.lower()),
                        "issue_type": itype,
                        "priority": "High" if itype == "Bug" and rng.random() < 0.5
                        else rng.choices(list(PRIORITY), list(PRIORITY.values()))[0],
                        "resource_name": person,
                        "resource_team": team,
                        "request_source": src,
                        "story_points": pts,
                        "created": _ts(created, rng),
                        "epic": epic,
                        "carried_over": "No",
                        "added_mid_sprint": "Yes" if mid else "No",
                        "started": None,
                    })  # fmt: skip
                    load += pts
                # finish tasks in order until capacity runs out
                used, day = 0.0, 0.0
                rng.shuffle(tasks)
                tasks.sort(key=lambda t: (t["carried_over"] != "Yes", t["priority"] not in ("Highest", "High")))
                pressure = 0.85 if s in (7, 8) else 1.0
                for t in tasks:
                    fits = used + t["story_points"] <= capacity * 1.12 * pressure + rng.uniform(-1, 2)
                    if t["started"] is None and (fits or rng.random() < 0.5):
                        t["started"] = max(_ts(start + timedelta(days=min(int(day), 10)), rng), t["created"] + timedelta(hours=2))
                    if fits:
                        used += t["story_points"]
                        span = max(0.5, t["story_points"] * rng.uniform(0.5, 1.1) * (1.4 if team == "UX/UI" else 1))
                        day = min(day + span * 0.6, 11)
                        done_at = t["started"] + timedelta(days=span)
                        last = datetime.combine(end, datetime.min.time()) + timedelta(hours=18)
                        resolved = min(done_at, last)
                        rows.append({**t, "sprint_name": sprint, "sprint_start": start, "sprint_end": end,
                                     "status": "Done", "resolved": resolved})  # fmt: skip
                    else:
                        status = "In Progress" if t["started"] is not None else "To Do"
                        rows.append({**t, "sprint_name": sprint, "sprint_start": start, "sprint_end": end,
                                     "status": status, "resolved": None})  # fmt: skip
                        if s < N_SPRINTS - 1:
                            carry.setdefault(person, []).append(t)
    cols = ["issue_key", "summary", "issue_type", "status", "priority", "sprint_name", "sprint_start",
            "sprint_end", "resource_name", "resource_team", "request_source", "story_points", "created",
            "started", "resolved", "epic", "carried_over", "added_mid_sprint"]  # fmt: skip
    df = pd.DataFrame(rows)[cols]
    for c in ("created", "started", "resolved"):
        df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d %H:%M")
    return df


def main() -> None:
    out = Path("data/sprint_tasks.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df = generate()
    df.to_csv(out, index=False)
    print(f"{len(df):,} rows, {df['issue_key'].nunique():,} tasks, {df['sprint_name'].nunique()} sprints -> {out}")


if __name__ == "__main__":
    main()
