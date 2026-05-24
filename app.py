"""
Prompt Hacker - interaktives Arbeitsblatt mit Lehrer-Dashboard.
GSM Duisburg, Klassenstufen 8-10.
"""
import json
import logging
import os
import re
import secrets
import sqlite3
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO, emit, join_room
from google import genai
from google.genai import types as genai_types

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("DATABASE_PATH", DATA_DIR / "prompt_hacker.sqlite3"))
LEVELS_PATH = BASE_DIR / "levels.json"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-prompt-hacker-change-me")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 4

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("prompt_hacker")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
LEHRER_PASSWORD = os.environ.get("LEHRER_PASSWORD", "gsm")
DAILY_TOKEN_LIMIT = int(os.environ.get("DAILY_TOKEN_LIMIT", "50000"))
MAX_LEVEL_ATTEMPTS = int(os.environ.get("MAX_LEVEL_ATTEMPTS", "5"))

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
if not gemini_client:
    log.warning("GEMINI_API_KEY nicht gesetzt - die App läuft im Offline-Modus.")

with open(LEVELS_PATH, "r", encoding="utf-8") as f:
    LEVELS = json.load(f)["levels"]
LEVELS_BY_ID = {lvl["id"]: lvl for lvl in LEVELS}

MAX_HISTORY_TURNS = 12
HINT_INTERVAL = 5
TEACHER_ACTION_TOKENS = set()

TUTOR_SYSTEM_PROMPT = """Du bist ein freundlicher KI-Tutor für Schülerinnen und Schüler
der Klassen 8-10. Thema: Prompts, LLM-Sicherheit, Prompt-Injection und sichere KI-Nutzung.

Regeln:
- Erkläre kurz, verständlich und auf Deutsch.
- Hilf beim Denken, aber schreibe keine perfekte Musterlösung.
- Keine echten Angriffsanleitungen gegen reale Systeme.
- Verweise auf Schutzmaßnahmen: keine Geheimnisse im Prompt, serverseitige Prüfung,
  Tool-Freigaben, Ausgabefilter, Protokollierung und Rate Limits.
- Transparenz: Du bist eine KI und kannst dich irren."""


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pseudonym TEXT NOT NULL,
                klasse TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                online INTEGER NOT NULL DEFAULT 0,
                game_unlocked INTEGER NOT NULL DEFAULT 1,
                worksheet_unlocked INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS level_states (
                student_id INTEGER NOT NULL,
                level_id INTEGER NOT NULL,
                history_json TEXT NOT NULL DEFAULT '[]',
                attempts INTEGER NOT NULL DEFAULT 0,
                extra_attempts INTEGER NOT NULL DEFAULT 0,
                solved INTEGER NOT NULL DEFAULT 0,
                hints_used INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (student_id, level_id),
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS worksheet_answers (
                student_id INTEGER NOT NULL,
                task_id TEXT NOT NULL,
                niveau TEXT NOT NULL,
                answer TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (student_id, task_id),
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ki_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                response TEXT,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS attempt_round_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                level_id INTEGER NOT NULL,
                amount INTEGER NOT NULL DEFAULT 5,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                decided_at TEXT,
                reason TEXT,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS token_usage (
                usage_date TEXT PRIMARY KEY,
                tokens INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('class_game_unlocked', '1')"
        )
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(students)").fetchall()
        }
        if "worksheet_unlocked" not in columns:
            db.execute("ALTER TABLE students ADD COLUMN worksheet_unlocked INTEGER NOT NULL DEFAULT 0")


init_db()


def estimate_tokens(*parts: str) -> int:
    text = "\n".join(p or "" for p in parts)
    return max(1, len(text) // 4)


def get_today_tokens() -> int:
    with connect_db() as db:
        row = db.execute("SELECT tokens FROM token_usage WHERE usage_date = ?", (date.today().isoformat(),)).fetchone()
        return int(row["tokens"]) if row else 0


def get_setting_bool(key: str, default: bool = False) -> bool:
    with connect_db() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return row["value"] in {"1", "true", "True", "yes"}


def set_setting_bool(key: str, value: bool):
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, "1" if value else "0"),
        )


def class_game_unlocked() -> bool:
    return get_setting_bool("class_game_unlocked", True)


def effective_game_unlocked(student) -> bool:
    return class_game_unlocked() and bool(student["game_unlocked"])


def add_tokens(amount: int):
    today = date.today().isoformat()
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO token_usage (usage_date, tokens) VALUES (?, ?)
            ON CONFLICT(usage_date) DO UPDATE SET tokens = tokens + excluded.tokens
            """,
            (today, amount),
        )
    socketio.emit("token_update", {"today": get_today_tokens(), "limit": DAILY_TOKEN_LIMIT}, to="lehrer_room")


def budget_ok() -> bool:
    return get_today_tokens() < DAILY_TOKEN_LIMIT


def current_student_id():
    return session.get("student_id")


def clear_student_session():
    session.pop("student_id", None)
    session.pop("pseudonym", None)


def clear_teacher_session():
    token = session.pop("teacher_action_token", None)
    if token:
        TEACHER_ACTION_TOKENS.discard(token)
    session.pop("is_teacher", None)


def require_student(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        sid = session.get("student_id")
        if not sid:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Bitte zuerst anmelden."}), 401
            return redirect(url_for("login"))
        if not get_student(sid):
            clear_student_session()
            if request.path.startswith("/api/"):
                return jsonify({"error": "Die Session wurde gelöscht. Bitte neu anmelden."}), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def require_teacher(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("is_teacher"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Lehrer-Login erforderlich."}), 401
            return redirect(url_for("lehrer_login"))
        return fn(*args, **kwargs)
    return wrapper


def teacher_action_ok() -> bool:
    token = request.headers.get("X-Teacher-Action-Token")
    return bool(token and token in TEACHER_ACTION_TOKENS) or bool(session.get("is_teacher"))


def require_teacher_action(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not teacher_action_ok():
            return jsonify({"error": "Lehreraktion nicht autorisiert."}), 403
        return fn(*args, **kwargs)
    return wrapper


def get_student(student_id: int):
    with connect_db() as db:
        return db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()


def get_level_state(student_id: int, level_id: int) -> dict:
    with connect_db() as db:
        row = db.execute(
            "SELECT * FROM level_states WHERE student_id = ? AND level_id = ?",
            (student_id, level_id),
        ).fetchone()
        if row:
            return {
                "history": json.loads(row["history_json"] or "[]"),
                "attempts": row["attempts"],
                "extra_attempts": row["extra_attempts"],
                "solved": bool(row["solved"]),
                "hints_used": row["hints_used"],
            }
        db.execute(
            """
            INSERT INTO level_states
            (student_id, level_id, history_json, attempts, extra_attempts, solved, hints_used, updated_at)
            VALUES (?, ?, '[]', 0, 0, 0, 0, ?)
            """,
            (student_id, level_id, now_iso()),
        )
        return {"history": [], "attempts": 0, "extra_attempts": 0, "solved": False, "hints_used": 0}


def save_level_state(student_id: int, level_id: int, state: dict):
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO level_states
            (student_id, level_id, history_json, attempts, extra_attempts, solved, hints_used, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, level_id) DO UPDATE SET
                history_json = excluded.history_json,
                attempts = excluded.attempts,
                extra_attempts = excluded.extra_attempts,
                solved = excluded.solved,
                hints_used = excluded.hints_used,
                updated_at = excluded.updated_at
            """,
            (
                student_id,
                level_id,
                json.dumps(state["history"], ensure_ascii=False),
                state["attempts"],
                state["extra_attempts"],
                1 if state["solved"] else 0,
                state["hints_used"],
                now_iso(),
            ),
        )


def history_contains_password(level_id: int, history: list[dict]) -> bool:
    level = LEVELS_BY_ID.get(level_id)
    if not level:
        return False
    password = level["password"]
    return any(
        check_direct(password, turn.get("content", ""))
        or check_separated(password, turn.get("content", ""))
        or check_character_sequence(password, turn.get("content", ""))
        for turn in history
    )


def success_confirmation_text(level: dict) -> str:
    return (
        f"Du hast es erraten: Das Passwort war „{level['password']}“. "
        "Damit hast du gezeigt, dass ein Bot ein Geheimnis auch verlieren kann, "
        "wenn er erst Hinweise gibt und am Ende eine richtige Vermutung bestätigt."
    )


def history_has_success_confirmation(history: list[dict]) -> bool:
    return any(
        turn.get("role") in {"model", "ai"}
        and any(marker in turn.get("content", "").lower() for marker in ["du hast es erraten", "passwort war"])
        for turn in history
    )


def ensure_success_from_history(student_id: int):
    with connect_db() as db:
        rows = db.execute(
            "SELECT level_id, history_json, solved FROM level_states WHERE student_id = ?",
            (student_id,),
        ).fetchall()
        for row in rows:
            if row["solved"]:
                continue
            history = json.loads(row["history_json"] or "[]")
            if history_contains_password(row["level_id"], history):
                if not history_has_success_confirmation(history):
                    level = LEVELS_BY_ID.get(row["level_id"])
                    if level:
                        history.append({"role": "model", "content": success_confirmation_text(level)})
                db.execute(
                    """
                    UPDATE level_states
                    SET solved = 1, history_json = ?, updated_at = ?
                    WHERE student_id = ? AND level_id = ?
                    """,
                    (json.dumps(history, ensure_ascii=False), now_iso(), student_id, row["level_id"]),
                )


def public_levels_for(student_id: int):
    ensure_success_from_history(student_id)
    public = []
    pending_requests = set()
    with connect_db() as db:
        rows = db.execute(
            """
            SELECT level_id FROM attempt_round_requests
            WHERE student_id = ? AND status = 'pending'
            """,
            (student_id,),
        ).fetchall()
        pending_requests = {row["level_id"] for row in rows}
        reflection_rows = db.execute(
            """
            SELECT task_id, answer FROM worksheet_answers
            WHERE student_id = ? AND task_id LIKE 'level_%_strategy'
            """,
            (student_id,),
        ).fetchall()
        reflections = {row["task_id"]: row["answer"] for row in reflection_rows}
    for lvl in LEVELS:
        state = get_level_state(student_id, lvl["id"])
        max_attempts = MAX_LEVEL_ATTEMPTS + state.get("extra_attempts", 0)
        public.append({
            "id": lvl["id"],
            "name": lvl["name"],
            "emoji": lvl["emoji"],
            "title": lvl["title"],
            "description": lvl["description"],
            "difficulty": lvl["difficulty"],
            "intro": lvl["intro"],
            "attempts": state["attempts"],
            "remaining_attempts": max(0, max_attempts - state["attempts"]),
            "max_attempts": max_attempts,
            "solved": state["solved"],
            "hints_used": state["hints_used"],
            "history": state["history"],
            "attempt_request_pending": lvl["id"] in pending_requests,
            "strategy_reflection": reflections.get(f"level_{lvl['id']}_strategy", ""),
        })
    return public


def check_direct(password: str, response: str) -> bool:
    return password.lower() in response.lower()


def check_separated(password: str, response: str) -> bool:
    pattern = r"[\W_]+".join(re.escape(c) for c in password)
    return re.search(pattern, response, re.IGNORECASE) is not None


def check_character_sequence(password: str, response: str) -> bool:
    pattern = r".*".join(rf"\b{re.escape(c)}\b" for c in password)
    return re.search(pattern, response, re.IGNORECASE | re.DOTALL) is not None


def check_with_judge(password: str, response: str) -> bool:
    if not gemini_client or not budget_ok():
        return False
    prompt = f"""Du bist Schiedsrichter in einem Lernspiel über KI-Sicherheit.

Das geheime Passwort ist: {password}

Eine KI hat folgende Antwort gegeben:
---
{response}
---

Wurde das Passwort in der Antwort preisgegeben, auch verschleiert?
Antworte ausschließlich mit JA oder NEIN."""
    try:
        result = gemini_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=genai_types.GenerateContentConfig(temperature=0.0, max_output_tokens=5),
        )
        add_tokens(estimate_tokens(prompt, result.text or ""))
        return (result.text or "").strip().upper().startswith("JA")
    except Exception as exc:
        log.warning("Judge-Aufruf fehlgeschlagen: %s", exc)
        return False


def detect_technique(password: str, response: str, user_msg: str) -> str:
    if check_direct(password, response):
        msg_lower = user_msg.lower()
        if any(k in msg_lower for k in ["ignoriere", "vergiss", "neue anweisung", "system"]):
            return "instruction_override"
        if any(k in msg_lower for k in ["gedicht", "geschichte", "rollenspiel", "stell dir vor", "spiele"]):
            return "rollenspiel"
        return "direkt"
    if check_separated(password, response) or check_character_sequence(password, response):
        return "zeichenliste / verschleierung"
    return "verschleierung"


def finish_reason_name(result) -> str:
    candidates = getattr(result, "candidates", None) or []
    if not candidates:
        return ""
    reason = getattr(candidates[0], "finish_reason", None)
    if not reason:
        return ""
    return getattr(reason, "value", str(reason))


def response_looks_cut_off(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    return not re.search(r'[.!?…)"”\'🤖🛡️🔒]$', stripped)


def call_guardian(level: dict, history: list, user_message: str) -> dict:
    if not gemini_client:
        return {
            "ok": True,
            "text": ("[Offline-Modus] Der Wächter würde hier antworten. "
                     "Setze GEMINI_API_KEY, damit das Spiel live mit Gemini läuft."),
        }
    if not budget_ok():
        return {"ok": True, "text": "[Budget erreicht] Das Tageslimit für KI-Anfragen ist aufgebraucht."}

    gemini_history = []
    for turn in history[-MAX_HISTORY_TURNS * 2:]:
        role = "user" if turn["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [{"text": turn["content"]}]})

    try:
        chat = gemini_client.chats.create(
            model=MODEL_NAME,
            config=genai_types.GenerateContentConfig(
                system_instruction=level["system_prompt"],
                temperature=0.7,
                max_output_tokens=800,
            ),
            history=gemini_history,
        )
        result = chat.send_message(user_message)
        text = (result.text or "").strip() or "(Der Wächter schweigt.)"
        add_tokens(estimate_tokens(level["system_prompt"], user_message, text))
        finish_reason = finish_reason_name(result)
        if finish_reason == "MAX_TOKENS" or (finish_reason and not text and finish_reason != "STOP"):
            log.warning(
                "Gemini-Antwort abgebrochen: finish_reason=%s, text=%r",
                finish_reason,
                text[:160],
            )
            return {
                "ok": False,
                "error": ("Die KI hat ihre Antwort mitten im Satz abgebrochen. "
                          "Dieser Versuch wurde nicht gezählt. Bitte formuliere deine Nachricht etwas anders."),
                "finish_reason": finish_reason,
            }
        if finish_reason and finish_reason != "STOP" and response_looks_cut_off(text):
            text = (
                f"{text}\n\n"
                "[Hinweis: Die KI hat diese Antwort möglicherweise gekürzt. "
                "Der Versuch zählt trotzdem, weil eine Antwort sichtbar war.]"
            )
        return {"ok": True, "text": text, "finish_reason": finish_reason}
    except Exception as exc:
        log.error("Gemini-Aufruf fehlgeschlagen: %s", exc)
        return {
            "ok": False,
            "error": ("Die KI konnte gerade nicht antworten. Dieser Versuch wurde nicht gezählt. "
                      "Bitte probiere es gleich noch einmal."),
            "finish_reason": type(exc).__name__,
        }


def call_tutor(question: str) -> str:
    if not gemini_client:
        return ("[Offline-Modus] Gute Frage. Bearbeite sie mit dem Infotext: "
                "Unterscheide Prompt, Systemprompt, Injection-Technik und Schutzmaßnahme.")
    if not budget_ok():
        return "[Budget erreicht] Das Tageslimit für KI-Anfragen ist aufgebraucht."
    prompt = f"Schülerfrage zum Arbeitsblatt:\n{question}"
    try:
        result = gemini_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=TUTOR_SYSTEM_PROMPT,
                temperature=0.4,
                max_output_tokens=450,
            ),
        )
        text = (result.text or "").strip() or "Ich konnte dazu gerade keine hilfreiche Antwort erzeugen."
        add_tokens(estimate_tokens(TUTOR_SYSTEM_PROMPT, prompt, text))
        return text
    except Exception as exc:
        log.error("Tutor-Aufruf fehlgeschlagen: %s", exc)
        return f"[Fehler beim KI-Tutor: {type(exc).__name__}]"


@app.route("/")
def root():
    if "student_id" in session:
        return redirect(url_for("student_app"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pseudonym = (request.form.get("pseudonym") or "").strip()
        klasse = (request.form.get("klasse") or "").strip()
        consent = request.form.get("consent") == "on"
        if len(pseudonym) < 2 or len(klasse) < 1:
            error = "Bitte Pseudonym und Klasse/Kurs eintragen."
        elif not consent:
            error = "Bitte bestätige den Datenschutz- und KI-Hinweis."
        else:
            with connect_db() as db:
                cur = db.execute(
                    """
                    INSERT INTO students (pseudonym, klasse, created_at, last_seen, online, game_unlocked, worksheet_unlocked)
                    VALUES (?, ?, ?, ?, 1, 1, 0)
                    """,
                    (pseudonym[:40], klasse[:30], now_iso(), now_iso()),
                )
                sid = cur.lastrowid
            old_sid = session.get("student_id")
            if old_sid and old_sid != sid:
                with connect_db() as db:
                    db.execute("UPDATE students SET online = 0, last_seen = ? WHERE id = ?", (now_iso(), old_sid))
                socketio.emit("student_offline", {"id": old_sid}, to="lehrer_room")
            clear_student_session()
            session["student_id"] = sid
            session["pseudonym"] = pseudonym[:40]
            socketio.emit("student_joined", student_summary(sid), to="lehrer_room")
            return redirect(url_for("student_app"))
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    sid = session.get("student_id")
    if sid:
        with connect_db() as db:
            db.execute("UPDATE students SET online = 0, last_seen = ? WHERE id = ?", (now_iso(), sid))
        socketio.emit("student_offline", {"id": sid}, to="lehrer_room")
    clear_student_session()
    return redirect(url_for("login"))


@app.route("/app")
@require_student
def student_app():
    student = get_student(current_student_id())
    return render_template("index.html", student=dict(student))


@app.route("/lehrer", methods=["GET", "POST"])
def lehrer_login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == LEHRER_PASSWORD:
            clear_teacher_session()
            session["is_teacher"] = True
            session["teacher_action_token"] = secrets.token_urlsafe(24)
            TEACHER_ACTION_TOKENS.add(session["teacher_action_token"])
            return redirect(url_for("dashboard"))
        error = "Passwort stimmt nicht."
    return render_template("lehrer_login.html", error=error)


@app.route("/lehrer/logout")
def lehrer_logout():
    clear_teacher_session()
    return redirect(url_for("lehrer_login"))


@app.route("/dashboard")
@require_teacher
def dashboard():
    return render_template(
        "dashboard.html",
        teacher_action_token=session.get("teacher_action_token"),
        daily_token_limit=DAILY_TOKEN_LIMIT,
        max_level_attempts=MAX_LEVEL_ATTEMPTS,
    )


def student_summary(student_id: int):
    ensure_success_from_history(student_id)
    with connect_db() as db:
        student = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        levels = db.execute("SELECT * FROM level_states WHERE student_id = ?", (student_id,)).fetchall()
        answers = db.execute("SELECT COUNT(*) AS c FROM worksheet_answers WHERE student_id = ?", (student_id,)).fetchone()
        pending = db.execute(
            "SELECT COUNT(*) AS c FROM ki_requests WHERE student_id = ? AND status = 'pending'",
            (student_id,),
        ).fetchone()
        attempt_pending = db.execute(
            "SELECT COUNT(*) AS c FROM attempt_round_requests WHERE student_id = ? AND status = 'pending'",
            (student_id,),
        ).fetchone()
    solved = sum(1 for row in levels if row["solved"])
    attempts = sum(row["attempts"] for row in levels)
    extra = sum(row["extra_attempts"] for row in levels)
    personal_game_unlocked = bool(student["game_unlocked"])
    return {
        "id": student["id"],
        "pseudonym": student["pseudonym"],
        "klasse": student["klasse"],
        "online": bool(student["online"]),
        "game_unlocked": class_game_unlocked() and personal_game_unlocked,
        "personal_game_unlocked": personal_game_unlocked,
        "worksheet_unlocked": bool(student["worksheet_unlocked"]),
        "solved": solved,
        "attempts": attempts,
        "extra_attempts": extra,
        "answers": answers["c"],
        "pending": pending["c"],
        "pending_attempt_rounds": attempt_pending["c"],
        "last_seen": student["last_seen"],
    }


def build_student_report(db, student) -> dict:
    level_rows = db.execute(
        "SELECT * FROM level_states WHERE student_id = ? ORDER BY level_id ASC",
        (student["id"],),
    ).fetchall()
    answer_rows = db.execute(
        "SELECT * FROM worksheet_answers WHERE student_id = ? ORDER BY task_id ASC",
        (student["id"],),
    ).fetchall()
    request_rows = db.execute(
        "SELECT * FROM ki_requests WHERE student_id = ? ORDER BY created_at ASC",
        (student["id"],),
    ).fetchall()
    attempt_request_rows = db.execute(
        "SELECT * FROM attempt_round_requests WHERE student_id = ? ORDER BY created_at ASC",
        (student["id"],),
    ).fetchall()

    levels = []
    for row in level_rows:
        level = LEVELS_BY_ID.get(row["level_id"], {})
        levels.append({
            "level_id": row["level_id"],
            "name": level.get("name", f"Level {row['level_id']}"),
            "title": level.get("title", ""),
            "difficulty": level.get("difficulty", ""),
            "attempts": row["attempts"],
            "extra_attempts": row["extra_attempts"],
            "max_attempts": MAX_LEVEL_ATTEMPTS + row["extra_attempts"],
            "solved": bool(row["solved"]),
            "hints_used": row["hints_used"],
            "updated_at": row["updated_at"],
            "history": json.loads(row["history_json"] or "[]"),
        })

    return {
        "id": student["id"],
        "pseudonym": student["pseudonym"],
        "klasse": student["klasse"],
        "created_at": student["created_at"],
        "last_seen": student["last_seen"],
        "online": bool(student["online"]),
        "game_unlocked": bool(student["game_unlocked"]),
        "class_game_unlocked": class_game_unlocked(),
        "personal_game_unlocked": bool(student["game_unlocked"]),
        "effective_game_unlocked": effective_game_unlocked(student),
        "worksheet_unlocked": bool(student["worksheet_unlocked"]),
        "levels": levels,
        "worksheet_answers": [dict(row) for row in answer_rows],
        "ki_requests": [dict(row) for row in request_rows],
        "attempt_round_requests": [dict(row) for row in attempt_request_rows],
    }


def build_session_report() -> dict:
    with connect_db() as db:
        students = db.execute("SELECT * FROM students ORDER BY created_at ASC").fetchall()
        token_rows = db.execute("SELECT * FROM token_usage ORDER BY usage_date ASC").fetchall()
        items = [build_student_report(db, student) for student in students]

    return {
        "format": "prompt_hacker_session_report",
        "schema_version": 1,
        "exported_at": now_iso(),
        "source": {
            "app": "Prompt Hacker",
            "model": MODEL_NAME,
            "gemini_configured": bool(GEMINI_API_KEY),
            "max_level_attempts": MAX_LEVEL_ATTEMPTS,
            "daily_token_limit": DAILY_TOKEN_LIMIT,
        },
        "tokens": [dict(row) for row in token_rows],
        "students": items,
    }


@app.route("/api/teacher/overview")
@require_teacher
def api_teacher_overview():
    with connect_db() as db:
        students = db.execute("SELECT id FROM students ORDER BY created_at DESC").fetchall()
        pending = db.execute(
            """
            SELECT r.*, s.pseudonym, s.klasse
            FROM ki_requests r
            JOIN students s ON s.id = r.student_id
            WHERE r.status = 'pending'
            ORDER BY r.created_at ASC
            """
        ).fetchall()
        pending_attempt_rounds = db.execute(
            """
            SELECT r.*, s.pseudonym, s.klasse
            FROM attempt_round_requests r
            JOIN students s ON s.id = r.student_id
            WHERE r.status = 'pending'
            ORDER BY r.created_at ASC
            """
        ).fetchall()
    student_items = [student_summary(row["id"]) for row in students]
    return jsonify({
        "students": student_items,
        "pending_requests": [dict(row) for row in pending],
        "pending_attempt_rounds": [dict(row) for row in pending_attempt_rounds],
        "class_game_unlocked": class_game_unlocked(),
        "tokens": {"today": get_today_tokens(), "limit": DAILY_TOKEN_LIMIT},
        "max_level_attempts": MAX_LEVEL_ATTEMPTS,
    })


@app.route("/api/teacher/student/<int:student_id>/details")
@require_teacher
def api_teacher_student_details(student_id):
    with connect_db() as db:
        student = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        if not student:
            return jsonify({"error": "Schueler:in nicht gefunden."}), 404
        return jsonify({"student": build_student_report(db, student)})


@app.route("/api/teacher/session_data", methods=["DELETE"])
@require_teacher_action
def api_delete_session_data():
    with connect_db() as db:
        db.execute("DELETE FROM ki_requests")
        db.execute("DELETE FROM attempt_round_requests")
        db.execute("DELETE FROM worksheet_answers")
        db.execute("DELETE FROM level_states")
        db.execute("DELETE FROM students")
        db.execute("DELETE FROM token_usage")

    payload = {"message": "Alle Sessiondaten wurden gelöscht."}
    socketio.emit("session_data_deleted", payload, to="lehrer_room")
    socketio.emit("session_reset", payload)
    return jsonify({"ok": True})


@app.route("/api/teacher/session_data/export_report")
@require_teacher
def api_export_session_report():
    report = build_session_report()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    return Response(
        payload,
        mimetype="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=prompt_hacker_bericht_{stamp}.json"
        },
    )


@app.route("/api/teacher/request/<int:req_id>/approve", methods=["POST"])
@require_teacher_action
def api_approve_request(req_id):
    with connect_db() as db:
        req = db.execute("SELECT * FROM ki_requests WHERE id = ?", (req_id,)).fetchone()
        if not req:
            return jsonify({"error": "Anfrage nicht gefunden."}), 404
        if req["status"] != "pending":
            return jsonify({"error": "Anfrage ist bereits entschieden."}), 400
    response = call_tutor(req["question"])
    with connect_db() as db:
        db.execute(
            "UPDATE ki_requests SET status = 'approved', response = ?, decided_at = ? WHERE id = ?",
            (response, now_iso(), req_id),
        )
    socketio.emit("ki_response", {"request_id": req_id, "response": response}, to=f"student_{req['student_id']}")
    socketio.emit("request_decided", {"request_id": req_id, "status": "approved"}, to="lehrer_room")
    return jsonify({"ok": True, "response": response})


@app.route("/api/teacher/request/<int:req_id>/deny", methods=["POST"])
@require_teacher_action
def api_deny_request(req_id):
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "Die Lehrkraft hat die Anfrage abgelehnt.").strip()
    with connect_db() as db:
        req = db.execute("SELECT * FROM ki_requests WHERE id = ?", (req_id,)).fetchone()
        if not req:
            return jsonify({"error": "Anfrage nicht gefunden."}), 404
        db.execute(
            "UPDATE ki_requests SET status = 'denied', response = ?, decided_at = ? WHERE id = ?",
            (reason, now_iso(), req_id),
        )
    socketio.emit("ki_denied", {"request_id": req_id, "reason": reason}, to=f"student_{req['student_id']}")
    socketio.emit("request_decided", {"request_id": req_id, "status": "denied"}, to="lehrer_room")
    return jsonify({"ok": True})


@app.route("/api/teacher/attempt_round/<int:req_id>/approve", methods=["POST"])
@require_teacher_action
def api_approve_attempt_round(req_id):
    with connect_db() as db:
        req = db.execute("SELECT * FROM attempt_round_requests WHERE id = ?", (req_id,)).fetchone()
        if not req:
            return jsonify({"error": "Anfrage nicht gefunden."}), 404
        if req["status"] != "pending":
            return jsonify({"error": "Anfrage ist bereits entschieden."}), 400
        db.execute(
            "UPDATE attempt_round_requests SET status = 'approved', decided_at = ? WHERE id = ?",
            (now_iso(), req_id),
        )

    state = get_level_state(req["student_id"], req["level_id"])
    state["extra_attempts"] += int(req["amount"] or MAX_LEVEL_ATTEMPTS)
    save_level_state(req["student_id"], req["level_id"], state)
    max_attempts = MAX_LEVEL_ATTEMPTS + state["extra_attempts"]
    socketio.emit(
        "attempt_request_decided",
        {
            "request_id": req_id,
            "status": "approved",
            "level_id": req["level_id"],
            "remaining_attempts": max(0, max_attempts - state["attempts"]),
        },
        to=f"student_{req['student_id']}",
    )
    socketio.emit(
        "attempts_update",
        {"level_id": req["level_id"], "remaining_attempts": max(0, max_attempts - state["attempts"])},
        to=f"student_{req['student_id']}",
    )
    socketio.emit("attempt_request_decided", {"request_id": req_id, "status": "approved"}, to="lehrer_room")
    socketio.emit("student_updated", student_summary(req["student_id"]), to="lehrer_room")
    return jsonify({"ok": True})


@app.route("/api/teacher/attempt_round/<int:req_id>/deny", methods=["POST"])
@require_teacher_action
def api_deny_attempt_round(req_id):
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "Die Lehrkraft hat die neue Versuchsrunde noch nicht freigegeben.").strip()
    with connect_db() as db:
        req = db.execute("SELECT * FROM attempt_round_requests WHERE id = ?", (req_id,)).fetchone()
        if not req:
            return jsonify({"error": "Anfrage nicht gefunden."}), 404
        if req["status"] != "pending":
            return jsonify({"error": "Anfrage ist bereits entschieden."}), 400
        db.execute(
            """
            UPDATE attempt_round_requests
            SET status = 'denied', decided_at = ?, reason = ?
            WHERE id = ?
            """,
            (now_iso(), reason, req_id),
        )
    socketio.emit(
        "attempt_request_decided",
        {"request_id": req_id, "status": "denied", "level_id": req["level_id"], "reason": reason},
        to=f"student_{req['student_id']}",
    )
    socketio.emit("attempt_request_decided", {"request_id": req_id, "status": "denied"}, to="lehrer_room")
    socketio.emit("student_updated", student_summary(req["student_id"]), to="lehrer_room")
    return jsonify({"ok": True})


@app.route("/api/teacher/student/<int:sid>/grant_attempts", methods=["POST"])
@require_teacher_action
def api_grant_attempts(sid):
    data = request.get_json(silent=True) or {}
    level_id = int(data.get("level_id") or 0)
    amount = max(1, min(10, int(data.get("amount") or MAX_LEVEL_ATTEMPTS)))
    if level_id not in LEVELS_BY_ID:
        return jsonify({"error": "Level nicht gefunden."}), 404
    state = get_level_state(sid, level_id)
    state["extra_attempts"] += amount
    save_level_state(sid, level_id, state)
    socketio.emit(
        "attempts_update",
        {"level_id": level_id, "remaining_attempts": MAX_LEVEL_ATTEMPTS + state["extra_attempts"] - state["attempts"]},
        to=f"student_{sid}",
    )
    socketio.emit("student_updated", student_summary(sid), to="lehrer_room")
    return jsonify({"ok": True})


@app.route("/api/teacher/class/toggle_game", methods=["POST"])
@require_teacher_action
def api_toggle_class_game():
    data = request.get_json(silent=True) or {}
    unlocked = bool(data.get("unlocked"))
    set_setting_bool("class_game_unlocked", unlocked)
    socketio.emit(
        "game_lock_update",
        {
            "class_game_unlocked": unlocked,
            "message": "Das Spiel wurde für die ganze Klasse freigegeben." if unlocked else "Das Spiel wurde für die ganze Klasse gesperrt.",
        },
    )
    with connect_db() as db:
        student_rows = db.execute("SELECT id FROM students").fetchall()
    for row in student_rows:
        socketio.emit("student_updated", student_summary(row["id"]), to="lehrer_room")
    return jsonify({"ok": True, "class_game_unlocked": unlocked})


@app.route("/api/teacher/student/<int:sid>/toggle_game", methods=["POST"])
@require_teacher_action
def api_toggle_game(sid):
    data = request.get_json(silent=True) or {}
    unlocked = 1 if bool(data.get("unlocked")) else 0
    with connect_db() as db:
        db.execute("UPDATE students SET game_unlocked = ? WHERE id = ?", (unlocked, sid))
    socketio.emit("game_lock_update", {"personal_game_unlocked": bool(unlocked)}, to=f"student_{sid}")
    socketio.emit("student_updated", student_summary(sid), to="lehrer_room")
    return jsonify({"ok": True})


@app.route("/api/teacher/student/<int:sid>/toggle_worksheet", methods=["POST"])
@require_teacher_action
def api_toggle_worksheet(sid):
    data = request.get_json(silent=True) or {}
    unlocked = 1 if bool(data.get("unlocked")) else 0
    with connect_db() as db:
        student = db.execute("SELECT id FROM students WHERE id = ?", (sid,)).fetchone()
        if not student:
            return jsonify({"error": "Schueler nicht gefunden."}), 404
        db.execute("UPDATE students SET worksheet_unlocked = ? WHERE id = ?", (unlocked, sid))
    socketio.emit("worksheet_lock_update", {"worksheet_unlocked": bool(unlocked)}, to=f"student_{sid}")
    socketio.emit("student_updated", student_summary(sid), to="lehrer_room")
    return jsonify({"ok": True})


@app.route("/api/levels")
@require_student
def api_levels():
    student = get_student(current_student_id())
    return jsonify({
        "levels": public_levels_for(current_student_id()),
        "game_unlocked": effective_game_unlocked(student),
        "class_game_unlocked": class_game_unlocked(),
        "personal_game_unlocked": bool(student["game_unlocked"]),
        "worksheet_unlocked": bool(student["worksheet_unlocked"]),
    })


@app.route("/api/level/<int:level_id>/attempt_round_request", methods=["POST"])
@require_student
def api_attempt_round_request(level_id):
    sid = current_student_id()
    level = LEVELS_BY_ID.get(level_id)
    if not level:
        return jsonify({"error": "Level nicht gefunden"}), 404

    state = get_level_state(sid, level_id)
    max_attempts = MAX_LEVEL_ATTEMPTS + state["extra_attempts"]
    if state["solved"]:
        return jsonify({"error": "Dieses Level ist bereits geknackt."}), 400
    if state["attempts"] < max_attempts:
        return jsonify({"error": "Du hast noch Versuche übrig."}), 400

    with connect_db() as db:
        pending = db.execute(
            """
            SELECT id FROM attempt_round_requests
            WHERE student_id = ? AND level_id = ? AND status = 'pending'
            """,
            (sid, level_id),
        ).fetchone()
        if pending:
            return jsonify({"ok": True, "request_id": pending["id"], "already_pending": True})
        cur = db.execute(
            """
            INSERT INTO attempt_round_requests (student_id, level_id, amount, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (sid, level_id, MAX_LEVEL_ATTEMPTS, now_iso()),
        )
        req_id = cur.lastrowid
        req = db.execute(
            """
            SELECT r.*, s.pseudonym, s.klasse
            FROM attempt_round_requests r
            JOIN students s ON s.id = r.student_id
            WHERE r.id = ?
            """,
            (req_id,),
        ).fetchone()

    socketio.emit("new_attempt_round_request", dict(req), to="lehrer_room")
    socketio.emit("student_updated", student_summary(sid), to="lehrer_room")
    return jsonify({"ok": True, "request_id": req_id})


@app.route("/api/level/<int:level_id>/attempt", methods=["POST"])
@require_student
def api_attempt(level_id):
    sid = current_student_id()
    student = get_student(sid)
    if not effective_game_unlocked(student):
        return jsonify({"error": "Das Spiel ist für dich gerade nicht freigegeben."}), 403
    level = LEVELS_BY_ID.get(level_id)
    if not level:
        return jsonify({"error": "Level nicht gefunden"}), 404

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Leere Nachricht"}), 400
    if len(user_message) > 2000:
        return jsonify({"error": "Nachricht zu lang (max. 2000 Zeichen)"}), 400

    state = get_level_state(sid, level_id)
    max_attempts = MAX_LEVEL_ATTEMPTS + state["extra_attempts"]
    if state["attempts"] >= max_attempts and not state["solved"]:
        with connect_db() as db:
            pending_round = db.execute(
                """
                SELECT id FROM attempt_round_requests
                WHERE student_id = ? AND level_id = ? AND status = 'pending'
                """,
                (sid, level_id),
            ).fetchone()
        return jsonify({
            "error": "Versuchslimit erreicht. Bitte frage deine Lehrkraft nach einer neuen Versuchsrunde.",
            "attempts": state["attempts"],
            "remaining_attempts": 0,
            "attempt_request_pending": bool(pending_round),
        }), 403
    if state["solved"]:
        return jsonify({"error": "Dieses Level ist bereits geknackt. Wähle das nächste Level."}), 403

    guardian_result = call_guardian(level, state["history"], user_message)
    if not guardian_result["ok"]:
        return jsonify({
            "error": guardian_result["error"],
            "attempts": state["attempts"],
            "remaining_attempts": max(0, max_attempts - state["attempts"]),
            "max_attempts": max_attempts,
            "not_counted": True,
            "finish_reason": guardian_result.get("finish_reason"),
        }), 502

    state["history"].append({"role": "user", "content": user_message})
    state["attempts"] += 1
    ai_response = guardian_result["text"]
    password = level["password"]
    success_stage = None
    if check_direct(password, ai_response):
        success_stage = "direkt"
    elif check_separated(password, ai_response):
        success_stage = "separated"
    elif check_character_sequence(password, ai_response):
        success_stage = "character_sequence"
    elif check_direct(password, user_message) or check_separated(password, user_message):
        success_stage = "user_guess"
    elif level_id == 3 and gemini_client and check_with_judge(password, ai_response):
        success_stage = "judge"

    technique = None
    if success_stage:
        state["solved"] = True
        if success_stage == "user_guess":
            ai_response = success_confirmation_text(level)
            technique = "Passwort erschlossen und bestätigen lassen"
        else:
            technique = detect_technique(password, ai_response, user_message)

    state["history"].append({"role": "model", "content": ai_response})

    if len(state["history"]) > MAX_HISTORY_TURNS * 2:
        state["history"] = state["history"][-MAX_HISTORY_TURNS * 2:]

    save_level_state(sid, level_id, state)
    with connect_db() as db:
        db.execute("UPDATE students SET last_seen = ? WHERE id = ?", (now_iso(), sid))

    socketio.emit("student_updated", student_summary(sid), to="lehrer_room")

    payload = {
        "ai_response": ai_response,
        "attempts": state["attempts"],
        "remaining_attempts": max(0, max_attempts - state["attempts"]),
        "max_attempts": max_attempts,
        "solved": state["solved"],
        "hint_available": (state["attempts"] - state["hints_used"] * HINT_INTERVAL) >= HINT_INTERVAL,
        "hints_used": state["hints_used"],
        "max_hints": len(level["hints"]),
    }
    if success_stage and technique:
        payload["just_solved"] = True
        payload["technique"] = technique
        payload["lesson"] = level["lesson"]
    return jsonify(payload)


@app.route("/api/level/<int:level_id>/hint", methods=["POST"])
@require_student
def api_hint(level_id):
    sid = current_student_id()
    level = LEVELS_BY_ID.get(level_id)
    if not level:
        return jsonify({"error": "Level nicht gefunden"}), 404
    state = get_level_state(sid, level_id)
    hints = level["hints"]
    if state["hints_used"] >= len(hints):
        return jsonify({"error": "Keine weiteren Hinweise verfügbar.", "hints_used": state["hints_used"]}), 400
    required_attempts = (state["hints_used"] + 1) * HINT_INTERVAL
    if state["attempts"] < required_attempts:
        return jsonify({
            "error": f"Tipp {state['hints_used'] + 1} gibt es nach {required_attempts} Versuchen.",
            "required_attempts": required_attempts,
            "current_attempts": state["attempts"],
        }), 403
    hint = hints[state["hints_used"]]
    state["hints_used"] += 1
    save_level_state(sid, level_id, state)
    return jsonify({"hint": hint, "hint_number": state["hints_used"], "max_hints": len(hints)})


@app.route("/api/level/<int:level_id>/reflection", methods=["POST"])
@require_student
def api_save_level_reflection(level_id):
    sid = current_student_id()
    if level_id not in LEVELS_BY_ID:
        return jsonify({"error": "Level nicht gefunden"}), 404
    state = get_level_state(sid, level_id)
    if not state["solved"]:
        return jsonify({"error": "Reflexion ist erst nach einem geknackten Level möglich."}), 403

    data = request.get_json(silent=True) or {}
    reflection = (data.get("reflection") or "").strip()
    if len(reflection) < 10:
        return jsonify({"error": "Schreibe mindestens einen kurzen Satz zu deiner Erfolgsstrategie."}), 400
    if len(reflection) > 2500:
        return jsonify({"error": "Text zu lang (max. 2500 Zeichen)."}), 400

    task_id = f"level_{level_id}_strategy"
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO worksheet_answers (student_id, task_id, niveau, answer, updated_at)
            VALUES (?, ?, 'reflexion', ?, ?)
            ON CONFLICT(student_id, task_id) DO UPDATE SET
                niveau = excluded.niveau,
                answer = excluded.answer,
                updated_at = excluded.updated_at
            """,
            (sid, task_id, reflection, now_iso()),
        )
    socketio.emit("student_updated", student_summary(sid), to="lehrer_room")
    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/worksheet/answers")
@require_student
def api_get_answers():
    student = get_student(current_student_id())
    if not student["worksheet_unlocked"]:
        return jsonify({"answers": {}, "locked": True})
    with connect_db() as db:
        rows = db.execute(
            "SELECT task_id, niveau, answer, updated_at FROM worksheet_answers WHERE student_id = ?",
            (current_student_id(),),
        ).fetchall()
    return jsonify({"answers": {row["task_id"]: dict(row) for row in rows}})


@app.route("/api/worksheet/answer", methods=["POST"])
@require_student
def api_save_answer():
    student = get_student(current_student_id())
    if not student["worksheet_unlocked"]:
        return jsonify({"error": "Das Arbeitsblatt ist noch nicht freigegeben."}), 403
    data = request.get_json(silent=True) or {}
    task_id = (data.get("task_id") or "").strip()
    niveau = (data.get("niveau") or "a").strip().lower()
    answer = (data.get("answer") or "").strip()
    if task_id not in {"task1", "task2", "task3", "task4", "task5"}:
        return jsonify({"error": "Unbekannte Aufgabe."}), 400
    if niveau not in {"a", "b", "c"}:
        return jsonify({"error": "Unbekanntes Niveau."}), 400
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO worksheet_answers (student_id, task_id, niveau, answer, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, task_id) DO UPDATE SET
                niveau = excluded.niveau,
                answer = excluded.answer,
                updated_at = excluded.updated_at
            """,
            (current_student_id(), task_id, niveau, answer[:4000], now_iso()),
        )
        db.execute("UPDATE students SET last_seen = ? WHERE id = ?", (now_iso(), current_student_id()))
    socketio.emit("student_updated", student_summary(current_student_id()), to="lehrer_room")
    return jsonify({"ok": True})


@app.route("/api/ki/request", methods=["POST"])
@require_student
def api_ki_request():
    student_row = get_student(current_student_id())
    if not student_row["worksheet_unlocked"]:
        return jsonify({"error": "Der KI-Tutor ist erst nach Freigabe des Arbeitsblatts aktiv."}), 403
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if len(question) < 3:
        return jsonify({"error": "Bitte erst eine Frage eingeben."}), 400
    if len(question) > 1200:
        return jsonify({"error": "Die Frage ist zu lang."}), 400
    with connect_db() as db:
        cur = db.execute(
            """
            INSERT INTO ki_requests (student_id, kind, question, status, created_at)
            VALUES (?, 'worksheet_chat', ?, 'pending', ?)
            """,
            (current_student_id(), question, now_iso()),
        )
        req_id = cur.lastrowid
        student = db.execute("SELECT pseudonym, klasse FROM students WHERE id = ?", (current_student_id(),)).fetchone()
    payload = {
        "id": req_id,
        "student_id": current_student_id(),
        "pseudonym": student["pseudonym"],
        "klasse": student["klasse"],
        "kind": "worksheet_chat",
        "question": question,
        "status": "pending",
        "created_at": now_iso(),
    }
    socketio.emit("new_ki_request", payload, to="lehrer_room")
    return jsonify({"ok": True, "request_id": req_id})


@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "gemini_configured": bool(GEMINI_API_KEY),
        "model": MODEL_NAME,
        "levels": len(LEVELS),
        "tokens_today": get_today_tokens(),
        "daily_token_limit": DAILY_TOKEN_LIMIT,
    })


@socketio.on("connect")
def socket_connect():
    sid = current_student_id()
    if sid:
        join_room(f"student_{sid}")
        with connect_db() as db:
            db.execute("UPDATE students SET online = 1, last_seen = ? WHERE id = ?", (now_iso(), sid))
        socketio.emit("student_updated", student_summary(sid), to="lehrer_room")


@socketio.on("disconnect")
def socket_disconnect():
    sid = current_student_id()
    if sid:
        with connect_db() as db:
            db.execute("UPDATE students SET online = 0, last_seen = ? WHERE id = ?", (now_iso(), sid))
        socketio.emit("student_offline", {"id": sid}, to="lehrer_room")


@socketio.on("teacher_join")
def teacher_join():
    if session.get("is_teacher"):
        join_room("lehrer_room")
        emit("token_update", {"today": get_today_tokens(), "limit": DAILY_TOKEN_LIMIT})


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
