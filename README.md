# ⬡ TechRead Tracker

Teknoloji haber okuma süresi takip sistemi. Öğrenciler web arayüzüne giriş yaparak okuma seanslarını loglar. Hoca tüm sınıfı tek ekrandan izler.

---

## Kurulum

```bash
# Gerekli kütüphaneyi kur
pip install flask

# Uygulamayı başlat
python app.py
```

Tarayıcıda aç: **http://localhost:5000**

---

## Kullanım

### Hoca Girişi
- **Kullanıcı adı:** `admin`
- **Şifre:** `admin123`
- Tarih seçerek tüm öğrencilerin durumunu, puanlarını ve ilerleme çubuklarını görebilir.

### Öğrenci Girişi
- Önce "Kayıt Ol" sekmesiyle hesap oluşturulur.
- Giriş yapıldıktan sonra **"Okumaya Başla"** butonuna basılır, okuma bitince **"Durdur"**.
- Günlük 120 dakikaya ulaşılırsa ✅, ulaşılmazsa −10 puan kesilir.

---

## Kurallar

| Durum | Sonuç |
|-------|-------|
| Günlük ≥ 120 dk okuma | ✅ Tam puan |
| Günlük < 120 dk okuma | ❌ −10 puan |

---

## API Endpoint'leri

| Endpoint | Metod | Açıklama |
|----------|-------|----------|
| `/api/login` | POST | Giriş |
| `/api/register` | POST | Öğrenci kaydı |
| `/api/logout` | POST | Çıkış |
| `/api/sessions/start` | POST | Okuma başlat |
| `/api/sessions/stop` | POST | Okuma durdur |
| `/api/my-stats` | GET | Kendi istatistikleri |
| `/api/teacher/report?date=YYYY-MM-DD` | GET | Sınıf raporu (hoca) |

---

## Proje Yapısı

```
tracker/
├── app.py          → Flask backend, tüm API route'ları
├── database.py     → SQLite bağlantısı ve tablo oluşturma
├── requirements.txt
├── tracker.db      → Otomatik oluşur
└── static/
    └── index.html  → Tek sayfa uygulama (login + öğrenci + hoca paneli)
```
