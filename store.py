"""
store.py — the tasting journal.

An append-only folder of small immutable JSON files, living in the OneDrive app folder that the
phone and the PC both reach. Nothing is ever edited in place, which is what makes two devices
writing at the same moment safe: they write different filenames into one folder and OneDrive
merges it without conflict copies.

  tastings/    T-<ts>-<spirit>-<rand>-r<n>.json   one scorecard sitting, immutable
  sessions/    F-<ts>-<rand>-r<n>.json       one flight/sitting that groups pours, immutable
  encounters/  E-<ts>-<rand>.json            spirits scored but never owned (bar pours)
  pending/     P-<ts>-<rand>.json            new-bottle requests awaiting approval on the PC
  pending/rejected/                          declined requests, kept not deleted
  retired/     <code>.json                    a spirit that has left the collection workbook
  snapshot/    collection.json               written by the PC, read by the phone
  meta/        codes.json                    X- display codes assigned by the PC
  meta/        high_water.json               highest code number ever seen, per sheet

Session ids use an `F-` (flight) prefix deliberately: `S-` is already a Sample bottle code, and a
session id sitting in a `spirit_id`-shaped field would be a nasty thing to debug.

Corrections are a NEW revision file, never an edit; deletions are a revision with deleted=true.
Readers resolve the highest revision per tasting_id. See SPEC.md §1.2, §1.3, §9.1.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

import rubric as rubric_mod

SUBDIRS = ("tastings", "sessions", "encounters", "pending", "pending/rejected", "retired",
           "snapshot", "meta")
TASTING_RE = re.compile(r"^(?P<id>T-\d{8}-\d{6}-[A-Za-z0-9_.-]+)-r(?P<rev>\d+)\.json$")
SESSION_RE = re.compile(r"^(?P<id>F-\d{8}-\d{6}-[A-Za-z0-9]+)-r(?P<rev>\d+)\.json$")


def _stamp(dt=None):
    return (dt or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")


def _now_iso():
    """Microsecond precision, deliberately. Second-resolution timestamps tie when two records are
    created in the same second, and ordering then falls back to a random filename suffix — which
    made assign_encounter_codes() hand X-1 to the second encounter."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _atomic_write_json(path: Path, payload: dict):
    """Write via a temp file in the same directory, then replace. A half-written journal file
    would be worse than a missing one, and os.replace is atomic on Windows and POSIX alike."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


class Journal:
    def __init__(self, root, rubric=None):
        self.root = Path(root)
        self.rubric = rubric or rubric_mod.load_rubric()

    # -- setup ---------------------------------------------------------------
    def ensure(self):
        for d in SUBDIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        return self

    def _dir(self, name):
        return self.root / name

    # -- writing -------------------------------------------------------------
    def write_tasting(self, *, spirit_id, scores=None, notes=None, session_id=None, date=None,
                      venue=None, pour_price=None, pour_size_oz=None, flight_pos=None,
                      include_in_average=True, status="submitted", entered_from="pc",
                      tasting_id=None, revision=None, tags=None, blind=False, barrel_id=None):
        """Record one sitting. Omit tasting_id for a new card; pass it to add a revision.

        A card may be unscored only when it is a draft — that is the Quick Entry lane, where a
        row is typed one-handed with notes and scored later (SPEC.md §1.2). An unscored card can
        never be counted towards a career score, whatever the caller asks for: rubric.career()
        would fail on empty scores, and a card with no numbers is not an opinion yet.
        """
        draft = str(status).lower() == "draft"
        if scores:
            # A draft may be half-filled. It began as the Quick Entry lane, where a row is typed
            # with no scores at all and scored later; autosave writes the same kind of record while
            # a card is still being worked on, which is partly scored rather than not at all. Out
            # of range is still wrong either way — incomplete is not the same as impossible.
            problems = (self.rubric.validate_partial(scores) if draft
                        else self.rubric.validate(scores))
            if problems:
                raise ValueError("; ".join(problems))
        elif not draft:
            raise ValueError("a submitted card needs scores; only a draft may be unscored")

        # An unfinished card has no total. rubric.total() insists on a whole card and would
        # otherwise raise here, and a running subtotal would be worse than nothing: it would read
        # as a low score rather than an incomplete one, and every average would be dragged down.
        complete = bool(scores) and not self.rubric.validate(scores)
        total = self.rubric.total(scores) if complete else None

        if tasting_id is None:
            # The random suffix is load-bearing, not decoration. Timestamps are second-resolution,
            # and two cards for the same spirit in the same second would otherwise share an id —
            # which means the same filename, which means the second write silently overwrites the
            # first. That is the exact immutability violation this whole design exists to prevent.
            tasting_id = f"T-{_stamp()}-{spirit_id}-{secrets.token_hex(2)}"
            revision = 1
        elif revision is None:
            revision = self._highest_revision(tasting_id) + 1

        rec = {
            "tasting_id": tasting_id,
            "revision": revision,
            "deleted": False,
            "spirit_id": spirit_id,
            "session_id": session_id,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "flight_pos": flight_pos,
            "scores": {k: int(v) for k, v in (scores or {}).items()},
            "barrel_id": barrel_id,
            "notes": notes or {},
            "tags": tags or [],
            "blind": bool(blind),
            "venue": venue,
            "pour_price": pour_price,
            "pour_size_oz": pour_size_oz,
            # A draft never counts, however complete it looks and whatever the caller asks for.
            # It is a card in progress, not an opinion yet.
            "include_in_average": bool(include_in_average) and complete and not draft,
            "status": status,
            "entered_from": entered_from,
            "rubric": self.rubric.name,
            "rubric_version": self.rubric.version,
            "total": total,
            "medal": self.rubric.medal(total) if total is not None else None,
            "created_at": _now_iso(),
        }
        path = self._dir("tastings") / f"{tasting_id}-r{revision}.json"
        if path.exists():
            raise FileExistsError(
                f"refusing to overwrite an existing revision: {path.name}. "
                "Journal files are immutable; write a new revision instead.")
        _atomic_write_json(path, rec)
        return rec

    def delete_tasting(self, tasting_id, reason=None):
        """A tombstone is just another revision. Nothing is unlinked."""
        rev = self._highest_revision(tasting_id) + 1
        if rev == 1:
            raise KeyError(f"no such tasting {tasting_id}")
        rec = {"tasting_id": tasting_id, "revision": rev, "deleted": True,
               "reason": reason, "created_at": _now_iso()}
        _atomic_write_json(self._dir("tastings") / f"{tasting_id}-r{rev}.json", rec)
        return rec

    # -- sessions (flights) --------------------------------------------------
    def write_session(self, *, title=None, date=None, location=None, company=None,
                      blind=False, notes=None, session_id=None, revision=None):
        """Start a flight, or amend one. Amending writes a new revision rather than editing the
        file — the same rule as tastings, so two devices can never clobber each other's copy."""
        if session_id is None:
            session_id = f"F-{_stamp()}-{secrets.token_hex(2)}"
            revision = 1
        elif revision is None:
            revision = self._highest_session_revision(session_id) + 1

        rec = {
            "session_id": session_id,
            "revision": revision,
            "deleted": False,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "title": title,
            "location": location,
            "company": company,
            "blind": bool(blind),
            "notes": notes,
            "created_at": _now_iso(),
        }
        path = self._dir("sessions") / f"{session_id}-r{revision}.json"
        if path.exists():
            raise FileExistsError(
                f"refusing to overwrite an existing revision: {path.name}. "
                "Journal files are immutable; write a new revision instead.")
        _atomic_write_json(path, rec)
        return rec

    def _session_files(self):
        for p in sorted(self._dir("sessions").glob("F-*.json")):
            m = SESSION_RE.match(p.name)
            if m:
                yield p, m.group("id"), int(m.group("rev"))

    def _highest_session_revision(self, session_id):
        return max((rev for _, sid, rev in self._session_files() if sid == session_id), default=0)

    def sessions(self):
        """Resolved view: highest revision wins, tombstones drop the record. Newest first."""
        best = {}
        for path, sid, rev in self._session_files():
            if sid not in best or rev > best[sid][0]:
                best[sid] = (rev, path)
        out = []
        for sid, (_, path) in best.items():
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec.get("deleted"):
                continue
            out.append(rec)
        return sorted(out, key=lambda r: (r.get("date") or "", r["created_at"]), reverse=True)

    def session(self, session_id):
        """One flight with its pours in flight order, or None if there is no such session."""
        rec = next((s for s in self.sessions() if s["session_id"] == session_id), None)
        if rec is None:
            return None
        pours = [t for t in self.tastings() if t.get("session_id") == session_id]
        pours.sort(key=lambda t: (t.get("flight_pos") is None, t.get("flight_pos") or 0,
                                  t.get("created_at") or ""))
        return dict(rec, pours=pours)

    def next_flight_pos(self, session_id):
        """1-based position for the next pour in this flight."""
        used = [t.get("flight_pos") or 0 for t in self.tastings()
                if t.get("session_id") == session_id]
        return (max(used) if used else 0) + 1

    def write_encounter(self, *, name, distillery=None, type=None, region=None, age=None,
                        proof=None, venue=None, notes=None, entered_from="phone"):
        """A spirit tasted but never owned. Filename carries a random suffix so two devices
        creating one at the same second cannot collide; the human-facing X- code is assigned
        later on the PC by assign_encounter_codes()."""
        uid = f"E-{_stamp()}-{secrets.token_hex(2)}"
        rec = {"encounter_uid": uid, "code": None, "name": name, "distillery": distillery,
               "type": type, "region": region, "age": age, "proof": proof, "venue": venue,
               "notes": notes, "linked_bottle_code": None, "entered_from": entered_from,
               "first_tasted": _now_iso(), "created_at": _now_iso()}
        _atomic_write_json(self._dir("encounters") / f"{uid}.json", rec)
        return rec

    def write_retired(self, *, code, fields=None, reason="deleted from the collection workbook"):
        """A spirit that has left the collection — a bottle finished and its row deleted.

        The row goes; the reviews do not. This keeps what the spirit *was* — its name, type,
        proof — so its sittings still mean something long after the workbook has forgotten it.
        Codes are never reissued (next_code is highest+1, not the first gap), so a retired code
        can never come to mean a different bottle.

        The filename is the code rather than a stamp with a random suffix, and deliberately so:
        there is exactly one of these per code and the write is guarded below, so the collision
        this project's naming rule exists to prevent cannot arise. The first record stands if a
        code is retired twice, because it is the one written nearest to when it was owned.
        """
        path = self._dir("retired") / f"{code}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        rec = {"code": code, "fields": dict(fields or {}), "reason": reason,
               "retired_at": _now_iso()}
        _atomic_write_json(path, rec)
        return rec

    def retired(self):
        """Every spirit recorded as having left the collection, oldest code first."""
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self._dir("retired").glob("*.json"))]

    def note_codes_seen(self, highest):
        """Remember the highest code number ever seen on each sheet, and never let it fall.

        next_code() is highest-present + 1, which is right until a row is deleted. Finish your
        newest bottle, delete its row, add another, and the workbook would hand out the code the
        old one had — quietly attaching its reviews to a different whiskey. Codes are cheap and
        the journal is forever, so a number once used is never used again.
        """
        path = self._dir("meta") / "high_water.json"
        known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        changed = False
        for sheet, n in (highest or {}).items():
            if int(n or 0) > int(known.get(sheet, 0)):
                known[sheet] = int(n)
                changed = True
        if changed:
            _atomic_write_json(path, known)
        return known

    def highest_seen(self, sheet):
        path = self._dir("meta") / "high_water.json"
        if not path.exists():
            return 0
        try:
            return int(json.loads(path.read_text(encoding="utf-8")).get(sheet, 0))
        except (OSError, ValueError, TypeError):
            return 0

    def write_pending_bottle(self, *, sheet, fields, entered_from="phone"):
        """A new-bottle request. Never touches the master workbook — SPEC.md §8.5."""
        if sheet not in ("Bottle", "Miniature", "Sample"):
            raise ValueError(f"unknown sheet {sheet!r}")
        uid = f"P-{_stamp()}-{secrets.token_hex(2)}"
        rec = {"pending_uid": uid, "sheet": sheet, "fields": dict(fields),
               "entered_from": entered_from, "created_at": _now_iso(), "state": "pending"}
        _atomic_write_json(self._dir("pending") / f"{uid}.json", rec)
        return rec

    def resolve_pending(self, pending_uid, *, approved, assigned_code=None, reason=None):
        src = self._dir("pending") / f"{pending_uid}.json"
        if not src.exists():
            raise KeyError(pending_uid)
        rec = json.loads(src.read_text(encoding="utf-8"))
        rec["state"] = "approved" if approved else "rejected"
        rec["resolved_at"] = _now_iso()
        rec["assigned_code"] = assigned_code
        rec["reason"] = reason
        if approved:
            src.unlink()
        else:
            _atomic_write_json(self._dir("pending/rejected") / f"{pending_uid}.json", rec)
            src.unlink()
        return rec

    # -- reading -------------------------------------------------------------
    def _tasting_files(self):
        for p in sorted(self._dir("tastings").glob("T-*.json")):
            m = TASTING_RE.match(p.name)
            if m:
                yield p, m.group("id"), int(m.group("rev"))

    def _highest_revision(self, tasting_id):
        return max((rev for _, tid, rev in self._tasting_files() if tid == tasting_id), default=0)

    def tastings(self, include_deleted=False):
        """Resolved view: highest revision wins, tombstones drop the record."""
        best = {}
        for path, tid, rev in self._tasting_files():
            if tid not in best or rev > best[tid][0]:
                best[tid] = (rev, path)
        out = []
        for tid, (_, path) in sorted(best.items()):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec.get("deleted") and not include_deleted:
                continue
            out.append(rec)
        return sorted(out, key=lambda r: (r.get("date") or "", r["tasting_id"]), reverse=True)

    def encounters(self):
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self._dir("encounters").glob("E-*.json"))]

    def pending(self):
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self._dir("pending").glob("P-*.json"))]

    # -- PC-side reconciliation ---------------------------------------------
    def assign_encounter_codes(self):
        """Give every encounter a stable X-n, oldest first. PC only; idempotent."""
        meta = self._dir("meta") / "codes.json"
        assigned = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
        nxt = max((int(c.split("-")[1]) for c in assigned.values()), default=0) + 1
        changed = False
        for enc in sorted(self.encounters(), key=lambda e: (e["first_tasted"], e["encounter_uid"])):
            uid = enc["encounter_uid"]
            if uid not in assigned:
                assigned[uid] = f"X-{nxt}"
                nxt += 1
                changed = True
            if enc.get("code") != assigned[uid]:
                enc["code"] = assigned[uid]
                _atomic_write_json(self._dir("encounters") / f"{uid}.json", enc)
        if changed:
            _atomic_write_json(meta, assigned)
        return assigned

    def career(self, spirit_id):
        """Every counted sitting for one spirit, aggregated. See SPEC.md §3.6."""
        sittings = [t for t in self.tastings() if t["spirit_id"] == spirit_id]
        result = self.rubric.career(sittings)
        result["spirit_id"] = spirit_id
        result["sittings"] = sittings
        return result

    def careers(self):
        return {sid: self.career(sid) for sid in {t["spirit_id"] for t in self.tastings()}}

    def stats(self):
        t = self.tastings()
        return {"tastings": len(t), "spirits_scored": len({x["spirit_id"] for x in t}),
                "sessions": len(self.sessions()),
                "encounters": len(self.encounters()), "retired": len(self.retired()),
                "pending": len(self.pending()),
                "files": sum(1 for _ in self._dir("tastings").glob("*.json"))}


def resolve_root(cfg, override=None) -> Path:
    for c in (override, os.environ.get("WHISKEY_APP_FOLDER"), cfg["paths"]["app_folder"]):
        if c:
            return Path(c)
    raise RuntimeError("no app folder configured")


def open_journal(config_path="config.yaml", root=None) -> Journal:
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return Journal(resolve_root(cfg, root), rubric_mod.Rubric(cfg["rubric"])).ensure()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Tasting journal status")
    ap.add_argument("--root", help="journal root (also WHISKEY_APP_FOLDER)")
    a = ap.parse_args()
    j = open_journal(root=a.root)
    print(f"Journal: {j.root}")
    for k, v in j.stats().items():
        print(f"  {k:<16} {v}")
