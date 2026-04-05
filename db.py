import sqlite3
from datetime import datetime

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
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def upsert_node(self, node_id, name):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT name FROM nodes WHERE id = ?", (node_id,))
            row = c.fetchone()

            if row is None:
                c.execute(
                    """
                    INSERT INTO nodes (id, name, first_seen, last_seen)
                    VALUES (?, ?, ?, ?)
                """,
                    (node_id, name, datetime.utcnow(), datetime.utcnow()),
                )
                logger.info(f"New node seen: {name} ({node_id})")
            else:
                old_name = row[0]
                new_name = name if name else old_name

                if new_name != old_name:
                    c.execute(
                        """
                        UPDATE nodes
                        SET name = ?, last_seen = ?
                        WHERE id = ?
                    """,
                        (new_name, datetime.utcnow(), node_id),
                    )
                    logger.info(f"Updated node: {new_name} ({node_id})")
                else:
                    c.execute(
                        """
                        UPDATE nodes
                        SET last_seen = ?
                        WHERE id = ?
                    """,
                        (datetime.utcnow(), node_id),
                    )

            conn.commit()
        finally:
            conn.close()

    def get_seen_nodes(self):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT id, name FROM nodes ORDER BY last_seen DESC")
            rows = c.fetchall()
        finally:
            conn.close()

        return [{"id": row[0], "name": row[1]} for row in rows]
