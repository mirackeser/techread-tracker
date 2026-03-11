"""
Production WSGI entry point.

Kullanım:
  Linux  → gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app
  Windows → python wsgi.py
"""
import os
from dotenv import load_dotenv

load_dotenv()

from database import init_db
from app import app

init_db()

# ─── Windows'ta Waitress ile production sunucu ─────────────────────────────
if __name__ == "__main__":
    from waitress import serve

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8000))

    print(f"🚀 Production sunucu başlatılıyor: http://{host}:{port}")
    print(f"   WSGI: Waitress | Workers: multi-threaded")
    print(f"   Debug: OFF | FLASK_ENV: {os.environ.get('FLASK_ENV', 'production')}")
    print()

    serve(app, host=host, port=port, threads=4)
