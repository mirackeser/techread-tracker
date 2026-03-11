import os
import re
import csv
import io
from flask import Flask, request, jsonify, session, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from database import init_db, get_connection, _fetchone, _fetchall, _exec, commit, close
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")

def _today():
    """Türkiye saatiyle bugünün tarihi."""
    return datetime.now(TZ).date()

def _now_iso():
    """Türkiye saatiyle şu anki zaman (ISO format)."""
    return datetime.now(TZ).isoformat()

# ─── ENV & CONFIG ─────────────────────────────────────────────────────────────

load_dotenv()

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY")

if not app.secret_key or len(app.secret_key) < 32:
    raise RuntimeError(
        "SECRET_KEY eksik veya çok kısa! .env dosyasında en az 32 karakterlik bir SECRET_KEY tanımlayın."
    )

IS_PRODUCTION = os.environ.get("FLASK_ENV", "production") == "production"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax" if not IS_PRODUCTION else "Strict",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,   # HTTPS zorunlu (production)
)

DAILY_REQUIREMENT_MINUTES = 120  # 2 saat
PENALTY_POINTS = 5

# ─── VALIDATION HELPERS ──────────────────────────────────────────────────────

_STUDENT_NO_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,30}$")
_NAME_RE = re.compile(r"^[\w\sçÇğĞıİöÖşŞüÜ.\-]{2,60}$", re.UNICODE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def _validate_student_no(val):
    if not val or not _STUDENT_NO_RE.match(val):
        return None, "Geçersiz öğrenci numarası (1-30 karakter, alfanümerik)"
    return val, None

def _validate_name(val):
    if not val or not _NAME_RE.match(val):
        return None, "Geçersiz isim (2-60 karakter)"
    return val, None

def _validate_password(val):
    if not val or len(val) < 6:
        return None, "Şifre en az 6 karakter olmalı"
    if len(val) > 128:
        return None, "Şifre en fazla 128 karakter olabilir"
    return val, None

def _validate_date(val):
    if not val:
        return str(_today()), None
    if not _DATE_RE.match(val):
        return None, "Geçersiz tarih formatı (YYYY-MM-DD)"
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return val, None
    except ValueError:
        return None, "Geçersiz tarih"

def _validate_duration(val):
    try:
        d = float(val)
    except (TypeError, ValueError):
        return None, "Geçerli bir süre girin (sayı)"
    if d <= 0:
        return None, "Süre 0'dan büyük olmalı"
    if d > 1440:
        return None, "Süre 1440 dakikayı (24 saat) aşamaz"
    return d, None

def _validate_title(val):
    if not val or len(val.strip()) < 2:
        return None, "Haber başlığı en az 2 karakter olmalı"
    if len(val) > 200:
        return None, "Haber başlığı en fazla 200 karakter olabilir"
    return val.strip(), None

def _validate_summary(val):
    if val and len(val) > 2000:
        return None, "Özet en fazla 2000 karakter olabilir"
    return (val or "").strip(), None

def _require_login():
    """Session kontrolü. Giriş yapılmamışsa (user_id, error_response) döner."""
    if "user_id" not in session:
        return None, (jsonify({"error": "Giriş yapılmadı"}), 401)
    return session["user_id"], None

def _require_teacher():
    """Hoca rolü kontrolü."""
    if session.get("role") != "teacher":
        return jsonify({"error": "Yetki yok"}), 403
    return None


# ─── AUTH ─────────────────────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400

    student_no = (data.get("student_no") or "").strip()
    password = (data.get("password") or "").strip()

    student_no, err = _validate_student_no(student_no)
    if err:
        return jsonify({"error": err}), 400

    if not password:
        return jsonify({"error": "Şifre boş olamaz"}), 400

    conn = get_connection()
    user = _fetchone(conn, "SELECT * FROM users WHERE student_no = ?", (student_no,))
    close(conn)

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Hatalı numara veya şifre"}), 401

    session.clear()
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["name"] = user["name"]

    return jsonify({"role": user["role"], "name": user["name"]})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Çıkış yapıldı"})


@app.route("/api/me", methods=["GET"])
def me():
    user_id, err = _require_login()
    if err:
        return err
    return jsonify({"user_id": user_id, "role": session["role"], "name": session["name"]})


# ─── KAYIT ────────────────────────────────────────────────────────────────────

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400

    name = (data.get("name") or "").strip()
    student_no = (data.get("student_no") or "").strip()
    password = (data.get("password") or "").strip()

    name, err = _validate_name(name)
    if err:
        return jsonify({"error": err}), 400

    student_no, err = _validate_student_no(student_no)
    if err:
        return jsonify({"error": err}), 400

    password, err = _validate_password(password)
    if err:
        return jsonify({"error": err}), 400

    password_hash = generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)

    conn = get_connection()
    try:
        _exec(conn,
            "INSERT INTO users (student_no, name, password_hash, role) VALUES (?, ?, ?, 'student')",
            (student_no, name, password_hash)
        )
        commit(conn)
        return jsonify({"message": "Kayıt başarılı"}), 201
    except Exception:
        conn.rollback()
        return jsonify({"error": "Bu öğrenci numarası zaten kayıtlı"}), 400
    finally:
        close(conn)


# ─── OKUMA KAYDI (MANUEL) ────────────────────────────────────────────────────

@app.route("/api/sessions/add", methods=["POST"])
def add_session():
    user_id, err = _require_login()
    if err:
        return err

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400

    duration, err = _validate_duration(data.get("duration_minutes"))
    if err:
        return jsonify({"error": err}), 400

    target_date, err = _validate_date(data.get("date"))
    if err:
        return jsonify({"error": err}), 400

    now = _now_iso()
    conn = get_connection()

    _exec(conn,
        "INSERT INTO reading_sessions (student_id, duration_minutes, date, created_at) VALUES (?, ?, ?, ?)",
        (user_id, duration, target_date, now)
    )

    _update_daily_summary(conn, user_id, target_date)
    commit(conn)
    close(conn)

    return jsonify({"message": "Okuma kaydı eklendi", "duration_minutes": duration})


# ─── HABER GİRİŞİ ────────────────────────────────────────────────────────────

@app.route("/api/news/add", methods=["POST"])
def add_news():
    user_id, err = _require_login()
    if err:
        return err

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400

    title, err = _validate_title(data.get("title"))
    if err:
        return jsonify({"error": err}), 400

    summary, err = _validate_summary(data.get("summary"))
    if err:
        return jsonify({"error": err}), 400

    target_date, err = _validate_date(data.get("date"))
    if err:
        return jsonify({"error": err}), 400

    now = _now_iso()
    conn = get_connection()

    _exec(conn,
        "INSERT INTO news_entries (student_id, date, title, summary, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, target_date, title, summary, now)
    )
    commit(conn)
    close(conn)

    return jsonify({"message": "Haber kaydedildi"})


@app.route("/api/news/my", methods=["GET"])
def my_news():
    user_id, err = _require_login()
    if err:
        return err

    conn = get_connection()
    news = _fetchall(conn,
        "SELECT * FROM news_entries WHERE student_id = ? ORDER BY created_at DESC LIMIT 50",
        (user_id,)
    )
    close(conn)

    return jsonify([dict(n) for n in news])


# ─── ÖĞRENCİ VERİSİ ──────────────────────────────────────────────────────────

@app.route("/api/my-stats", methods=["GET"])
def my_stats():
    user_id, err = _require_login()
    if err:
        return err

    conn = get_connection()

    summaries = _fetchall(conn,
        "SELECT * FROM daily_summaries WHERE student_id = ? ORDER BY date DESC LIMIT 30",
        (user_id,)
    )

    today = str(_today())
    today_summary = _fetchone(conn,
        "SELECT * FROM daily_summaries WHERE student_id = ? AND date = ?",
        (user_id, today)
    )

    total_penalty = _fetchone(conn,
        "SELECT COALESCE(SUM(penalty_applied), 0) AS total FROM daily_summaries WHERE student_id = ?",
        (user_id,)
    )["total"]

    user = _fetchone(conn, "SELECT base_score FROM users WHERE id = ?", (user_id,))
    close(conn)

    final_score = user["base_score"] - total_penalty

    return jsonify({
        "final_score": round(final_score, 1),
        "total_penalty": total_penalty,
        "today": dict(today_summary) if today_summary else None,
        "history": [dict(s) for s in summaries]
    })


# ─── HOCA PANELİ ─────────────────────────────────────────────────────────────

@app.route("/api/teacher/report", methods=["GET"])
def teacher_report():
    err = _require_teacher()
    if err:
        return err

    raw_date = request.args.get("date", str(_today()))
    target_date, err = _validate_date(raw_date)
    if err:
        return jsonify({"error": err}), 400

    conn = get_connection()

    rows = _fetchall(conn, """
        SELECT 
            u.id,
            u.name,
            u.student_no,
            u.base_score,
            COALESCE(ds.total_minutes, 0) AS total_minutes,
            COALESCE(ds.met_requirement, 0) AS met_requirement,
            COALESCE(ds.penalty_applied, 0) AS penalty_applied,
            (u.base_score - COALESCE((
                SELECT SUM(penalty_applied) FROM daily_summaries WHERE student_id = u.id
            ), 0)) AS final_score,
            COALESCE((
                SELECT COUNT(*) FROM news_entries WHERE student_id = u.id AND date = ?
            ), 0) AS news_count
        FROM users u
        LEFT JOIN daily_summaries ds ON u.id = ds.student_id AND ds.date = ?
        WHERE u.role = 'student'
        ORDER BY final_score DESC
    """, (target_date, target_date))

    close(conn)
    return jsonify({
        "date": target_date,
        "students": [dict(r) for r in rows]
    })


@app.route("/api/teacher/students", methods=["GET"])
def all_students():
    err = _require_teacher()
    if err:
        return err

    conn = get_connection()
    students = _fetchall(conn,
        "SELECT id, name, student_no, base_score FROM users WHERE role = 'student'"
    )
    close(conn)
    return jsonify([dict(s) for s in students])


@app.route("/api/teacher/student-news", methods=["GET"])
def student_news():
    err = _require_teacher()
    if err:
        return err

    student_id = request.args.get("student_id")
    if not student_id or not student_id.isdigit():
        return jsonify({"error": "Geçersiz öğrenci ID"}), 400

    raw_date = request.args.get("date", str(_today()))
    target_date, err = _validate_date(raw_date)
    if err:
        return jsonify({"error": err}), 400

    conn = get_connection()
    news = _fetchall(conn,
        "SELECT * FROM news_entries WHERE student_id = ? AND date = ? ORDER BY created_at DESC",
        (int(student_id), target_date)
    )
    close(conn)

    return jsonify([dict(n) for n in news])


# ─── HAFTALIK RAPOR (HOCA) ────────────────────────────────────────────────────

@app.route("/api/teacher/weekly-report", methods=["GET"])
def teacher_weekly_report():
    err = _require_teacher()
    if err:
        return err

    raw_date = request.args.get("date", str(_today()))
    target_date, err = _validate_date(raw_date)
    if err:
        return jsonify({"error": err}), 400

    # Haftanın pazartesi-pazar aralığını bul
    d = datetime.strptime(target_date, "%Y-%m-%d").date()
    week_start = d - timedelta(days=d.weekday())  # Pazartesi
    week_end = week_start + timedelta(days=6)      # Pazar

    conn = get_connection()

    rows = _fetchall(conn, """
        SELECT 
            u.id,
            u.name,
            u.student_no,
            u.base_score,
            COALESCE((
                SELECT SUM(ds.total_minutes)
                FROM daily_summaries ds
                WHERE ds.student_id = u.id AND ds.date BETWEEN ? AND ?
            ), 0) AS week_total_minutes,
            COALESCE((
                SELECT COUNT(*)
                FROM daily_summaries ds
                WHERE ds.student_id = u.id AND ds.date BETWEEN ? AND ? AND ds.met_requirement = 1
            ), 0) AS days_met,
            COALESCE((
                SELECT SUM(ds.penalty_applied)
                FROM daily_summaries ds
                WHERE ds.student_id = u.id AND ds.date BETWEEN ? AND ?
            ), 0) AS week_penalty,
            COALESCE((
                SELECT COUNT(*)
                FROM news_entries ne
                WHERE ne.student_id = u.id AND ne.date BETWEEN ? AND ?
            ), 0) AS week_news_count,
            (u.base_score - COALESCE((
                SELECT SUM(penalty_applied) FROM daily_summaries WHERE student_id = u.id
            ), 0)) AS final_score
        FROM users u
        WHERE u.role = 'student'
        ORDER BY final_score DESC
    """, (str(week_start), str(week_end),
          str(week_start), str(week_end),
          str(week_start), str(week_end),
          str(week_start), str(week_end)))

    close(conn)
    return jsonify({
        "week_start": str(week_start),
        "week_end": str(week_end),
        "students": [dict(r) for r in rows]
    })


# ─── TOPLAM PUAN SIRALAMASI ──────────────────────────────────────────────────

@app.route("/api/teacher/leaderboard", methods=["GET"])
def teacher_leaderboard():
    err = _require_teacher()
    if err:
        return err

    conn = get_connection()

    rows = _fetchall(conn, """
        SELECT 
            u.id,
            u.name,
            u.student_no,
            u.base_score,
            COALESCE((
                SELECT SUM(ds.total_minutes)
                FROM daily_summaries ds
                WHERE ds.student_id = u.id
            ), 0) AS all_total_minutes,
            COALESCE((
                SELECT COUNT(*)
                FROM daily_summaries ds
                WHERE ds.student_id = u.id AND ds.met_requirement = 1
            ), 0) AS total_days_met,
            COALESCE((
                SELECT COUNT(*)
                FROM daily_summaries ds
                WHERE ds.student_id = u.id
            ), 0) AS total_days_tracked,
            COALESCE((
                SELECT SUM(ds.penalty_applied)
                FROM daily_summaries ds
                WHERE ds.student_id = u.id
            ), 0) AS total_penalty,
            COALESCE((
                SELECT COUNT(*)
                FROM news_entries ne
                WHERE ne.student_id = u.id
            ), 0) AS total_news_count,
            (u.base_score - COALESCE((
                SELECT SUM(penalty_applied) FROM daily_summaries WHERE student_id = u.id
            ), 0)) AS final_score
        FROM users u
        WHERE u.role = 'student'
        ORDER BY final_score DESC
    """)

    close(conn)
    return jsonify({
        "students": [dict(r) for r in rows]
    })


# ─── ÖĞRENCİ HAFTALIK İSTATİSTİK ────────────────────────────────────────────

@app.route("/api/my-weekly-stats", methods=["GET"])
def my_weekly_stats():
    user_id, err = _require_login()
    if err:
        return err

    today = _today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    conn = get_connection()

    week_minutes = _fetchone(conn, """
        SELECT COALESCE(SUM(total_minutes), 0) AS total
        FROM daily_summaries
        WHERE student_id = ? AND date BETWEEN ? AND ?
    """, (user_id, str(week_start), str(week_end)))["total"]

    days_met = _fetchone(conn, """
        SELECT COUNT(*) AS cnt
        FROM daily_summaries
        WHERE student_id = ? AND date BETWEEN ? AND ? AND met_requirement = 1
    """, (user_id, str(week_start), str(week_end)))["cnt"]

    week_penalty = _fetchone(conn, """
        SELECT COALESCE(SUM(penalty_applied), 0) AS total
        FROM daily_summaries
        WHERE student_id = ? AND date BETWEEN ? AND ?
    """, (user_id, str(week_start), str(week_end)))["total"]

    week_news = _fetchone(conn, """
        SELECT COUNT(*) AS cnt
        FROM news_entries
        WHERE student_id = ? AND date BETWEEN ? AND ?
    """, (user_id, str(week_start), str(week_end)))["cnt"]

    close(conn)

    # Haftada 7 gün, günde 120dk = 840dk hedef
    week_target = 7 * 120

    return jsonify({
        "week_start": str(week_start),
        "week_end": str(week_end),
        "week_total_minutes": round(week_minutes, 1),
        "week_target_minutes": week_target,
        "days_met": days_met,
        "week_penalty": week_penalty,
        "week_news_count": week_news
    })


# ─── YARDIMCI ────────────────────────────────────────────────────────────────

def _update_daily_summary(conn, student_id, target_date):
    total = _fetchone(conn, """
        SELECT COALESCE(SUM(duration_minutes), 0) AS total
        FROM reading_sessions
        WHERE student_id = ? AND date = ?
    """, (student_id, target_date))["total"]

    met = 1 if total >= DAILY_REQUIREMENT_MINUTES else 0
    penalty = 0.0 if met else float(PENALTY_POINTS)

    _exec(conn, """
        INSERT INTO daily_summaries (student_id, date, total_minutes, met_requirement, penalty_applied)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(student_id, date) DO UPDATE SET
            total_minutes = excluded.total_minutes,
            met_requirement = excluded.met_requirement,
            penalty_applied = excluded.penalty_applied
    """, (student_id, target_date, total, met, penalty))


# ─── HOCA: ÖĞRENCİ YÖNETİMİ ─────────────────────────────────────────────────

@app.route("/api/teacher/delete-student", methods=["POST"])
def delete_student():
    err = _require_teacher()
    if err:
        return err

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400

    student_id = data.get("student_id")
    if not student_id:
        return jsonify({"error": "Öğrenci ID gerekli"}), 400

    conn = get_connection()

    user = _fetchone(conn, "SELECT role FROM users WHERE id = ?", (student_id,))
    if not user:
        close(conn)
        return jsonify({"error": "Öğrenci bulunamadı"}), 404
    if user["role"] == "teacher":
        close(conn)
        return jsonify({"error": "Hoca hesabı silinemez"}), 403

    _exec(conn, "DELETE FROM news_entries WHERE student_id = ?", (student_id,))
    _exec(conn, "DELETE FROM reading_sessions WHERE student_id = ?", (student_id,))
    _exec(conn, "DELETE FROM daily_summaries WHERE student_id = ?", (student_id,))
    _exec(conn, "DELETE FROM active_sessions WHERE student_id = ?", (student_id,))
    _exec(conn, "DELETE FROM users WHERE id = ?", (student_id,))
    commit(conn)
    close(conn)

    return jsonify({"message": "Öğrenci silindi"})


@app.route("/api/teacher/reset-password", methods=["POST"])
def reset_password():
    err = _require_teacher()
    if err:
        return err

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400

    student_id = data.get("student_id")
    new_password = (data.get("new_password") or "").strip()

    if not student_id:
        return jsonify({"error": "Öğrenci ID gerekli"}), 400

    new_password, err = _validate_password(new_password)
    if err:
        return jsonify({"error": err}), 400

    conn = get_connection()
    user = _fetchone(conn, "SELECT id FROM users WHERE id = ? AND role = 'student'", (student_id,))
    if not user:
        close(conn)
        return jsonify({"error": "Öğrenci bulunamadı"}), 404

    hashed = generate_password_hash(new_password, method="pbkdf2:sha256", salt_length=16)
    _exec(conn, "UPDATE users SET password_hash = ? WHERE id = ?", (hashed, student_id))
    commit(conn)
    close(conn)

    return jsonify({"message": "Şifre sıfırlandı"})


@app.route("/api/teacher/update-score", methods=["POST"])
def update_score():
    err = _require_teacher()
    if err:
        return err

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400

    student_id = data.get("student_id")
    try:
        new_score = float(data.get("base_score"))
    except (TypeError, ValueError):
        return jsonify({"error": "Geçerli bir puan girin"}), 400

    if new_score < 0 or new_score > 200:
        return jsonify({"error": "Puan 0-200 arasında olmalı"}), 400

    conn = get_connection()
    user = _fetchone(conn, "SELECT id FROM users WHERE id = ? AND role = 'student'", (student_id,))
    if not user:
        close(conn)
        return jsonify({"error": "Öğrenci bulunamadı"}), 404

    _exec(conn, "UPDATE users SET base_score = ? WHERE id = ?", (new_score, student_id))
    commit(conn)
    close(conn)

    return jsonify({"message": f"Puan {new_score} olarak güncellendi"})


# ─── CSV DIŞA AKTARMA (HOCA) ─────────────────────────────────────────────────

@app.route("/api/teacher/export-csv", methods=["GET"])
def export_csv():
    err = _require_teacher()
    if err:
        return err

    report_type = request.args.get("type", "leaderboard")
    conn = get_connection()

    output = io.StringIO()
    output.write('\ufeff')  # BOM for Excel UTF-8
    writer = csv.writer(output)

    if report_type == "daily":
        raw_date = request.args.get("date", str(_today()))
        target_date, err = _validate_date(raw_date)
        if err:
            close(conn)
            return jsonify({"error": err}), 400

        writer.writerow(["#", "Öğrenci No", "Ad Soyad", "Okuma (dk)", "Hedef", "Ceza", "Haber Sayısı", "Toplam Puan"])

        rows = _fetchall(conn, """
            SELECT u.name, u.student_no,
                COALESCE(ds.total_minutes, 0) AS total_minutes,
                COALESCE(ds.met_requirement, 0) AS met_requirement,
                COALESCE(ds.penalty_applied, 0) AS penalty_applied,
                (u.base_score - COALESCE((SELECT SUM(penalty_applied) FROM daily_summaries WHERE student_id = u.id), 0)) AS final_score,
                COALESCE((SELECT COUNT(*) FROM news_entries WHERE student_id = u.id AND date = ?), 0) AS news_count
            FROM users u
            LEFT JOIN daily_summaries ds ON u.id = ds.student_id AND ds.date = ?
            WHERE u.role = 'student' ORDER BY final_score DESC
        """, (target_date, target_date))

        for i, r in enumerate(rows, 1):
            writer.writerow([i, r["student_no"], r["name"], round(r["total_minutes"], 1),
                "TAMAM" if r["met_requirement"] else "EKSİK", r["penalty_applied"],
                r["news_count"], round(r["final_score"], 1)])

        filename = f"gunluk_rapor_{target_date}.csv"

    elif report_type == "weekly":
        raw_date = request.args.get("date", str(_today()))
        target_date, _ = _validate_date(raw_date)
        d = datetime.strptime(target_date, "%Y-%m-%d").date()
        week_start = d - timedelta(days=d.weekday())
        week_end = week_start + timedelta(days=6)

        writer.writerow(["#", "Öğrenci No", "Ad Soyad", "Haftalık Süre (dk)", "Hedefe Ulaşan Gün", "Haftalık Ceza", "Haber Sayısı", "Toplam Puan"])

        rows = _fetchall(conn, """
            SELECT u.name, u.student_no,
                COALESCE((SELECT SUM(ds.total_minutes) FROM daily_summaries ds WHERE ds.student_id = u.id AND ds.date BETWEEN ? AND ?), 0) AS week_min,
                COALESCE((SELECT COUNT(*) FROM daily_summaries ds WHERE ds.student_id = u.id AND ds.date BETWEEN ? AND ? AND ds.met_requirement = 1), 0) AS days_met,
                COALESCE((SELECT SUM(ds.penalty_applied) FROM daily_summaries ds WHERE ds.student_id = u.id AND ds.date BETWEEN ? AND ?), 0) AS week_penalty,
                COALESCE((SELECT COUNT(*) FROM news_entries ne WHERE ne.student_id = u.id AND ne.date BETWEEN ? AND ?), 0) AS week_news,
                (u.base_score - COALESCE((SELECT SUM(penalty_applied) FROM daily_summaries WHERE student_id = u.id), 0)) AS final_score
            FROM users u WHERE u.role = 'student' ORDER BY final_score DESC
        """, (str(week_start), str(week_end), str(week_start), str(week_end),
              str(week_start), str(week_end), str(week_start), str(week_end)))

        for i, r in enumerate(rows, 1):
            writer.writerow([i, r["student_no"], r["name"], round(r["week_min"], 1),
                f"{r['days_met']}/7", r["week_penalty"], r["week_news"], round(r["final_score"], 1)])

        filename = f"haftalik_rapor_{week_start}_{week_end}.csv"

    else:  # leaderboard
        writer.writerow(["#", "Öğrenci No", "Ad Soyad", "Toplam Okuma (dk)", "Hedefe Ulaşan Gün", "Toplam Gün", "Toplam Haber", "Toplam Ceza", "Toplam Puan"])

        rows = _fetchall(conn, """
            SELECT u.name, u.student_no,
                COALESCE((SELECT SUM(ds.total_minutes) FROM daily_summaries ds WHERE ds.student_id = u.id), 0) AS all_min,
                COALESCE((SELECT COUNT(*) FROM daily_summaries ds WHERE ds.student_id = u.id AND ds.met_requirement = 1), 0) AS days_met,
                COALESCE((SELECT COUNT(*) FROM daily_summaries ds WHERE ds.student_id = u.id), 0) AS days_tracked,
                COALESCE((SELECT COUNT(*) FROM news_entries ne WHERE ne.student_id = u.id), 0) AS news_count,
                COALESCE((SELECT SUM(ds.penalty_applied) FROM daily_summaries ds WHERE ds.student_id = u.id), 0) AS total_penalty,
                (u.base_score - COALESCE((SELECT SUM(penalty_applied) FROM daily_summaries WHERE student_id = u.id), 0)) AS final_score
            FROM users u WHERE u.role = 'student' ORDER BY final_score DESC
        """)

        for i, r in enumerate(rows, 1):
            writer.writerow([i, r["student_no"], r["name"], round(r["all_min"], 1),
                r["days_met"], r["days_tracked"], r["news_count"], r["total_penalty"], round(r["final_score"], 1)])

        filename = "toplam_puan_siralaması.csv"

    close(conn)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ─── ÖĞRENCİ ŞİFRE DEĞİŞTİRME ──────────────────────────────────────────────

@app.route("/api/change-password", methods=["POST"])
def change_password():
    user_id, err = _require_login()
    if err:
        return err

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400

    old_password = (data.get("old_password") or "").strip()
    new_password = (data.get("new_password") or "").strip()

    if not old_password:
        return jsonify({"error": "Mevcut şifre gerekli"}), 400

    new_password, err = _validate_password(new_password)
    if err:
        return jsonify({"error": err}), 400

    conn = get_connection()
    user = _fetchone(conn, "SELECT password_hash FROM users WHERE id = ?", (user_id,))

    if not check_password_hash(user["password_hash"], old_password):
        close(conn)
        return jsonify({"error": "Mevcut şifre hatalı"}), 401

    hashed = generate_password_hash(new_password, method="pbkdf2:sha256", salt_length=16)
    _exec(conn, "UPDATE users SET password_hash = ? WHERE id = ?", (hashed, user_id))
    commit(conn)
    close(conn)

    return jsonify({"message": "Şifre başarıyla değiştirildi"})


# ─── STATIC DOSYALAR ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


if __name__ == "__main__":
    init_db()
    debug_mode = not IS_PRODUCTION
    print(f"⚙️  Debug: {'ON' if debug_mode else 'OFF'} | Env: {os.environ.get('FLASK_ENV', 'production')}")
    app.run(debug=debug_mode, port=5000)
