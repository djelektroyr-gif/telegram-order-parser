# db.py
import sqlite3

DB_NAME = "sent_messages.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sent_messages (
            message_id TEXT PRIMARY KEY,
            chat_id TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def is_message_sent(message_id, chat_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sent_messages WHERE message_id = ? AND chat_id = ?", (message_id, chat_id))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

def mark_message_sent(message_id, chat_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO sent_messages (message_id, chat_id) VALUES (?, ?)", (message_id, chat_id))
    conn.commit()
    conn.close()