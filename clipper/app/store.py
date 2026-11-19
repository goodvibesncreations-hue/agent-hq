"""Tiny SQLite store for jobs (long videos) and clips."""
import json
import sqlite3
import threading
import time
from pathlib import Path

_lock = threading.Lock()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source TEXT NOT NULL,           -- URL, or 'upload:<filename>'
          title TEXT DEFAULT '',
          campaign TEXT DEFAULT '',
          settings TEXT DEFAULT '{}',     -- clips, mode, watermark, rules
          status TEXT DEFAULT 'queued',   -- queued, running, done, failed
          step TEXT DEFAULT 'Waiting in line',
          error TEXT DEFAULT '',
          created REAL, updated REAL
        );
        CREATE TABLE IF NOT EXISTS clips (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL,
          title TEXT, hook TEXT, caption TEXT, hashtags TEXT,
          start REAL, "end" REAL, score INTEGER,
          file TEXT, thumb TEXT,
          status TEXT DEFAULT 'pending',  -- pending, approved, rejected, posted
          created REAL
        );
        """)
        self.db.commit()

    def _q(self, sql, args=()):
        with _lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # jobs
    def add_job(self, source, campaign="", settings=None, title=""):
        now = time.time()
        cur = self._q("INSERT INTO jobs (source,title,campaign,settings,created,updated) VALUES (?,?,?,?,?,?)",
                      (source, title, campaign, json.dumps(settings or {}), now, now))
        return cur.lastrowid

    def job_exists(self, source):
        return self._q("SELECT id FROM jobs WHERE source=? AND status!='failed'", (source,)).fetchone() is not None

    def update_job(self, job_id, **fields):
        fields["updated"] = time.time()
        cols = ",".join(f"{k}=?" for k in fields)
        self._q(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def next_job(self):
        return self._q("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()

    def jobs(self, limit=50):
        return [dict(r) for r in self._q("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def get_job(self, job_id):
        r = self._q("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def reset_running(self):
        self._q("UPDATE jobs SET status='queued', step='Restarted, waiting in line' WHERE status='running'")

    # clips
    def add_clip(self, job_id, c, file, thumb):
        self._q("INSERT INTO clips (job_id,title,hook,caption,hashtags,start,\"end\",score,file,thumb,created) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, c["title"], c["hook"], c["caption"], " ".join(c["hashtags"]),
                 c["start"], c["end"], c["score"], file, thumb, time.time()))

    def clips(self, status=None, limit=200):
        sql = "SELECT clips.*, jobs.campaign, jobs.title AS job_title FROM clips JOIN jobs ON jobs.id=clips.job_id"
        args = ()
        if status:
            sql += " WHERE clips.status=?"
            args = (status,)
        sql += " ORDER BY clips.score DESC, clips.id DESC LIMIT ?"
        return [dict(r) for r in self._q(sql, (*args, limit)).fetchall()]

    def get_clip(self, clip_id):
        r = self._q("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        return dict(r) if r else None

    def set_clip_status(self, clip_id, status):
        self._q("UPDATE clips SET status=? WHERE id=?", (status, clip_id))

    def counts(self):
        out = {r["status"]: r["n"] for r in self._q("SELECT status, COUNT(*) n FROM clips GROUP BY status")}
        out["jobs_queued"] = self._q("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        return out
