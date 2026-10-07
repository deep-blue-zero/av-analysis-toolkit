"""Transactional local search over verified performance runs; no canonical promotion."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import unicodedata

from .common import AVError, read_json, sha256
from .inventory import verify_run


def normalized(text):
    return unicodedata.normalize("NFKC", text).casefold()


def connect(path, readonly=False):
    path = Path(path).resolve()
    if readonly:
        if not path.is_file():
            raise AVError("Evidence index does not exist; run performance-index first")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    if not readonly:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS index_meta (version INTEGER NOT NULL);
            INSERT INTO index_meta SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM index_meta);
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, source_path TEXT NOT NULL, label TEXT NOT NULL,
                audio_stream INTEGER NOT NULL, performance_key TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS cues (
                run_id TEXT NOT NULL REFERENCES runs(run_id), cue_id TEXT NOT NULL,
                start_s REAL NOT NULL, end_s REAL NOT NULL, speaker TEXT, text TEXT NOT NULL,
                search_text TEXT NOT NULL, status TEXT NOT NULL, pitch_hz REAL, rms_dbfs REAL,
                kind TEXT NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY(run_id, cue_id));
            CREATE INDEX IF NOT EXISTS cue_speaker ON cues(speaker);
            CREATE INDEX IF NOT EXISTS cue_time ON cues(run_id, start_s);
        """)
        existing_fts = db.execute("SELECT 1 FROM sqlite_master WHERE name='cue_fts'").fetchone()
        try:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS cue_fts USING fts5(search_text, content='cues', content_rowid='rowid', tokenize='trigram')")
        except sqlite3.OperationalError:
            # Some Python builds omit FTS5. Literal substring search still works.
            pass
        else:
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS cues_ai AFTER INSERT ON cues BEGIN
                    INSERT INTO cue_fts(rowid, search_text) VALUES (new.rowid, new.search_text); END;
                CREATE TRIGGER IF NOT EXISTS cues_ad AFTER DELETE ON cues BEGIN
                    INSERT INTO cue_fts(cue_fts, rowid, search_text) VALUES ('delete', old.rowid, old.search_text); END;
                CREATE TRIGGER IF NOT EXISTS cues_au AFTER UPDATE ON cues BEGIN
                    INSERT INTO cue_fts(cue_fts, rowid, search_text) VALUES ('delete', old.rowid, old.search_text);
                    INSERT INTO cue_fts(rowid, search_text) VALUES (new.rowid, new.search_text); END;
            """)
            if not existing_fts:
                db.execute("INSERT INTO cue_fts(cue_fts) VALUES ('rebuild')")
                db.commit()
    if db.execute("SELECT version FROM index_meta").fetchone()[0] != 1:
        db.close()
        raise AVError("Unsupported evidence index version")
    return db


def index_run(run_dir, database, label=None):
    folder = Path(run_dir).resolve()
    database = Path(database).resolve()
    if database.is_relative_to(folder):
        raise AVError("The mutable search index must be outside immutable evidence runs")
    verify_run(folder)
    manifest = read_json(folder / "run.json")
    if manifest["operation"] != "performance":
        raise AVError("Only performance runs can enter this index")
    report = read_json(folder / "performance.json")
    manifest_hash = sha256(folder / "run.json")
    key = report["performance_key"]
    db = connect(database)
    try:
        with db:
            # Reruns with identical evidence replace the location, never duplicate corpus counts.
            old = db.execute("SELECT run_id FROM runs WHERE performance_key=?", (key,)).fetchone()
            if old:
                db.execute("DELETE FROM cues WHERE run_id=?", (old[0],))
                db.execute("DELETE FROM runs WHERE run_id=?", (old[0],))
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)", (manifest_hash, str(folder), manifest_hash,
                report["source"]["sha256"], report["source"]["path"],
                label or Path(report["source"]["path"]).stem, report["audio_stream_index"], key))
            for cue in report["cues"]:
                summary = cue["measurements"]
                db.execute("INSERT INTO cues VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                    manifest_hash, cue["cue_id"], cue["start_s"], cue["end_s"], cue["speaker"], cue["text"],
                    normalized(cue["text"]), cue["alignment"]["status"], summary["f0_median_hz"],
                    summary["rms_dbfs_energy_mean"], cue["kind"], json.dumps(cue, ensure_ascii=False, allow_nan=False)))
    finally:
        db.close()
    return {"database": str(database), "indexed_cues": len(report["cues"]), "run_id": manifest_hash,
            "replaced_identical_evidence": bool(old), "perceptual_review": "NOT_PERFORMED"}


def search(database, query="", *, source=None, speaker=None, status=None, min_pitch=None,
           min_rms=None, limit=25, reuse_database=None, reuse_revision=None):
    if not 1 <= limit <= 1000:
        raise AVError("Search limit must be between 1 and 1000")
    terms, params = [], []
    # instr provides literal Japanese substring matching; no tokenizer/SQL wildcard surprises.
    if query:
        terms.append("instr(c.search_text, ?) > 0")
        params.append(normalized(query))
    if source:
        terms.append("(instr(r.label, ?) > 0 OR r.source_sha256=?)")
        params.extend([source, source])
    if speaker:
        terms.append("c.speaker=?")
        params.append(speaker)
    if status:
        terms.append("c.status=?")
        params.append(status)
    for value, field in ((min_pitch, "pitch_hz"), (min_rms, "rms_dbfs")):
        if value is not None:
            from .common import finite
            terms.append(f"c.{field}>=?")
            params.append(finite(value))
    db = connect(database, readonly=True)
    try:
        fts = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='cue_fts'").fetchone())
        if fts and len(normalized(query)) >= 3:
            terms.append("c.rowid IN (SELECT rowid FROM cue_fts WHERE cue_fts MATCH ?)")
            params.append('"' + normalized(query).replace('"', '""') + '"')
        rows = db.execute("""SELECT r.label, r.path AS run_path, r.manifest_sha256, r.source_sha256,
                    r.source_path, r.audio_stream, c.cue_id, c.start_s, c.end_s, c.speaker,
                    c.text, c.status, c.pitch_hz, c.rms_dbfs, c.kind
                    FROM cues c JOIN runs r ON r.run_id=c.run_id """
                    + ("WHERE " + " AND ".join(terms) if terms else "")
                    + " ORDER BY r.label, c.start_s LIMIT ?", params + [limit]).fetchall()
        result = []
        for row in rows:
            record = dict(row)
            manifest = Path(row["run_path"]) / "run.json"
            record["run_manifest_status"] = ("MATCH" if manifest.is_file() and sha256(manifest) == row["manifest_sha256"]
                                             else "MISSING_OR_CHANGED")
            record["evidence_class"] = "COMPUTED_AND_TEXT_CANDIDATES_NOT_LISTENING"
            result.append(record)
        if reuse_database is not None:
            from .reuse.lookup import attach
            attach(result, reuse_database, reuse_revision)
        return {"matches": result, "count": len(result), "limit": limit,
                "matching": "NFKC casefolded literal substring; supports Japanese short queries",
                "trigram_acceleration": fts and len(normalized(query)) >= 3,
                "rms_summary": "10 log10(mean window power); mixed audio, not isolated vocal intensity",
                "verification": "Manifest identity checked; run ave verify before detailed evidence reuse"}
    finally:
        db.close()
