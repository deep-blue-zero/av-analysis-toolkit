"""Small local search index. Retrieval is deliberately separate from verification."""
from __future__ import annotations

import sqlite3

import numpy as np
from scipy.signal import stft


RATE = 16000


def descriptors(x, rate=RATE, window_s=.4, hop_s=.2):
    """Frequency-shape fingerprints of overlapping windows, each channel intact."""
    n = min(len(x), max(256, round(window_s * rate)))
    if n < 256:
        return []
    starts = sorted(set(list(range(0, len(x)-n+1, max(1, round(hop_s*rate)))) + [len(x)-n]))
    bands = np.geomspace(80, min(7600, rate*.48), 25)
    out = []
    for channel in range(x.shape[1]):
        for start in starts:
            y = x[start:start+n, channel].astype(float)
            y -= y.mean()
            if np.mean(y*y) < 1e-12:
                continue
            f, _, z = stft(y, fs=rate, nperseg=min(512, n), noverlap=min(384, n-1), boundary=None)
            power = np.mean(np.abs(z)**2, axis=1)
            v = np.array([np.sum(power[(f >= a) & (f < b)]) for a, b in zip(bands, bands[1:])])
            v = np.log1p(v / (np.mean(v)*.03 + 1e-18))
            v -= v.mean()
            norm = np.linalg.norm(v)
            if norm < 1e-8:
                continue
            v /= norm
            out.append((start/rate, channel, v.astype(np.float32)))
    return out


_PLANES = np.random.default_rng(318421).normal(size=(96, 24))


def keys(vector):
    bits = (_PLANES @ vector) > 0
    # Eight small bands allow nearby candidates to share a bucket. The detailed
    # comparison, never a bucket collision, establishes correspondence.
    return [f"{i}:" + str(sum(int(b) << j for j, b in enumerate(bits[i*12:i*12+12]))) for i in range(8)]


class Index:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS fingerprint_meta (version INTEGER);
            INSERT INTO fingerprint_meta SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM fingerprint_meta);
            CREATE TABLE IF NOT EXISTS indexed (id TEXT PRIMARY KEY, key TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS vectors (id TEXT, offset REAL, channel INTEGER, vector BLOB);
            CREATE TABLE IF NOT EXISTS buckets (bucket TEXT, vector_row INTEGER);
            CREATE INDEX IF NOT EXISTS bucket_lookup ON buckets(bucket);
            CREATE INDEX IF NOT EXISTS vector_owner ON vectors(id);
        """)
        if self.db.execute("SELECT version FROM fingerprint_meta").fetchone()[0] != 1:
            raise ValueError("Unsupported fingerprint index schema")

    def add(self, identity, desc, key):
        old = self.db.execute("SELECT key FROM indexed WHERE id=?", (identity,)).fetchone()
        if old and old[0] == key:
            return True
        with self.db:
            self.db.execute("DELETE FROM buckets WHERE vector_row IN (SELECT rowid FROM vectors WHERE id=?)", (identity,))
            self.db.execute("DELETE FROM vectors WHERE id=?", (identity,))
            for offset, channel, vector in desc:
                cursor = self.db.execute("INSERT INTO vectors VALUES (?,?,?,?)", (identity, offset, channel, vector.tobytes()))
                self.db.executemany("INSERT INTO buckets VALUES (?,?)", [(k, cursor.lastrowid) for k in keys(vector)])
            self.db.execute("INSERT OR REPLACE INTO indexed VALUES (?,?)", (identity, key))
        return False

    def candidates(self, desc, allowed, limit=20):
        scores = {}
        query_buckets = {}
        for _, _, vector in desc:
            for k in keys(vector):
                query_buckets.setdefault(k, []).append(vector)
        for k, vectors in query_buckets.items():
            cursor = self.db.execute("SELECT v.id, v.vector FROM buckets b JOIN vectors v ON v.rowid=b.vector_row WHERE b.bucket=?", (k,))
            while True:
                rows = cursor.fetchmany(4096)
                if not rows: break
                rows = [(identity, raw) for identity, raw in rows if identity in allowed]
                if not rows: continue
                targets = np.array([np.frombuffer(raw, dtype=np.float32) for _,raw in rows])
                best = np.full(len(rows), -1., dtype=np.float32)
                for start in range(0,len(vectors),256):
                    values = targets @ np.array(vectors[start:start+256]).T
                    best = np.maximum(best, np.max(values, axis=1))
                for (identity,_), score in zip(rows,best):
                    if score >= .72: scores[identity] = max(float(score), scores.get(identity,-1))
        ordered = sorted(scores, key=lambda identity: (-scores[identity], identity))
        return ordered[:limit], len(ordered) > limit

    def close(self):
        self.db.close()
