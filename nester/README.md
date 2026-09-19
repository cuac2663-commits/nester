# Nester — Find your place. Find your people.

Real full-stack roommate matching website for the shared house at **15 Pinewood Grove, Cork**.

## Features

- Beautiful dark purple-black modern design (responsive)
- Home / The home / Gallery (with lightbox) / Apply
- Real application form with full country list + many lifestyle fields
- Applications saved to **SQLite** database
- Automatic **compatibility score** based on real answers
- Secure **Admin panel** (password hashed, sessions, rate limiting)
  - Dashboard with status counts
  - Search / filter / sort applications
  - View full application + match reasons
  - Change status, internal notes, delete
  - Activity log
  - CSV export
  - Edit property info
- Optional email notification on new application (SMTP via .env)
- Security headers, no password in frontend code

## Quick start

```bash
cd nester
pip install -r requirements.txt
# edit .env if needed (ADMIN_PASSWORD, SMTP...)
python app.py
```

Open http://localhost:5000

**Admin:** http://localhost:5000/admin  
Default password: `nester-admin-2025`  
(Change it in `.env` → `ADMIN_PASSWORD=your-strong-password`)

## Production notes

- Set strong `SECRET_KEY` and `ADMIN_PASSWORD` in `.env`
- Use a real WSGI server (gunicorn / waitress)
- Put behind HTTPS
- Configure SMTP for email alerts
- The DB file is `data/nester.db`

## Stack

- Python 3 + Flask
- SQLite
- Tailwind CSS (CDN)
- bcrypt / werkzeug for password hashing
