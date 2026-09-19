#!/usr/bin/env python3
"""
Nester - Shared Housing Roommate Finder
Real full-stack app: form → SQLite → admin panel → matching → email
"""

import os
import sqlite3
import secrets
import json
import csv
import io
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_file, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "nester.db"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

login_attempts = {}
RATE_LIMIT_WINDOW = 300
RATE_LIMIT_MAX = 8

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS property (
        id INTEGER PRIMARY KEY,
        address TEXT,
        available_from TEXT,
        rooms INTEGER,
        people INTEGER,
        price_per_room INTEGER,
        description TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        status TEXT DEFAULT 'new',
        full_name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT,
        age INTEGER,
        nationality TEXT,
        current_country TEXT,
        city_area TEXT,
        languages TEXT,
        work_study TEXT,
        monthly_budget INTEGER,
        move_in_date TEXT,
        stay_length TEXT,
        preferred_home_type TEXT,
        sleep_schedule TEXT,
        cleanliness TEXT,
        smoking TEXT,
        pets TEXT,
        alcohol TEXT,
        social_style TEXT,
        guests TEXT,
        cooking TEXT,
        roommate_expectations TEXT,
        deal_breakers TEXT,
        about_you TEXT,
        match_score INTEGER DEFAULT 0,
        match_reasons TEXT,
        ip_address TEXT
    );
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id INTEGER NOT NULL,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        action TEXT NOT NULL,
        details TEXT,
        application_id INTEGER
    );
    """)
    cur = db.execute("SELECT COUNT(*) FROM property")
    if cur.fetchone()[0] == 0:
        db.execute(
            """INSERT INTO property (id, address, available_from, rooms, people, price_per_room, description, updated_at)
               VALUES (1, ?, ?, 2, 2, 1000, ?, ?)""",
            (
                "15 Pinewood Grove, Commons Road, Cork, T23 V6W7, Ireland",
                "2025-11-29",
                "A calm, well-kept shared house in Cork — real photos, honest details, and a straightforward application that a real person actually reads.",
                datetime.utcnow().isoformat()
            )
        )
    db.commit()
    db.close()

def get_admin_hash():
    plain = os.getenv("ADMIN_PASSWORD", "nester-admin-2025")
    hash_file = DATA_DIR / "admin_hash.txt"
    if hash_file.exists():
        return hash_file.read_text().strip()
    h = generate_password_hash(plain)
    hash_file.write_text(h)
    return h

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

def check_rate_limit(ip):
    now = datetime.utcnow().timestamp()
    if ip not in login_attempts:
        login_attempts[ip] = []
    login_attempts[ip] = [t for t in login_attempts[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(login_attempts[ip]) >= RATE_LIMIT_MAX:
        return False
    login_attempts[ip].append(now)
    return True

def calculate_match(app_data):
    score = 50
    positives = []
    diffs = []
    budget = app_data.get("monthly_budget") or 0
    try:
        budget = int(budget)
    except:
        budget = 0
    if 800 <= budget <= 1300:
        score += 12
        positives.append("Similar budget (€800–1300)")
    elif budget > 1300:
        score += 5
        positives.append("Higher budget available")
    else:
        score -= 8
        diffs.append("Budget below preferred range")
    move = (app_data.get("move_in_date") or "").lower()
    if "nov" in move or "29" in move or "december" in move or "january" in move or "asap" in move:
        score += 10
        positives.append("Compatible move-in period")
    else:
        diffs.append("Move-in date may not match availability (from 29 Nov)")
    stay = (app_data.get("stay_length") or "").lower()
    if any(x in stay for x in ["6", "12", "year", "long", "permanent"]):
        score += 8
        positives.append("Longer stay preferred")
    elif "3" in stay or "month" in stay:
        score += 3
    clean = (app_data.get("cleanliness") or "").lower()
    if any(x in clean for x in ["very", "high", "tidy", "clean", "neat"]):
        score += 8
        positives.append("High cleanliness standards")
    elif "average" in clean or "moderate" in clean:
        score += 4
    else:
        diffs.append("Cleanliness preference may differ")
    smoke = (app_data.get("smoking") or "").lower()
    if smoke in ("no", "never", "non-smoker", "non smoker"):
        score += 10
        positives.append("Non-smoker")
    elif "outside" in smoke or "occasional" in smoke:
        score += 2
        diffs.append("Occasional smoking")
    else:
        score -= 15
        diffs.append("Smoker – house is non-smoking preferred")
    pets = (app_data.get("pets") or "").lower()
    if pets in ("no", "none", "no pets"):
        score += 6
        positives.append("No pets")
    else:
        score -= 5
        diffs.append("Has / wants pets")
    alc = (app_data.get("alcohol") or "").lower()
    if any(x in alc for x in ["rarely", "never", "social", "moderate", "occasional"]):
        score += 4
        positives.append("Moderate alcohol use")
    elif "heavy" in alc or "daily" in alc:
        score -= 6
        diffs.append("Heavier alcohol use")
    sleep = (app_data.get("sleep_schedule") or "").lower()
    if any(x in sleep for x in ["early", "normal", "regular", "9", "10", "11"]):
        score += 5
        positives.append("Compatible sleep schedule")
    elif "night" in sleep or "late" in sleep:
        diffs.append("Late sleep schedule")
    social = (app_data.get("social_style") or "").lower()
    if any(x in social for x in ["friendly", "balanced", "quiet", "respectful", "chill"]):
        score += 6
        positives.append("Compatible social style")
    elif "party" in social or "very social" in social:
        diffs.append("Very social / party lifestyle")
    guests = (app_data.get("guests") or "").lower()
    if any(x in guests for x in ["rarely", "occasional", "ask", "no", "limited"]):
        score += 4
        positives.append("Respectful guest policy")
    else:
        diffs.append("Frequent guests possible")
    cook = (app_data.get("cooking") or "").lower()
    if any(x in cook for x in ["yes", "often", "love", "cook"]):
        score += 3
        positives.append("Enjoys cooking")
    score = max(0, min(100, score))
    return score, positives, diffs

def send_admin_notification(app_row):
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    to_email = os.getenv("ADMIN_EMAIL")
    if not all([host, user, password, to_email]):
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = to_email
        msg["Subject"] = f"[Nester] New application from {app_row['full_name']}"
        body = f"""New roommate application received!

Name: {app_row['full_name']}
Email: {app_row['email']}
Phone: {app_row.get('phone', '-')}
Age: {app_row.get('age', '-')}
Nationality: {app_row.get('nationality', '-')}
Budget: €{app_row.get('monthly_budget', '-')}
Move-in: {app_row.get('move_in_date', '-')}
Match score: {app_row.get('match_score', 0)}%

Open admin panel to review.
"""
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", 587))) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:
        print("Email error:", e)
        return False

COUNTRIES = [
    "Afghanistan","Albania","Algeria","Andorra","Angola","Antigua and Barbuda","Argentina","Armenia","Australia","Austria",
    "Azerbaijan","Bahamas","Bahrain","Bangladesh","Barbados","Belarus","Belgium","Belize","Benin","Bhutan",
    "Bolivia","Bosnia and Herzegovina","Botswana","Brazil","Brunei","Bulgaria","Burkina Faso","Burundi","Cabo Verde","Cambodia",
    "Cameroon","Canada","Central African Republic","Chad","Chile","China","Colombia","Comoros","Congo","Costa Rica",
    "Croatia","Cuba","Cyprus","Czech Republic","Denmark","Djibouti","Dominica","Dominican Republic","Ecuador","Egypt",
    "El Salvador","Equatorial Guinea","Eritrea","Estonia","Eswatini","Ethiopia","Fiji","Finland","France","Gabon",
    "Gambia","Georgia","Germany","Ghana","Greece","Grenada","Guatemala","Guinea","Guinea-Bissau","Guyana",
    "Haiti","Honduras","Hungary","Iceland","India","Indonesia","Iran","Iraq","Ireland","Israel",
    "Italy","Jamaica","Japan","Jordan","Kazakhstan","Kenya","Kiribati","Kuwait","Kyrgyzstan","Laos",
    "Latvia","Lebanon","Lesotho","Liberia","Libya","Liechtenstein","Lithuania","Luxembourg","Madagascar","Malawi",
    "Malaysia","Maldives","Mali","Malta","Marshall Islands","Mauritania","Mauritius","Mexico","Micronesia","Moldova",
    "Monaco","Mongolia","Montenegro","Morocco","Mozambique","Myanmar","Namibia","Nauru","Nepal","Netherlands",
    "New Zealand","Nicaragua","Niger","Nigeria","North Korea","North Macedonia","Norway","Oman","Pakistan","Palau",
    "Palestine","Panama","Papua New Guinea","Paraguay","Peru","Philippines","Poland","Portugal","Qatar","Romania",
    "Russia","Rwanda","Saint Kitts and Nevis","Saint Lucia","Saint Vincent and the Grenadines","Samoa","San Marino","Sao Tome and Principe","Saudi Arabia","Senegal",
    "Serbia","Seychelles","Sierra Leone","Singapore","Slovakia","Slovenia","Solomon Islands","Somalia","South Africa","South Korea",
    "South Sudan","Spain","Sri Lanka","Sudan","Suriname","Sweden","Switzerland","Syria","Taiwan","Tajikistan",
    "Tanzania","Thailand","Timor-Leste","Togo","Tonga","Trinidad and Tobago","Tunisia","Turkey","Turkmenistan","Tuvalu",
    "Uganda","Ukraine","United Arab Emirates","United Kingdom","United States","Uruguay","Uzbekistan","Vanuatu","Vatican City","Venezuela",
    "Vietnam","Yemen","Zambia","Zimbabwe","Other"
]

@app.route("/")
def home():
    db = get_db()
    prop = db.execute("SELECT * FROM property WHERE id=1").fetchone()
    return render_template("index.html", prop=prop, active="home")

@app.route("/the-home")
def the_home():
    db = get_db()
    prop = db.execute("SELECT * FROM property WHERE id=1").fetchone()
    return render_template("the_home.html", prop=prop, active="the-home")

@app.route("/gallery")
def gallery():
    img_dir = BASE_DIR / "static" / "images"
    images = sorted([f.name for f in img_dir.glob("*.jpg") if not f.name.startswith(".")])
    return render_template("gallery.html", images=images, active="gallery")

@app.route("/apply", methods=["GET", "POST"])
def apply():
    if request.method == "POST":
        data = {k: request.form.get(k, "").strip() for k in [
            "full_name","email","phone","age","nationality","current_country","city_area",
            "languages","work_study","monthly_budget","move_in_date","stay_length",
            "preferred_home_type","sleep_schedule","cleanliness","smoking","pets",
            "alcohol","social_style","guests","cooking","roommate_expectations",
            "deal_breakers","about_you"
        ]}
        data["email"] = data["email"].lower()
        errors = []
        if not data["full_name"] or len(data["full_name"]) < 2:
            errors.append("Full name is required")
        if not data["email"] or "@" not in data["email"]:
            errors.append("Valid email is required")
        if errors:
            return render_template("apply.html", countries=COUNTRIES, errors=errors, form=data, active="apply")
        score, positives, diffs = calculate_match(data)
        reasons = json.dumps({"positives": positives, "diffs": diffs})
        db = get_db()
        cur = db.execute(
            """INSERT INTO applications (
                created_at, status, full_name, email, phone, age, nationality,
                current_country, city_area, languages, work_study, monthly_budget,
                move_in_date, stay_length, preferred_home_type, sleep_schedule,
                cleanliness, smoking, pets, alcohol, social_style, guests, cooking,
                roommate_expectations, deal_breakers, about_you, match_score, match_reasons, ip_address
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.utcnow().isoformat(), "new",
                data["full_name"], data["email"], data["phone"],
                int(data["age"]) if data["age"] and data["age"].isdigit() else None,
                data["nationality"], data["current_country"], data["city_area"],
                data["languages"], data["work_study"],
                int(data["monthly_budget"]) if data["monthly_budget"] and str(data["monthly_budget"]).isdigit() else None,
                data["move_in_date"], data["stay_length"], data["preferred_home_type"],
                data["sleep_schedule"], data["cleanliness"], data["smoking"],
                data["pets"], data["alcohol"], data["social_style"], data["guests"],
                data["cooking"], data["roommate_expectations"], data["deal_breakers"],
                data["about_you"], score, reasons, request.remote_addr
            )
        )
        app_id = cur.lastrowid
        db.execute(
            "INSERT INTO activity_log (created_at, action, details, application_id) VALUES (?,?,?,?)",
            (datetime.utcnow().isoformat(), "new_application", f"From {data['full_name']}", app_id)
        )
        db.commit()
        row = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        send_admin_notification(dict(row))
        return render_template("apply_success.html", name=data["full_name"], active="apply")
    return render_template("apply.html", countries=COUNTRIES, errors=None, form={}, active="apply")

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr
        if not check_rate_limit(ip):
            error = "Too many attempts. Please wait a few minutes."
        else:
            pwd = request.form.get("password", "")
            if check_password_hash(get_admin_hash(), pwd):
                session["admin_logged_in"] = True
                session.permanent = True
                db = get_db()
                db.execute(
                    "INSERT INTO activity_log (created_at, action, details) VALUES (?,?,?)",
                    (datetime.utcnow().isoformat(), "admin_login", f"IP {ip}")
                )
                db.commit()
                return redirect(url_for("admin_dashboard"))
            else:
                error = "Incorrect password"
    return render_template("admin_login.html", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    db = get_db()
    counts = {}
    for status in ["new", "reviewing", "shortlisted", "matched", "rejected"]:
        counts[status] = db.execute(
            "SELECT COUNT(*) FROM applications WHERE status=?", (status,)
        ).fetchone()[0]
    total = sum(counts.values())
    recent = db.execute(
        "SELECT * FROM applications ORDER BY created_at DESC LIMIT 8"
    ).fetchall()
    return render_template("admin_dashboard.html", counts=counts, total=total, recent=recent)

@app.route("/admin/applications")
@login_required
def admin_applications():
    db = get_db()
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "newest")
    sql = "SELECT * FROM applications WHERE 1=1"
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if q:
        sql += " AND (full_name LIKE ? OR email LIKE ? OR nationality LIKE ? OR city_area LIKE ?)"
        params.extend([f"%{q}%"] * 4)
    if sort == "newest":
        sql += " ORDER BY created_at DESC"
    elif sort == "oldest":
        sql += " ORDER BY created_at ASC"
    elif sort == "score":
        sql += " ORDER BY match_score DESC"
    elif sort == "name":
        sql += " ORDER BY full_name ASC"
    apps = db.execute(sql, params).fetchall()
    return render_template("admin_applications.html", apps=apps, status=status, q=q, sort=sort)

@app.route("/admin/application/<int:app_id>", methods=["GET", "POST"])
@login_required
def admin_application(app_id):
    db = get_db()
    app_row = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    if not app_row:
        flash("Application not found", "error")
        return redirect(url_for("admin_applications"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "status":
            new_status = request.form.get("status")
            if new_status in ("new", "reviewing", "shortlisted", "matched", "rejected"):
                db.execute("UPDATE applications SET status=? WHERE id=?", (new_status, app_id))
                db.execute(
                    "INSERT INTO activity_log (created_at, action, details, application_id) VALUES (?,?,?,?)",
                    (datetime.utcnow().isoformat(), "status_change", f"→ {new_status}", app_id)
                )
                db.commit()
                flash(f"Status updated to {new_status}", "success")
        elif action == "note":
            note = request.form.get("note", "").strip()
            if note:
                db.execute(
                    "INSERT INTO notes (application_id, note, created_at) VALUES (?,?,?)",
                    (app_id, note, datetime.utcnow().isoformat())
                )
                db.execute(
                    "INSERT INTO activity_log (created_at, action, details, application_id) VALUES (?,?,?,?)",
                    (datetime.utcnow().isoformat(), "note_added", note[:80], app_id)
                )
                db.commit()
                flash("Note added", "success")
        elif action == "delete":
            db.execute("DELETE FROM applications WHERE id=?", (app_id,))
            db.execute(
                "INSERT INTO activity_log (created_at, action, details) VALUES (?,?,?)",
                (datetime.utcnow().isoformat(), "application_deleted", f"ID {app_id}")
            )
            db.commit()
            flash("Application deleted", "success")
            return redirect(url_for("admin_applications"))
        return redirect(url_for("admin_application", app_id=app_id))
    notes = db.execute(
        "SELECT * FROM notes WHERE application_id=? ORDER BY created_at DESC", (app_id,)
    ).fetchall()
    reasons = {}
    try:
        reasons = json.loads(app_row["match_reasons"] or "{}")
    except:
        pass
    return render_template("admin_application.html", app=app_row, notes=notes, reasons=reasons)

@app.route("/admin/export")
@login_required
def admin_export():
    db = get_db()
    rows = db.execute("SELECT * FROM applications ORDER BY created_at DESC").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "created_at", "status", "full_name", "email", "phone", "age",
        "nationality", "current_country", "city_area", "languages", "work_study",
        "monthly_budget", "move_in_date", "stay_length", "match_score",
        "sleep_schedule", "cleanliness", "smoking", "pets", "alcohol",
        "social_style", "guests", "cooking", "about_you"
    ])
    for r in rows:
        writer.writerow([
            r["id"], r["created_at"], r["status"], r["full_name"], r["email"],
            r["phone"], r["age"], r["nationality"], r["current_country"],
            r["city_area"], r["languages"], r["work_study"], r["monthly_budget"],
            r["move_in_date"], r["stay_length"], r["match_score"],
            r["sleep_schedule"], r["cleanliness"], r["smoking"], r["pets"],
            r["alcohol"], r["social_style"], r["guests"], r["cooking"], r["about_you"]
        ])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"nester_applications_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    )

@app.route("/admin/activity")
@login_required
def admin_activity():
    db = get_db()
    logs = db.execute(
        "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    return render_template("admin_activity.html", logs=logs)

@app.route("/admin/property", methods=["GET", "POST"])
@login_required
def admin_property():
    db = get_db()
    if request.method == "POST":
        db.execute(
            """UPDATE property SET address=?, available_from=?, rooms=?, people=?,
               price_per_room=?, description=?, updated_at=? WHERE id=1""",
            (
                request.form.get("address"),
                request.form.get("available_from"),
                int(request.form.get("rooms") or 2),
                int(request.form.get("people") or 2),
                int(request.form.get("price_per_room") or 1000),
                request.form.get("description"),
                datetime.utcnow().isoformat()
            )
        )
        db.commit()
        flash("Property updated", "success")
        return redirect(url_for("admin_property"))
    prop = db.execute("SELECT * FROM property WHERE id=1").fetchone()
    return render_template("admin_property.html", prop=prop)

@app.after_request
def set_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

if __name__ == "__main__":
    init_db()
    get_admin_hash()
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV") == "development"
    print(f"Nester running on http://0.0.0.0:{port}")
    print("Default admin password: nester-admin-2025 (change via ADMIN_PASSWORD in .env)")
    app.run(host="0.0.0.0", port=port, debug=debug)
