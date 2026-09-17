# SPDX-License-Identifier: GPL-2.0
# pylint: disable=missing-module-docstring,missing-function-docstring
# pylint: disable=missing-class-docstring

import difflib
import sqlite3


# Authors routinely reword the subject between versions, so the previous
# version is matched on title similarity rather than equality. Requiring
# equality missed ~19% of the resubmissions in the netdev archive; 0.85
# recovers most of those, below it the pairs are mostly unrelated.
#
# There is no clean separation at the top of the range: sibling patches of
# a treewide series differ by just a driver name and score much like a
# reworded title ("NFC: nfcmrvl: Replace strcpy() with strscpy()" vs
# "NFC: s3fwrn5: ..." is 0.867), so some of those get matched. That's
# tolerable - the caller only looks for a previous version once the message
# is already known to be a vN posted as a reply, so matching the wrong
# predecessor does not make the warning itself wrong.
TITLE_MATCH_RATIO = 0.85


SCHEMA = """
CREATE TABLE IF NOT EXISTS identity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    known_dev   INTEGER DEFAULT 0,
    welcomed    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS identity_email (
    email       TEXT PRIMARY KEY,
    identity_id INTEGER NOT NULL REFERENCES identity(id)
);

CREATE TABLE IF NOT EXISTS identity_name (
    name        TEXT NOT NULL,
    identity_id INTEGER NOT NULL REFERENCES identity(id),
    UNIQUE(name, identity_id)
);

CREATE TABLE IF NOT EXISTS submission (
    message_id  TEXT PRIMARY KEY,
    identity_id INTEGER NOT NULL REFERENCES identity(id),
    title       TEXT NOT NULL,
    version     INTEGER,
    timestamp   TEXT NOT NULL,
    warned      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pv_bot_action (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT NOT NULL,
    identity_id INTEGER NOT NULL REFERENCES identity(id),
    tag         TEXT NOT NULL,
    timestamp   TEXT NOT NULL
);
"""


class AgentDB:
    def __init__(self, db_path=":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def resolve_identity(self, name, email):
        cur = self.conn.cursor()

        # 1. Look up by email
        cur.execute("SELECT identity_id FROM identity_email WHERE email = ?",
                     (email,))
        row = cur.fetchone()
        if row:
            identity_id = row[0]
            if name:
                cur.execute(
                    "INSERT OR IGNORE INTO identity_name (name, identity_id) "
                    "VALUES (?, ?)", (name, identity_id))
                self.conn.commit()
            return identity_id

        # 2. Look up by name
        if name:
            cur.execute(
                "SELECT identity_id FROM identity_name WHERE name = ?",
                (name,))
            row = cur.fetchone()
            if row:
                identity_id = row[0]
                cur.execute(
                    "INSERT INTO identity_email (email, identity_id) "
                    "VALUES (?, ?)", (email, identity_id))
                self.conn.commit()
                return identity_id

        # 3. Create new identity
        cur.execute("INSERT INTO identity DEFAULT VALUES")
        identity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO identity_email (email, identity_id) VALUES (?, ?)",
            (email, identity_id))
        if name:
            cur.execute(
                "INSERT INTO identity_name (name, identity_id) VALUES (?, ?)",
                (name, identity_id))
        self.conn.commit()
        return identity_id

    def get_identity(self, identity_id):
        cur = self.conn.cursor()
        cur.execute("SELECT known_dev, welcomed FROM identity WHERE id = ?",
                     (identity_id,))
        return cur.fetchone()

    def set_known_dev(self, identity_id, status):
        self.conn.execute(
            "UPDATE identity SET known_dev = ? WHERE id = ?",
            (status, identity_id))
        self.conn.commit()

    def set_welcomed(self, identity_id):
        self.conn.execute(
            "UPDATE identity SET welcomed = 1 WHERE id = ?",
            (identity_id,))
        self.conn.commit()

    def get_identity_emails(self, identity_id):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT email FROM identity_email WHERE identity_id = ?",
            (identity_id,))
        return [r[0] for r in cur.fetchall()]

    def get_identity_names(self, identity_id):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT name FROM identity_name WHERE identity_id = ?",
            (identity_id,))
        return [r[0] for r in cur.fetchall()]

    def add_submission(self, message_id, identity_id, title, version,
                       timestamp):
        self.conn.execute(
            "INSERT OR IGNORE INTO submission "
            "(message_id, identity_id, title, version, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, identity_id, title, version, timestamp))
        self.conn.commit()

    def find_recent_duplicate(self, identity_id, title, before_timestamp,
                              hours=24):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT message_id, version, timestamp FROM submission "
            "WHERE identity_id = ? AND title = ? "
            "AND datetime(timestamp) > datetime(?, '-' || ? || ' hours') "
            "AND timestamp < ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (identity_id, title, before_timestamp, hours, before_timestamp))
        return cur.fetchone()

    def find_previous_version(self, identity_id, title, version):
        cur = self.conn.cursor()
        prev_versions = []
        if version is not None and version >= 1:
            prev_versions.append(version - 1)
        if version is not None and version >= 2:
            prev_versions.append(version - 2)
        if not prev_versions:
            return None

        placeholders = ",".join("?" * len(prev_versions))
        cur.execute(
            f"SELECT message_id, version, timestamp, title FROM submission "
            f"WHERE identity_id = ? "
            f"AND COALESCE(version, 0) IN ({placeholders}) "
            f"ORDER BY timestamp DESC",
            (identity_id,) + tuple(prev_versions))

        # Rows come newest first, so a strict > keeps the most recent of
        # equally good matches.
        best = None
        best_ratio = 0.0
        for row in cur.fetchall():
            ratio = difflib.SequenceMatcher(None, title, row[3]).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = row
        if best_ratio < TITLE_MATCH_RATIO:
            return None
        return best[:3]

    def set_submission_warned(self, message_id, flag):
        self.conn.execute(
            "UPDATE submission SET warned = warned | ? WHERE message_id = ?",
            (flag, message_id))
        self.conn.commit()

    def add_pv_bot_action(self, message_id, identity_id, tag, timestamp):
        self.conn.execute(
            "INSERT INTO pv_bot_action "
            "(message_id, identity_id, tag, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (message_id, identity_id, tag, timestamp))
        self.conn.commit()

    def get_pv_bot_actions(self, identity_id=None):
        cur = self.conn.cursor()
        if identity_id is not None:
            cur.execute(
                "SELECT message_id, identity_id, tag, timestamp "
                "FROM pv_bot_action WHERE identity_id = ? "
                "ORDER BY timestamp", (identity_id,))
        else:
            cur.execute(
                "SELECT message_id, identity_id, tag, timestamp "
                "FROM pv_bot_action ORDER BY timestamp")
        return cur.fetchall()
