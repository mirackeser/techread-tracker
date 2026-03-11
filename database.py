import os
import sqlite3
from werkzeug.security import generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL")

# ─── CONNECTION ──────────────────────────────────────────────────────────────

def _is_postgres():
    return DATABASE_URL is not None

def get_connection():
    if _is_postgres():
        import psycopg2
        import psycopg2.extras
        # Render bazen postgres:// verir, psycopg2 postgresql:// ister
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        conn.autocommit = False
        return conn
    else:
        conn = sqlite3.connect("tracker.db")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

def _execute(conn, sql, params=None):
    """Veritabanı motoruna göre placeholder dönüştürür ve çalıştırır."""
    if _is_postgres():
        import psycopg2.extras
        sql = sql.replace("?", "%s")
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        return cur
    else:
        return conn.execute(sql, params or ())

def _fetchone(conn, sql, params=None):
    cur = _execute(conn, sql, params)
    row = cur.fetchone()
    if _is_postgres():
        cur.close()
    return row

def _fetchall(conn, sql, params=None):
    cur = _execute(conn, sql, params)
    rows = cur.fetchall()
    if _is_postgres():
        cur.close()
    return rows

def _exec(conn, sql, params=None):
    cur = _execute(conn, sql, params)
    if _is_postgres():
        cur.close()

def commit(conn):
    conn.commit()

def close(conn):
    conn.close()

# ─── INIT ────────────────────────────────────────────────────────────────────

def init_db():
    conn = get_connection()

    if _is_postgres():
        import psycopg2.extras
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                student_no TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'student',
                base_score REAL DEFAULT 100.0
            );

            CREATE TABLE IF NOT EXISTS reading_sessions (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES users(id),
                duration_minutes REAL DEFAULT 0,
                date TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_summaries (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                total_minutes REAL DEFAULT 0,
                met_requirement INTEGER DEFAULT 0,
                penalty_applied REAL DEFAULT 0,
                UNIQUE(student_id, date)
            );

            CREATE TABLE IF NOT EXISTS news_entries (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES users(id),
                date TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS active_sessions (
                id SERIAL PRIMARY KEY,
                student_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
                start_time TEXT NOT NULL
            );
        """)
        cur.close()
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_no TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'student',
                base_score REAL DEFAULT 100.0
            );

            CREATE TABLE IF NOT EXISTS reading_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                duration_minutes REAL DEFAULT 0,
                date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (student_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS daily_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                total_minutes REAL DEFAULT 0,
                met_requirement INTEGER DEFAULT 0,
                penalty_applied REAL DEFAULT 0,
                UNIQUE(student_id, date)
            );

            CREATE TABLE IF NOT EXISTS news_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (student_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS active_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER UNIQUE NOT NULL,
                start_time TEXT NOT NULL,
                FOREIGN KEY (student_id) REFERENCES users(id)
            );
        """)

    # Varsayılan hoca hesabı — admin / Admin@2508.
    existing = _fetchone(conn, "SELECT id FROM users WHERE student_no = ?", ("admin",))
    if not existing:
        default_password = os.environ.get("TEACHER_DEFAULT_PASSWORD", "Admin@2508.")
        hashed = generate_password_hash(default_password, method="pbkdf2:sha256", salt_length=16)
        _exec(conn,
            "INSERT INTO users (student_no, name, password_hash, role) VALUES (?, ?, ?, ?)",
            ("admin", "Hoca", hashed, "teacher")
        )

    conn.commit()
    conn.close()
