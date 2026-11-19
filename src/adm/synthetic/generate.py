"""Seeded synthetic data generator for "Project Meridian".

Usage:  python -m adm.synthetic.generate [--out data/synthetic] [--seed 42]

Writes one CSV per entity, a combined ``programme_data.xlsx`` and a deliberately broken
``data/samples/dirty_import.xlsx`` for demonstrating import validation. Output is
deterministic for a given seed. All names, products and events are fictional.
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from adm.config import PROJECT_ROOT
from adm.ingestion.schemas import ENTITIES
from adm.rules.raid import similarity
from adm.synthetic.scenarios import AS_OF

GENERIC_MAX_SIMILARITY = 0.45   # well below the duplicate rule's 0.6 threshold

SEED = 42
PROGRAMME_ID = "MER"
PROGRAMME_START = date(2026, 8, 10)
SPRINT_DAYS = 14
SPRINTS_PER_PI = 5
N_SPRINTS = 10
CURRENT_SPRINT = 8          # AS_OF (2026-11-19) falls in sprint 8 (2026-11-16 .. 2026-11-29)

Record = dict[str, object]
Dataset = dict[str, list[Record]]

SQUADS = [
    # id, name, domain, team size, capacity points per sprint
    ("PAY", "Payments", "Payments & Transfers", 8, 40),
    ("ONB", "Customer Onboarding", "KYC & Account Opening", 7, 36),
    ("DAT", "Data Platform", "Data & Analytics", 8, 38),
    ("MOB", "Mobile App", "Digital Channels", 9, 44),
    ("IDS", "Identity & Security", "IAM & Cyber Security", 7, 34),
]
SQUAD_IDS = [s[0] for s in SQUADS]
TEAM_SIZE = {s[0]: s[3] for s in SQUADS}
CAPACITY = {s[0]: s[4] for s in SQUADS}

PEOPLE = [
    "Amara Okafor", "Ben Hartley", "Chen Wei", "Deepa Rao", "Elliot Grant", "Fatima Malik",
    "George Mensah", "Hannah Lowe", "Isaac Morgan", "Jade Thompson", "Kofi Asante",
    "Lena Fischer", "Marcus Bell", "Nadia Hussain", "Owen Price", "Priya Nair",
]

WORK_OBJECTS = {
    "PAY": ["faster payments retry logic", "standing order API", "confirmation of payee check",
            "payment limits service", "ISO 20022 message mapper", "refund workflow",
            "fraud score hook", "ledger reconciliation job", "scheduled payments UI API",
            "payment status webhooks"],
    "ONB": ["eKYC document capture", "address lookup integration", "sanctions screening call",
            "applicant risk scoring", "account opening journey", "consent capture",
            "duplicate applicant check", "onboarding case dashboard", "ID&V fallback flow",
            "welcome pack trigger"],
    "DAT": ["CDC ingestion pipeline", "customer 360 model", "data quality checks",
            "lineage metadata capture", "PII tokenisation", "lakehouse partitioning",
            "regulatory extract", "event schema registry", "data access policies",
            "feature store table"],
    "MOB": ["biometric login screen", "push notification service", "card freeze toggle",
            "transaction search", "spending insights widget", "offline mode cache",
            "accessibility fixes pass", "app store release pipeline", "in-app chat entry",
            "payee management screen"],
    "IDS": ["SSO federation for colleague apps", "MFA step-up policy", "privileged access vault",
            "token revocation service", "audit log forwarding", "secrets rotation job",
            "role mining report", "session management hardening", "threat model update",
            "certificate lifecycle automation"],
}
VERBS = ["Implement", "Build", "Refactor", "Add", "Integrate", "Automate", "Expose", "Harden"]
DEFECTS = ["crash on resume", "timeout under load", "incorrect rounding", "missing validation",
           "UI overlap on small screens", "stale cache after logout", "wrong error message"]

FEATURES = {
    "PAY": ["Instant payments core", "Payee confirmation", "Refunds & reversals",
            "Payment limits", "Scheduled payments", "Payment notifications",
            "ISO 20022 migration", "Instant payments go-live readiness"],
    "ONB": ["eKYC integration", "Digital ID&V", "Sanctions screening", "Applicant risk scoring",
            "Digital account opening journey", "Consent management", "Fraud checks at onboarding",
            "Onboarding operations dashboard"],
    "DAT": ["Data lake landing zone", "CDC from core banking", "Customer 360 model",
            "Data quality framework", "PII protection", "Regulatory reporting extracts",
            "Customer data lake go-live", "Self-service analytics"],
    "MOB": ["Biometric login", "Card controls", "Transaction search", "Push notifications",
            "Spending insights", "Mobile app beta release", "Accessibility uplift",
            "In-app support"],
    "IDS": ["Colleague SSO", "MFA uplift", "Privileged access management", "Secrets management",
            "Audit & monitoring", "Certificate automation", "Pen test remediation",
            "Zero-trust pilot"],
}


# --- helpers ---------------------------------------------------------------------

def sprint_dates(n: int) -> tuple[date, date]:
    start = PROGRAMME_START + timedelta(days=(n - 1) * SPRINT_DAYS)
    return start, start + timedelta(days=SPRINT_DAYS - 1)


def sprint_id(n: int) -> str:
    return f"S{n:02d}"


def pi_of(n: int) -> int:
    return 1 if n <= SPRINTS_PER_PI else 2


def pick_points(rng: random.Random) -> int:
    return rng.choices([1, 2, 3, 5, 8], weights=[1, 3, 4, 3, 1])[0]


def squad_profile(rng: random.Random, squad: str, n: int) -> dict[str, float]:
    """Per-sprint behaviour that creates each squad's personality (closed sprints)."""
    cap = CAPACITY[squad]
    if squad == "PAY":
        return {"commit": cap + rng.randint(-2, 2), "say_do": 0.93 + rng.uniform(-0.02, 0.04),
                "added": rng.randint(0, 3), "removed": 0, "defects": rng.randint(0, 1),
                "blocked_p": 0.05}
    if squad == "ONB":
        return {"commit": cap + rng.randint(-2, 2),
                "say_do": 0.92 - 0.045 * (n - 1) + rng.uniform(-0.02, 0.02),
                "added": 6 + int(1.5 * n) + rng.randint(0, 2),
                "removed": rng.randint(2, 5) if n > SPRINTS_PER_PI else 0,
                "defects": rng.randint(0, 2), "blocked_p": 0.10}
    if squad == "DAT":
        return {"commit": cap + rng.randint(-2, 2),
                "say_do": 0.88 - 0.04 * (n - 1) + rng.uniform(-0.02, 0.02),
                "added": rng.randint(1, 4), "removed": rng.randint(0, 2), "defects": 1,
                "blocked_p": 0.25 if n <= SPRINTS_PER_PI else 0.45}
    if squad == "MOB":
        swing = [1.00, 0.80, 1.10, 0.75, 1.05, 0.70, 0.95, 1.0][n - 1]
        say_do = [0.92, 0.55, 0.95, 0.60, 0.90, 0.50, 0.62, 0.0][n - 1]
        return {"commit": int(cap * swing), "say_do": say_do + rng.uniform(-0.02, 0.02),
                "added": rng.randint(2, 6), "removed": rng.randint(0, 3),
                "defects": rng.randint(5, 8) if n >= 6 else rng.randint(0, 2),
                "blocked_p": 0.10}
    # IDS
    return {"commit": cap + rng.randint(-2, 2), "say_do": 0.80 + rng.uniform(-0.04, 0.04),
            "added": rng.randint(0, 3), "removed": rng.randint(0, 1), "defects": rng.randint(0, 1),
            "blocked_p": 0.12}


# --- generators per entity ------------------------------------------------------------

def gen_reference() -> Dataset:
    end = sprint_dates(N_SPRINTS)[1]
    data: Dataset = {
        "programme": [{
            "programme_id": PROGRAMME_ID, "name": "Project Meridian",
            "description": "Fictional retail-bank digital modernisation programme",
            "start_date": PROGRAMME_START, "end_date": end,
        }],
        "squad": [
            {"squad_id": sid, "programme_id": PROGRAMME_ID, "name": name, "domain": domain,
             "team_size": size, "capacity_points_per_sprint": cap}
            for sid, name, domain, size, cap in SQUADS
        ],
        "program_increment": [],
        "sprint": [],
    }
    for pi in (1, 2):
        first, last = (pi - 1) * SPRINTS_PER_PI + 1, pi * SPRINTS_PER_PI
        data["program_increment"].append({
            "pi_id": f"PI-{pi}", "programme_id": PROGRAMME_ID,
            "name": f"PI {pi} - {'Foundations' if pi == 1 else 'Scale & Launch'}",
            "start_date": sprint_dates(first)[0], "end_date": sprint_dates(last)[1],
        })
    for n in range(1, N_SPRINTS + 1):
        start, end_ = sprint_dates(n)
        data["sprint"].append({
            "sprint_id": sprint_id(n), "pi_id": f"PI-{pi_of(n)}", "name": f"Sprint {n}",
            "number": n, "start_date": start, "end_date": end_,
        })
    return data


def gen_features(rng: random.Random) -> list[Record]:
    out = []
    pi1_status = {
        "PAY": ["Done"] * 4, "ONB": ["Done", "Done", "Done", "In Progress"],
        "DAT": ["Done", "Done", "In Progress", "In Progress"],
        "MOB": ["Done", "Done", "Done", "In Progress"],
        "IDS": ["Done", "Done", "Done", "In Progress"],
    }
    for sid in SQUAD_IDS:
        for i, title in enumerate(FEATURES[sid]):
            pi = 1 if i < 4 else 2
            planned = rng.randint(3, 10)
            if pi == 1:
                status = pi1_status[sid][i]
                actual = max(planned - rng.randint(0, 1), 0) if status == "Done" else None
                target = sprint_id(3 + i % 3)
            else:
                status = "In Progress" if i < 7 else "Not Started"
                actual = None
                target = sprint_id(8 + i % 3)
            out.append({
                "feature_id": f"F-{sid}-{i + 1:02d}", "squad_id": sid, "pi_id": f"PI-{pi}",
                "title": title, "business_value_planned": planned,
                "business_value_actual": actual, "status": status, "target_sprint_id": target,
            })
    return out


def gen_work(rng: random.Random, features: list[Record]) -> tuple[list[Record], list[Record]]:
    items: list[Record] = []
    commitments: list[Record] = []
    counters = dict.fromkeys(SQUAD_IDS, 0)

    def feats(sid: str, pi: int, statuses: tuple[str, ...] | None = None) -> list[str]:
        return [str(f["feature_id"]) for f in features
                if f["squad_id"] == sid and f["pi_id"] == f"PI-{pi}"
                and (statuses is None or f["status"] in statuses)]

    def eligible_features(sid: str, n: int) -> list[str]:
        """Sprint work only links to features that have started; 'Not Started' features
        only receive refined backlog items in future sprints."""
        if n > CURRENT_SPRINT:
            return feats(sid, pi_of(n))
        return feats(sid, pi_of(n), ("In Progress", "Done"))

    def new_item(sid: str, n: int, kind: str, created: date, points: int) -> Record:
        counters[sid] += 1
        if kind == "Defect":
            title = f"Fix {rng.choice(DEFECTS)} in {rng.choice(WORK_OBJECTS[sid])}"
        elif kind == "Spike":
            title = f"Spike: options for {rng.choice(WORK_OBJECTS[sid])}"
        else:
            title = f"{rng.choice(VERBS)} {rng.choice(WORK_OBJECTS[sid])}"
        return {
            "work_item_id": f"{sid}-{counters[sid]:04d}",
            "feature_id": rng.choice(eligible_features(sid, n)), "squad_id": sid,
            "sprint_id": sprint_id(n), "type": kind, "title": title, "story_points": points,
            "status": "To Do", "created_date": created, "done_date": None,
            "blocked_days": 0, "carried_over_count": 0,
        }

    def relink_spillover(sid: str, n: int, pool: list[Record]) -> None:
        """A feature marked Done cannot have unfinished work in its PI's last sprint.
        Move that spill-over to a PI feature still in progress, or (if every PI feature
        is done, e.g. Payments) to the squad's lead feature in the next PI."""
        status = {f["feature_id"]: f["status"] for f in features}
        targets = feats(sid, pi_of(n), ("In Progress",)) or feats(sid, pi_of(n) + 1)[:1]
        for it in pool:
            if it["status"] == "Not Done" and status[it["feature_id"]] == "Done" and targets:
                it["feature_id"] = rng.choice(targets)

    for n in range(1, CURRENT_SPRINT + 1):
        start, end = sprint_dates(n)
        closed = n < CURRENT_SPRINT
        for sid in SQUAD_IDS:
            prof = squad_profile(rng, sid, n)
            planned: list[Record] = []
            while sum(int(i["story_points"]) for i in planned) < prof["commit"]:
                kind = "Spike" if rng.random() < 0.05 else "Story"
                planned.append(new_item(sid, n, kind, start - timedelta(days=rng.randint(1, 20)),
                                        pick_points(rng)))
            removed: list[Record] = []
            while sum(int(i["story_points"]) for i in removed) < prof["removed"]:
                it = new_item(sid, n, "Story", start - timedelta(days=rng.randint(1, 20)),
                              pick_points(rng))
                it["status"] = "Removed"
                removed.append(it)
            added: list[Record] = []
            for _ in range(int(prof["defects"])):
                added.append(new_item(sid, n, "Defect", start + timedelta(days=rng.randint(1, 8)),
                                      rng.choice([1, 2, 3])))
            while sum(int(i["story_points"]) for i in added) < prof["added"]:
                added.append(new_item(sid, n, "Story", start + timedelta(days=rng.randint(2, 9)),
                                      pick_points(rng)))

            committed = sum(int(i["story_points"]) for i in planned + removed)
            pool = planned + added
            rng.shuffle(pool)
            done_pts = 0
            if closed:
                target = prof["say_do"] * committed
                for it in pool:
                    if done_pts < target:
                        it["status"] = "Done"
                        done_pts += int(it["story_points"])
                    else:
                        it["status"] = "Not Done"
                        it["carried_over_count"] = 1
            else:  # current sprint, in flight
                days_in = (AS_OF - start).days
                for it in pool:
                    r = rng.random()
                    if r < 0.30 and it["created_date"] < AS_OF:
                        it["status"] = "Done"
                        done_pts += int(it["story_points"])
                    else:
                        it["status"] = "In Progress" if r < 0.60 else "To Do"
            for it in pool:
                if it["status"] == "Done":
                    created = it["created_date"]
                    lo = max(start, created + timedelta(days=1))
                    hi = end if closed else AS_OF
                    span = max((hi - lo).days, 0)
                    it["done_date"] = lo + timedelta(days=rng.randint(0, span))
                blocked_p = prof["blocked_p"] * (2 if it["status"] == "Not Done" else 1)
                if rng.random() < blocked_p:
                    it["blocked_days"] = rng.randint(3, 8) if sid == "DAT" else rng.randint(1, 3)
            if not closed:
                days_in = (AS_OF - start).days
                for it in pool:
                    it["blocked_days"] = min(int(it["blocked_days"]), days_in)
            if n % SPRINTS_PER_PI == 0:
                relink_spillover(sid, n, pool)

            items.extend(planned + removed + added)
            commitments.append({
                "commitment_id": f"SC-{sprint_id(n)}-{sid}", "sprint_id": sprint_id(n),
                "squad_id": sid, "committed_points": committed,
                "completed_points": done_pts,
                "added_points": sum(int(i["story_points"]) for i in added),
                "removed_points": sum(int(i["story_points"]) for i in removed),
                "carried_over_points": sum(int(i["story_points"]) for i in pool
                                           if i["status"] == "Not Done"),
                "available_capacity_days": float(TEAM_SIZE[sid] * 10 - rng.randint(0, 6)),
            })

    # Refined backlog for future sprints: planned, not started, no commitment record yet
    # (commitments are only created when a sprint starts).
    for n in range(CURRENT_SPRINT + 1, N_SPRINTS + 1):
        for sid in SQUAD_IDS:
            for _ in range(rng.randint(6, 8)):
                kind = "Spike" if rng.random() < 0.08 else "Story"
                items.append(new_item(sid, n, kind, AS_OF - timedelta(days=rng.randint(1, 30)),
                                      pick_points(rng)))
    items.sort(key=lambda r: str(r["work_item_id"]))
    return items, commitments


def gen_objectives(rng: random.Random, features: list[Record]) -> list[Record]:
    achievement = {"PAY": 0.97, "ONB": 0.80, "DAT": 0.55, "MOB": 0.75, "IDS": 0.82}
    out = []
    for pi in (1, 2):
        for sid in SQUAD_IDS:
            titles = [f["title"] for f in features
                      if f["squad_id"] == sid and f["pi_id"] == f"PI-{pi}"]
            for k in range(5):
                desc = (f"Deliver {titles[k]}" if k < 4
                        else f"Stretch: early adoption metrics for {titles[0]}")
                planned = rng.randint(4, 10)
                actual = (round(planned * achievement[sid] + rng.uniform(-0.4, 0.4))
                          if pi == 1 else None)
                out.append({
                    "objective_id": f"OBJ-PI{pi}-{sid}-{k + 1}", "pi_id": f"PI-{pi}",
                    "squad_id": sid, "description": desc, "committed": "Yes" if k < 4 else "No",
                    "planned_bv": planned,
                    "actual_bv": None if actual is None else max(0, min(10, actual)),
                })
    return out


def gen_milestones() -> list[Record]:
    d = date
    rows = [
        ("MS-01", None, "Programme mobilised", d(2026, 8, 14), d(2026, 8, 14), d(2026, 8, 14)),
        ("MS-02", "PAY", "Payments API v1 live", d(2026, 9, 18), d(2026, 9, 18), d(2026, 9, 18)),
        ("MS-03", "ONB", "eKYC vendor integrated", d(2026, 9, 25), d(2026, 10, 2),
         d(2026, 10, 2)),
        ("MS-04", "DAT", "Data lake landing zone ready", d(2026, 9, 30), d(2026, 10, 9),
         d(2026, 10, 9)),
        ("MS-05", None, "PI 1 system demo", d(2026, 10, 16), d(2026, 10, 16), d(2026, 10, 16)),
        ("MS-06", "MOB", "Mobile app beta release", d(2026, 11, 20), d(2026, 11, 27), None),
        ("MS-07", "DAT", "Customer data lake go-live", d(2026, 12, 4), d(2026, 12, 24), None),
        ("MS-08", "IDS", "SSO live for colleague apps", d(2026, 12, 11), d(2026, 12, 11), None),
        ("MS-09", "ONB", "Digital account opening live", d(2026, 12, 11), d(2026, 12, 18), None),
        ("MS-10", "PAY", "Instant payments go-live", d(2026, 12, 18), d(2026, 12, 18), None),
        ("MS-11", "IDS", "Penetration test sign-off", d(2026, 12, 4), d(2026, 12, 4), None),
        ("MS-12", None, "PI 2 system demo", d(2026, 12, 24), d(2026, 12, 24), None),
    ]
    owners = {None: "Priya Nair", "PAY": "Ben Hartley", "ONB": "Fatima Malik",
              "DAT": "Chen Wei", "MOB": "Jade Thompson", "IDS": "Kofi Asante"}
    return [{
        "milestone_id": mid, "programme_id": PROGRAMME_ID, "squad_id": sid, "name": name,
        "baseline_date": base, "forecast_date": fc, "actual_date": act,
        "status": "Complete" if act else "Planned", "owner": owners[sid],
    } for mid, sid, name, base, fc, act in rows]


# Planted dependencies: id -> (requesting, providing, external, description, needed_by,
#                              committed_date, blocks_milestone)
PLANTED_DEPS = {
    "DEP-005": ("DAT", None, "Core banking vendor", "CDC change feed from core banking",
                date(2026, 10, 23), date(2026, 11, 27), None),
    "DEP-009": ("DAT", "ONB", None, "Customer event schema v2 published",
                date(2026, 11, 6), date(2026, 11, 20), None),
    "DEP-012": ("DAT", "IDS", None, "Privileged access model for the customer data lake",
                date(2026, 10, 29), date(2026, 11, 26), "MS-07"),
    "DEP-017": ("MOB", "IDS", None, "Biometric login SDK token exchange endpoint",
                date(2026, 11, 13), date(2026, 11, 24), "MS-06"),
    "DEP-021": ("ONB", None, "KYC vendor", "Production KYC sandbox credentials",
                date(2026, 11, 2), None, "MS-09"),
    "DEP-024": ("DAT", "MOB", None, "Mobile analytics event stream",
                date(2026, 11, 10), date(2026, 11, 25), None),
    "DEP-028": ("IDS", None, "Pen test vendor", "Pen test window confirmed",
                date(2026, 11, 16), None, "MS-11"),
    "DEP-030": ("ONB", "DAT", None, "Customer 360 read API for duplicate checks",
                date(2026, 11, 12), date(2026, 12, 3), "MS-09"),
    "DEP-014": ("MOB", "ONB", None, "Onboarding status API for app home screen",
                date(2026, 12, 2), date(2026, 12, 9), None),
    "DEP-033": ("ONB", "IDS", None, "Step-up MFA policy for account opening",
                date(2026, 12, 7), date(2026, 12, 11), "MS-09"),
}


def gen_dependencies(rng: random.Random) -> list[Record]:
    out = []
    pairs = [(a, b) for a in SQUAD_IDS for b in SQUAD_IDS if a != b]
    for k in range(1, 36):
        dep_id = f"DEP-{k:03d}"
        if dep_id in PLANTED_DEPS:
            req, prov, ext, desc, need, commit, blocks = PLANTED_DEPS[dep_id]
            out.append({
                "dependency_id": dep_id, "requesting_squad_id": req, "providing_squad_id": prov,
                "external_party": ext, "description": desc, "needed_by_date": need,
                "committed_date": commit, "delivered_date": None,
                "status": "Committed" if commit else "Requested", "blocks_milestone_id": blocks,
            })
            continue
        req, prov = rng.choice(pairs)
        obj = rng.choice(WORK_OBJECTS[prov])
        need = PROGRAMME_START + timedelta(days=rng.randint(20, 135))
        if need < AS_OF:
            delivered = need - timedelta(days=rng.randint(0, 5))
            row = {"committed_date": need - timedelta(days=rng.randint(0, 3)),
                   "delivered_date": delivered, "status": "Delivered"}
        elif (need - AS_OF).days > 21 and rng.random() < 0.4:
            row = {"committed_date": None, "delivered_date": None, "status": "Requested"}
        else:
            row = {"committed_date": need - timedelta(days=rng.randint(0, 7)),
                   "delivered_date": None, "status": "Committed"}
        out.append({
            "dependency_id": dep_id, "requesting_squad_id": req, "providing_squad_id": prov,
            "external_party": None, "description": f"{obj.capitalize()} available to {req}",
            "needed_by_date": need, "blocks_milestone_id": None, **row,
        })
    return out


RAID_TEXT = {
    "RISK": ["Key engineer attrition", "Test environment instability", "Vendor contract renewal",
             "Regulatory change in scope", "Performance under peak load", "Data migration quality",
             "Third-party API deprecation", "Budget pressure in Q1", "Skills gap in cloud tooling",
             "Release window clash with change freeze"],
    "ASSUMPTION": ["Cloud landing zone remains available", "Vendor SLAs unchanged",
                   "Change freeze dates confirmed", "Shared test data available"],
    "ISSUE": ["Test environment outage", "Build pipeline flaky tests", "Licence shortfall",
              "Late design sign-off", "Defect backlog growing"],
    "ACTION": ["Confirm release plan with change board", "Update capacity plan",
               "Agree test data strategy", "Review vendor SLA", "Run risk workshop",
               "Publish API contract"],
    "DECISION": ["Adopt trunk-based development", "Use managed Kafka", "Defer card tokenisation",
                 "Standardise on OpenAPI 3.1"],
}
RAID_DESCRIPTIONS = {
    "RISK": ["There is a risk that {base} affects the {obj}. If it materialises, {consequence}."],
    "ASSUMPTION": ["Planning assumes: {base} for the {obj}. If this proves false, "
                   "{consequence}."],
    "ISSUE": ["{base} is currently affecting the {obj}. {effect}"],
    "ACTION": ["{base} covering the {obj}. Agreed at the {forum}."],
    "DECISION": ["Agreed to {base} for the {obj}. Rationale: {rationale}."],
}
CONSEQUENCES = ["sprint commitments may be missed", "the next release window could slip",
                "rework would be needed before go-live", "additional vendor cost may be incurred",
                "downstream squads would be delayed"]
EFFECTS = ["Two stories are blocked pending resolution.", "Testing throughput is reduced.",
           "The team is working around it manually.", "A fix is scheduled for this sprint."]
FORUMS = ["squad stand-up", "Scrum of Scrums", "programme risk review", "PI planning",
          "sprint retrospective"]
RATIONALES = ["reduces integration effort", "aligns with the target architecture",
              "lowers operational risk", "keeps the PI scope achievable"]

PLANTED_DESCRIPTIONS = {
    "RAID-R-031": "Legacy IAM signing certificates expire in early December, before the colleague "
                  "SSO cutover (MS-08). Renewal is manual and no mitigation has been agreed.",
    "RAID-R-032": "Quarterly privileged access reviews are run by spreadsheet. An audit finding "
                  "is likely if reviews slip. No accountable owner has been named.",
    "RAID-R-033": "The pen test (MS-11) depends on a vendor window that is not yet confirmed "
                  "(DEP-028). High-severity findings would delay go-live. No owner assigned.",
    "RAID-A-018": "Confirm the external pen test window with the vendor so that MS-11 can hold.",
    "RAID-A-019": "Produce a dated plan to renew the legacy IAM certificates ahead of SSO "
                  "cutover (see RAID-R-031).",
    "RAID-A-020": "Agree accountable owners for the unowned Identity & Security risks.",
    "RAID-A-022": "Escalate the late privileged access model (DEP-012) to the Identity & "
                  "Security lead; it blocks the customer data lake go-live (MS-07).",
    "RAID-R-012": "App store review could reject the beta build (MS-06) for privacy-label "
                  "gaps. Pre-submission checklist in place; risk not reviewed recently.",
    "RAID-I-005": "Data lake build work is blocked until the privileged access model (DEP-012) "
                  "is delivered by Identity & Security. Milestone MS-07 is affected.",
    "RAID-R-020": "The KYC vendor may breach its response-time SLA during peak onboarding. "
                  "Trigger date has passed and the risk is still open.",
    "RAID-R-007": "The KYC vendor API enforces strict rate limits that peak onboarding volumes "
                  "could exceed, causing failed applications.",
    "RAID-R-027": "KYC vendor rate limiting under load could throttle onboarding requests "
                  "and degrade the applicant journey.",
}

RAID_COUNTS = {"RISK": 40, "ASSUMPTION": 8, "ISSUE": 12, "ACTION": 25, "DECISION": 5}
RAID_PREFIX = {"RISK": "R", "ASSUMPTION": "AS", "ISSUE": "I", "ACTION": "A", "DECISION": "D"}


def _planted_raid() -> list[Record]:
    d = date
    base = {"description": None, "linked_ids": None}

    def r(**kw) -> Record:
        return {**base, **kw}

    return [
        r(raid_id="RAID-R-031", type="RISK", squad_id="IDS",
          title="Legacy IAM certificates expire before SSO cutover", owner="Kofi Asante",
          probability=4, impact=5, status="Open", raised_date=d(2026, 10, 5),
          due_date=d(2026, 12, 1), last_updated=d(2026, 11, 14), mitigation=None,
          linked_ids="MS-08"),
        r(raid_id="RAID-R-032", type="RISK", squad_id="IDS",
          title="Privileged access reviews not automated", owner=None, probability=4, impact=4,
          status="Open", raised_date=d(2026, 9, 21), due_date=d(2026, 12, 4),
          last_updated=d(2026, 11, 12), mitigation="Interim manual quarterly review"),
        r(raid_id="RAID-R-033", type="RISK", squad_id="IDS",
          title="Pen test findings may delay go-live", owner=None, probability=3, impact=5,
          status="Open", raised_date=d(2026, 10, 19), due_date=d(2026, 12, 4),
          last_updated=d(2026, 11, 10), mitigation=None, linked_ids="MS-11;DEP-028"),
        r(raid_id="RAID-A-018", type="ACTION", squad_id="IDS",
          title="Book pen test vendor window", owner="Kofi Asante", status="Open",
          raised_date=d(2026, 10, 12), due_date=d(2026, 11, 2), last_updated=d(2026, 11, 9),
          linked_ids="DEP-028"),
        r(raid_id="RAID-A-019", type="ACTION", squad_id="IDS",
          title="Produce certificate renewal plan", owner="Lena Fischer", status="Open",
          raised_date=d(2026, 10, 14), due_date=d(2026, 11, 6), last_updated=d(2026, 11, 9),
          linked_ids="RAID-R-031"),
        r(raid_id="RAID-A-020", type="ACTION", squad_id="IDS",
          title="Assign owners to unowned security risks", owner="Kofi Asante",
          status="In Progress", raised_date=d(2026, 10, 20), due_date=d(2026, 11, 10),
          last_updated=d(2026, 11, 11)),
        r(raid_id="RAID-A-022", type="ACTION", squad_id="DAT",
          title="Escalate privileged access model to IDS lead", owner="Chen Wei", status="Open",
          raised_date=d(2026, 10, 30), due_date=d(2026, 11, 12), last_updated=d(2026, 11, 13),
          linked_ids="DEP-012"),
        r(raid_id="RAID-R-012", type="RISK", squad_id="MOB",
          title="App store review rejection delays beta", owner="Jade Thompson", probability=3,
          impact=4, status="Open", raised_date=d(2026, 9, 1), due_date=d(2026, 11, 27),
          last_updated=d(2026, 10, 10), mitigation="Pre-submission checklist", linked_ids="MS-06"),
        r(raid_id="RAID-I-005", type="ISSUE", squad_id="DAT",
          title="Data lake build blocked awaiting privileged access model", owner="Chen Wei",
          impact=5, status="Open", raised_date=d(2026, 9, 15), last_updated=d(2026, 11, 17),
          mitigation="Escalated via RAID-A-022", linked_ids="DEP-012;MS-07"),
        r(raid_id="RAID-R-020", type="RISK", squad_id="ONB",
          title="KYC vendor SLA breach during peak onboarding", owner="Fatima Malik",
          probability=4, impact=4, status="Open", raised_date=d(2026, 9, 28),
          due_date=d(2026, 11, 5), last_updated=d(2026, 11, 13),
          mitigation="Negotiate burst capacity", linked_ids="DEP-021"),
        r(raid_id="RAID-R-007", type="RISK", squad_id="ONB",
          title="Third-party KYC vendor API rate limits", owner="Fatima Malik", probability=3,
          impact=4, status="Open", raised_date=d(2026, 8, 24), due_date=d(2026, 12, 11),
          last_updated=d(2026, 11, 11), mitigation="Client-side throttling and queueing"),
        r(raid_id="RAID-R-027", type="RISK", squad_id="ONB",
          title="KYC vendor API rate limiting under load", owner="Owen Price", probability=3,
          impact=4, status="Open", raised_date=d(2026, 10, 26), due_date=d(2026, 12, 11),
          last_updated=d(2026, 11, 16), mitigation="Request higher rate tier"),
    ]


PROGRAMME_OBJECTS = ["release train cadence", "PI planning logistics", "programme budget",
                     "vendor management", "architecture runway", "change board approvals",
                     "shared test environments", "cloud cost controls"]


def gen_raid(rng: random.Random) -> list[Record]:
    """Generic RAID items are deliberately 'clean' so only planted items trigger hygiene rules:
    owned, recently updated, future due dates, mitigated risks, recent open issues, and titles
    unique within (type, squad) so they are not duplicate candidates."""
    by_id: dict[str, Record] = {}
    squad_weights = {"PAY": 2, "ONB": 2, "DAT": 2, "MOB": 2, "IDS": 4, None: 1}
    squads, weights = list(squad_weights), list(squad_weights.values())
    titles: dict[tuple[str, str | None], list[str]] = {}
    for typ, count in RAID_COUNTS.items():
        for k in range(1, count + 1):
            rid = f"RAID-{RAID_PREFIX[typ]}-{k:03d}"
            squad = rng.choices(squads, weights)[0]
            objects = WORK_OBJECTS[squad] if squad else PROGRAMME_OBJECTS
            existing = titles.setdefault((typ, squad), [])
            for _ in range(500):
                base, obj = rng.choice(RAID_TEXT[typ]), rng.choice(objects)
                title = f"{base}: {obj}"
                if all(similarity(title, t) < GENERIC_MAX_SIMILARITY for t in existing):
                    break
            else:
                raise RuntimeError(f"Could not find a distinct title for {rid}")
            existing.append(title)
            raised = PROGRAMME_START + timedelta(days=rng.randint(0, (AS_OF - PROGRAMME_START).days
                                                                  - 20))
            status = rng.choices(["Open", "In Progress", "Closed"], weights=[5, 3, 2])[0]
            if typ == "DECISION":
                status = "Closed"
            if typ == "ISSUE" and status != "Closed":
                raised = max(raised, AS_OF - timedelta(days=28))
            last_updated = AS_OF - timedelta(days=rng.randint(0, 10))
            prob = impact = None
            if typ in ("RISK", "ISSUE"):
                impact = rng.randint(1, 4)
            if typ == "RISK":
                prob = rng.randint(1, 4)
            due = AS_OF + timedelta(days=rng.randint(7, 60)) if typ != "DECISION" else None
            description = rng.choice(RAID_DESCRIPTIONS[typ]).format(
                base=base if typ in ("ISSUE", "ACTION") else base.lower(), obj=obj,
                consequence=rng.choice(CONSEQUENCES), effect=rng.choice(EFFECTS),
                forum=rng.choice(FORUMS), rationale=rng.choice(RATIONALES))
            by_id[rid] = {
                "raid_id": rid, "type": typ, "squad_id": squad,
                "title": title, "description": description,
                "owner": rng.choice(PEOPLE), "probability": prob, "impact": impact,
                "status": status, "raised_date": raised, "due_date": due,
                "last_updated": last_updated,
                "mitigation": "Mitigation plan agreed and tracked" if typ == "RISK" else None,
                "linked_ids": None,
            }
    for rec in _planted_raid():
        rec["description"] = PLANTED_DESCRIPTIONS[str(rec["raid_id"])]
        by_id[str(rec["raid_id"])] = rec
    return [by_id[k] for k in sorted(by_id)]


def entity_rng(seed: int, entity: str) -> random.Random:
    """One independent, seeded stream per entity: changing how one entity is generated
    never reshuffles the others. String seeds are hashed with SHA-512 by `random`, so this
    is stable across runs and machines (unaffected by PYTHONHASHSEED)."""
    return random.Random(f"{seed}:{entity}")


def generate_dataset(seed: int = SEED) -> Dataset:
    data = gen_reference()
    features = gen_features(entity_rng(seed, "feature"))
    items, commitments = gen_work(entity_rng(seed, "work_item"), features)
    data["sprint_squad_commitment"] = commitments
    data["feature"] = features
    data["work_item"] = items
    data["pi_objective"] = gen_objectives(entity_rng(seed, "pi_objective"), features)
    data["milestone"] = gen_milestones()
    data["dependency"] = gen_dependencies(entity_rng(seed, "dependency"))
    data["raid_item"] = gen_raid(entity_rng(seed, "raid_item"))
    return data


def to_frames(data: Dataset) -> dict[str, pd.DataFrame]:
    frames = {}
    for spec in ENTITIES:
        frames[spec.name] = pd.DataFrame(data[spec.name], columns=spec.columns, dtype=object)
    return frames


def write_dataset(data: Dataset, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    frames = to_frames(data)
    for name, df in frames.items():
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False, lineterminator="\n")
        written.append(path)
    xlsx = out_dir / "programme_data.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        for name, df in frames.items():
            df.to_excel(writer, sheet_name=name, index=False)
    written.append(xlsx)
    return written


def write_dirty_sample(path: Path) -> Path:
    """A small workbook with deliberate errors for demonstrating validation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    squad = pd.DataFrame([
        {"squad_id": "QA1", "programme_id": "XXX", "name": "Ghost squad", "domain": "",
         "team_size": 5, "capacity_points_per_sprint": 20},
        {"squad_id": "QA2", "programme_id": "MER", "name": "Bad size", "domain": "",
         "team_size": "abc", "capacity_points_per_sprint": 20},
    ])
    raid = pd.DataFrame([
        {"raid_id": "RAID-R-901", "type": "RISK", "squad_id": "PAY", "title": "Bad date",
         "owner": "Ben Hartley", "probability": 2, "impact": 3, "status": "Open",
         "raised_date": "31/13/2026", "due_date": "", "last_updated": "2026-11-18",
         "mitigation": "x", "linked_ids": ""},
        {"raid_id": "RAID-R-902", "type": "RISK", "squad_id": "PAY", "title": "",
         "owner": "Ben Hartley", "probability": 2, "impact": 3, "status": "Open",
         "raised_date": "2026-11-01", "due_date": "", "last_updated": "2026-11-18",
         "mitigation": "x", "linked_ids": ""},
        {"raid_id": "RAID-R-903", "type": "RISK", "squad_id": "PAY", "title": "Probability 9",
         "owner": "Ben Hartley", "probability": 9, "impact": 3, "status": "Open",
         "raised_date": "2026-11-01", "due_date": "", "last_updated": "2026-11-18",
         "mitigation": "x", "linked_ids": ""},
        {"raid_id": "RAID-R-904", "type": "RISK", "squad_id": "ZZZ", "title": "Unknown squad",
         "owner": "Ben Hartley", "probability": 2, "impact": 3, "status": "Open",
         "raised_date": "2026-11-01", "due_date": "", "last_updated": "2026-11-18",
         "mitigation": "x", "linked_ids": ""},
        {"raid_id": "RAID-R-905", "type": "RISK", "squad_id": "PAY", "title": "No impact",
         "owner": "Ben Hartley", "probability": 2, "impact": "", "status": "Open",
         "raised_date": "2026-11-01", "due_date": "", "last_updated": "2026-11-18",
         "mitigation": "x", "linked_ids": ""},
        {"raid_id": "RAID-R-906", "type": "RISK", "squad_id": "PAY", "title": "Valid but bad link",
         "owner": "Ben Hartley", "probability": 2, "impact": 3, "status": "Open",
         "raised_date": "2026-11-01", "due_date": "", "last_updated": "2026-11-18",
         "mitigation": "x", "linked_ids": "DEP-999"},
    ])
    work = pd.DataFrame([
        {"work_item_id": "PAY-9001", "feature_id": "", "squad_id": "PAY", "sprint_id": "S08",
         "type": "Story", "title": "Done without date", "story_points": 3, "status": "Done",
         "created_date": "2026-11-10", "done_date": "", "blocked_days": 0,
         "carried_over_count": 0},
    ])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        squad.to_excel(writer, sheet_name="squad", index=False)
        raid.to_excel(writer, sheet_name="raid_item", index=False)
        work.to_excel(writer, sheet_name="work_item", index=False)
        pd.DataFrame([{"note": "unknown sheet"}]).to_excel(writer, sheet_name="notes",
                                                           index=False)
    return path


DEFAULT_OUT = PROJECT_ROOT / "data" / "synthetic"
DIRTY_SAMPLE = PROJECT_ROOT / "data" / "samples" / "dirty_import.xlsx"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    data = generate_dataset(args.seed)
    paths = write_dataset(data, args.out)
    write_dirty_sample(DIRTY_SAMPLE)
    for spec in ENTITIES:
        print(f"{spec.name:<25} {len(data[spec.name]):>5} rows")
    print(f"Wrote {len(paths)} files to {args.out} and {DIRTY_SAMPLE}")


if __name__ == "__main__":
    main()
