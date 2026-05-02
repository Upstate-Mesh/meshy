import sqlite3
from datetime import UTC, datetime


def _now():
    return datetime.now(UTC).isoformat()


from loguru import logger


class NodeDB:
    def __init__(self, db_file="nodes.db"):
        self.db_file = db_file
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_file)

    def _init_db(self):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                name TEXT,
                lat REAL,
                lon REAL,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                seen_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def upsert_node(self, node_id, name, lat=None, lon=None):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT name FROM nodes WHERE id = ?", (node_id,))
            row = c.fetchone()

            if row is None:
                c.execute(
                    """
                    INSERT INTO nodes (id, name, lat, lon, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (node_id, name, lat, lon, _now(), _now()),
                )
                logger.info(f"New node seen: {name} ({node_id})")
            else:
                old_name = row[0]
                new_name = name if name else old_name

                if new_name != old_name:
                    logger.info(f"Updated node: {new_name} ({node_id})")

                c.execute(
                    """
                    UPDATE nodes
                    SET name = ?, lat = COALESCE(?, lat), lon = COALESCE(?, lon), last_seen = ?
                    WHERE id = ?
                """,
                    (new_name, lat, lon, _now(), node_id),
                )

            conn.commit()
        finally:
            conn.close()

    def upsert_alert(self, alert_id):
        """Insert alert if not already seen. Returns True if new, False if duplicate."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO alerts (id, seen_at) VALUES (?, ?)",
                (alert_id, _now()),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_seen_nodes(self):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT id, name, lat, lon FROM nodes ORDER BY last_seen DESC")
            rows = c.fetchall()
        finally:
            conn.close()

        return [
            {"id": row[0], "name": row[1], "lat": row[2], "lon": row[3]} for row in rows
        ]
