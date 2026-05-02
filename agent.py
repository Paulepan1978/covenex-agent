"""
╔══════════════════════════════════════════════════════════════╗
║  COVENEX RECRUITER — Autonomer KI-Agent                     ║
║  CYQUEO GmbH · IT Security Recruiting                       ║
║                                                              ║
║  Was dieser Agent tut:                                       ║
║  • Läuft jeden Morgen automatisch (Standard: 07:30 Uhr)     ║
║  • Prüft Gmail auf neue Kandidaten-Antworten                ║
║  • Beantwortet Antworten automatisch                        ║
║  • Sendet fällige Follow-ups                                ║
║  • Schreibt neue Kandidaten an                              ║
║  • Bucht Termine in Google Calendar                         ║
║  • Schickt dir einen Tages-Report per E-Mail                ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import json
import time
import logging
import schedule
from datetime import datetime, timedelta
from typing import Optional
import anthropic
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pickle

# ── LOGGING ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('agent.log')
    ]
)
log = logging.getLogger("covenex-agent")

# ══════════════════════════════════════════════════════════════
# KONFIGURATION — hier alles eintragen
# ══════════════════════════════════════════════════════════════
CONFIG = {
    # Anthropic API Key (von console.anthropic.com)
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "HIER_API_KEY_EINTRAGEN"),

    # Gmail / Google OAuth (aus Google Cloud Console)
    "GMAIL_CLIENT_ID":     os.getenv("GMAIL_CLIENT_ID", ""),
    "GMAIL_CLIENT_SECRET": os.getenv("GMAIL_CLIENT_SECRET", ""),
    "GMAIL_SENDER_EMAIL":  os.getenv("GMAIL_SENDER_EMAIL", "recruiting@cyqueo.com"),
    "GMAIL_SENDER_NAME":   os.getenv("GMAIL_SENDER_NAME", "CYQUEO Recruiting"),

    # Report-Empfänger (deine E-Mail)
    "REPORT_EMAIL": os.getenv("REPORT_EMAIL", "deine@email.de"),

    # Wann läuft der Agent täglich (24h Format)
    "DAILY_RUN_TIME": os.getenv("DAILY_RUN_TIME", "07:30"),

    # Maximale E-Mails pro Tag (Sicherheitslimit)
    "MAX_EMAILS_PER_DAY": int(os.getenv("MAX_EMAILS_PER_DAY", "30")),

    # Follow-up Timing
    "FOLLOWUP_1_DAYS": int(os.getenv("FOLLOWUP_1_DAYS", "5")),
    "FOLLOWUP_2_DAYS": int(os.getenv("FOLLOWUP_2_DAYS", "10")),
    "FOLLOWUP_3_DAYS": int(os.getenv("FOLLOWUP_3_DAYS", "15")),
}

# ══════════════════════════════════════════════════════════════
# CYQUEO VERTRIEBS-DNA — Eingebettet in alle KI-Entscheidungen
# ══════════════════════════════════════════════════════════════
CYQUEO_SYSTEM_PROMPT = """Du bist der autonome Recruiting-Agent für CYQUEO GmbH (cyqueo.com), München.

CYQUEO VERTRIEBS-DNA:
- Oldschool Hunter-Kultur: 10 Entscheidergespräche/Tag, 5 Opportunities/Woche
- 98% Customer Retention, CSAT 9.7/10, Kununu Top Company 2022–2026
- 20+ Jahre MSSP, 1,6 Mio. geschützte Mitarbeiter, 17 Partner-Hersteller
- Homeoffice deutschlandweit, regionale Kundenbetreuung

IDEALE KANDIDATEN (Priorität):
1. IT-Reseller: Bechtle, Computacenter, Cancom, Axians, Controlware, Conscia, SHD
2. IT-Distribution: Ingram Micro, Arrow, Also, TD Synnex, Exclusive Networks
3. Komplexer IT-Lösungsvertrieb (lösungsorientiert, aktiv)

VERMEIDEN:
- Hersteller (Zscaler, CrowdStrike, SentinelOne, Palo Alto) — Gehalt inkompatibel
- Senior/Enterprise/Strategic/Global Account Manager — zu strategisch
- Kein IT-Background

OFFENE STELLEN:
- Account Manager IT Security (70–90k + Provision, Hunter-Profil)
- Vertriebsinnendienst IT Security (45–60k + Bonus)

Antworte immer auf Deutsch. Bei E-Mails: Betreff + vollständiger Text.
Bei Entscheidungen: klares JA/NEIN + kurze Begründung."""


# ══════════════════════════════════════════════════════════════
# KANDIDATEN-DATENBANK (JSON-Datei als einfache DB)
# ══════════════════════════════════════════════════════════════
DB_FILE = "candidates.json"

def load_db() -> dict:
    """Lädt die Kandidaten-Datenbank."""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"candidates": [], "sent_today": 0, "last_reset": str(datetime.today().date())}

def save_db(db: dict):
    """Speichert die Kandidaten-Datenbank."""
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def reset_daily_counter(db: dict) -> dict:
    """Setzt den Tages-Counter zurück wenn ein neuer Tag beginnt."""
    today = str(datetime.today().date())
    if db.get("last_reset") != today:
        db["sent_today"] = 0
        db["last_reset"] = today
        log.info("Tages-Counter zurückgesetzt")
    return db


# ══════════════════════════════════════════════════════════════
# ANTHROPIC KI-MODUL
# ══════════════════════════════════════════════════════════════
def ask_ai(prompt: str, max_tokens: int = 800) -> str:
    """Schickt eine Anfrage an Claude und gibt die Antwort zurück."""
    client = anthropic.Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=CYQUEO_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        log.error(f"KI-Fehler: {e}")
        return f"KI-Fehler: {str(e)}"


# ══════════════════════════════════════════════════════════════
# GMAIL-MODUL
# ══════════════════════════════════════════════════════════════
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar'
]

def get_google_services():
    """Authentifiziert mit Google und gibt Gmail + Calendar Services zurück."""
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Erstmalige Authentifizierung
            client_config = {
                "installed": {
                    "client_id": CONFIG["GMAIL_CLIENT_ID"],
                    "client_secret": CONFIG["GMAIL_CLIENT_SECRET"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as f:
            pickle.dump(creds, f)

    gmail = build('gmail', 'v1', credentials=creds)
    calendar = build('calendar', 'v3', credentials=creds)
    return gmail, calendar


def send_email(gmail_service, to: str, subject: str, body: str,
               reply_to_thread: Optional[str] = None) -> bool:
    """Sendet eine E-Mail via Gmail API."""
    db = load_db()
    db = reset_daily_counter(db)

    if db["sent_today"] >= CONFIG["MAX_EMAILS_PER_DAY"]:
        log.warning(f"Tageslimit erreicht ({CONFIG['MAX_EMAILS_PER_DAY']} E-Mails)")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['To'] = to
        msg['From'] = f"{CONFIG['GMAIL_SENDER_NAME']} <{CONFIG['GMAIL_SENDER_EMAIL']}>"
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
        body_dict = {'raw': raw}
        if reply_to_thread:
            body_dict['threadId'] = reply_to_thread

        gmail_service.users().messages().send(
            userId='me', body=body_dict
        ).execute()

        db["sent_today"] += 1
        save_db(db)
        log.info(f"E-Mail gesendet an {to} | Betreff: {subject}")
        return True

    except Exception as e:
        log.error(f"Gmail-Fehler beim Senden an {to}: {e}")
        return False


def check_inbox(gmail_service) -> list:
    """Prüft den Posteingang auf neue Kandidaten-Antworten."""
    try:
        results = gmail_service.users().messages().list(
            userId='me',
            q=f'to:{CONFIG["GMAIL_SENDER_EMAIL"]} is:unread',
            maxResults=50
        ).execute()

        messages = results.get('messages', [])
        replies = []

        for msg_ref in messages:
            msg = gmail_service.users().messages().get(
                userId='me', id=msg_ref['id'], format='full'
            ).execute()

            headers = {h['name']: h['value'] for h in msg['payload'].get('headers', [])}
            subject = headers.get('Subject', '')
            sender = headers.get('From', '')
            thread_id = msg.get('threadId', '')

            # E-Mail-Body extrahieren
            body = extract_email_body(msg['payload'])

            replies.append({
                'id': msg_ref['id'],
                'thread_id': thread_id,
                'subject': subject,
                'from': sender,
                'body': body[:1000],  # Max 1000 Zeichen
                'date': headers.get('Date', '')
            })

            # Als gelesen markieren
            gmail_service.users().messages().modify(
                userId='me', id=msg_ref['id'],
                body={'removeLabelIds': ['UNREAD']}
            ).execute()

        log.info(f"{len(replies)} neue E-Mail(s) im Posteingang")
        return replies

    except Exception as e:
        log.error(f"Gmail Inbox-Fehler: {e}")
        return []


def extract_email_body(payload) -> str:
    """Extrahiert den Textinhalt einer E-Mail."""
    body = ""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                data = part['body'].get('data', '')
                if data:
                    body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
                    break
    elif payload['body'].get('data'):
        body = base64.urlsafe_b64decode(
            payload['body']['data']
        ).decode('utf-8', errors='replace')
    return body


# ══════════════════════════════════════════════════════════════
# GOOGLE CALENDAR MODUL
# ══════════════════════════════════════════════════════════════
def create_calendar_event(calendar_service, candidate_name: str,
                           candidate_email: str, date_str: str,
                           time_str: str, job_title: str) -> bool:
    """Erstellt einen Kalender-Termin mit Google Meet Link."""
    try:
        # Datum und Zeit parsen
        start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=45)

        event = {
            'summary': f'Interview: {candidate_name} — {job_title}',
            'description': f'CYQUEO Recruiting Gespräch\nKandidat: {candidate_name}\nStelle: {job_title}',
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Europe/Berlin'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Europe/Berlin'},
            'attendees': [
                {'email': candidate_email},
                {'email': CONFIG['GMAIL_SENDER_EMAIL']}
            ],
            'conferenceData': {
                'createRequest': {'requestId': f'covenex-{int(time.time())}'}
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 1440},  # 24h vorher
                    {'method': 'popup', 'minutes': 30}
                ]
            }
        }

        calendar_service.events().insert(
            calendarId='primary',
            body=event,
            conferenceDataVersion=1,
            sendUpdates='all'
        ).execute()

        log.info(f"Kalender-Termin erstellt: {candidate_name} am {date_str} {time_str}")
        return True

    except Exception as e:
        log.error(f"Calendar-Fehler: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# AGENT-AKTIONEN
# ══════════════════════════════════════════════════════════════

def process_inbox_replies(gmail_service, calendar_service, replies: list) -> list:
    """
    Verarbeitet alle neuen E-Mail-Antworten von Kandidaten.
    KI entscheidet was zu tun ist und antwortet automatisch.
    """
    actions_taken = []

    for reply in replies:
        log.info(f"Verarbeite Antwort von: {reply['from']}")

        # KI analysiert die Antwort
        analysis_prompt = f"""Analysiere diese E-Mail-Antwort eines Kandidaten auf eine CYQUEO Recruiting-Anfrage.

Von: {reply['from']}
Betreff: {reply['subject']}
Nachricht: {reply['body']}

Entscheide:
1. INTERESSE (positiv, will mehr wissen) → erstelle Antwortmail mit 3 Terminvorschlägen für nächste Woche
2. RÜCKFRAGE (hat Fragen zur Stelle/Gehalt/Team) → beantworte die Fragen professionell
3. ABLEHNUNG (kein Interesse) → höfliche Antwort, Tür offen halten
4. TERMINBESTÄTIGUNG (bestätigt einen Termin) → Bestätigung + Google Meet Link ankündigen

Antworte im Format:
TYP: [INTERESSE/RÜCKFRAGE/ABLEHNUNG/TERMINBESTÄTIGUNG]
BETREFF: [E-Mail Betreff]
MAIL: [vollständige E-Mail auf Deutsch]"""

        ai_response = ask_ai(analysis_prompt, max_tokens=600)
        lines = ai_response.strip().split('\n')

        reply_type = ""
        subject = ""
        mail_lines = []
        in_mail = False

        for line in lines:
            if line.startswith("TYP:"):
                reply_type = line.replace("TYP:", "").strip()
            elif line.startswith("BETREFF:"):
                subject = line.replace("BETREFF:", "").strip()
            elif line.startswith("MAIL:"):
                in_mail = True
                mail_content = line.replace("MAIL:", "").strip()
                if mail_content:
                    mail_lines.append(mail_content)
            elif in_mail:
                mail_lines.append(line)

        mail_body = '\n'.join(mail_lines).strip()

        if not subject:
            subject = f"Re: {reply['subject']}"

        # E-Mail-Adresse aus "From" extrahieren
        sender_email = reply['from']
        if '<' in sender_email:
            sender_email = sender_email.split('<')[1].replace('>', '').strip()

        # Antwort senden
        if mail_body and sender_email:
            success = send_email(
                gmail_service,
                to=sender_email,
                subject=subject,
                body=mail_body,
                reply_to_thread=reply['thread_id']
            )
            if success:
                actions_taken.append({
                    'action': f'Antwort gesendet ({reply_type})',
                    'candidate': reply['from'],
                    'detail': subject
                })

        # Bei Terminbestätigung: Google Calendar Eintrag
        if reply_type == "TERMINBESTÄTIGUNG":
            # KI extrahiert Datum und Zeit
            date_prompt = f"""Extrahiere Datum und Uhrzeit aus dieser E-Mail. Antworte NUR im Format:
DATUM: YYYY-MM-DD
UHRZEIT: HH:MM

E-Mail: {reply['body']}"""
            date_response = ask_ai(date_prompt, max_tokens=50)
            try:
                date_line = [l for l in date_response.split('\n') if 'DATUM:' in l][0]
                time_line = [l for l in date_response.split('\n') if 'UHRZEIT:' in l][0]
                date_val = date_line.replace('DATUM:', '').strip()
                time_val = time_line.replace('UHRZEIT:', '').strip()
                create_calendar_event(
                    calendar_service,
                    candidate_name=reply['from'],
                    candidate_email=sender_email,
                    date_str=date_val,
                    time_str=time_val,
                    job_title="IT Security Sales"
                )
                actions_taken.append({
                    'action': 'Kalender-Termin erstellt',
                    'candidate': reply['from'],
                    'detail': f"{date_val} {time_val}"
                })
            except Exception as e:
                log.warning(f"Konnte Termin nicht automatisch erstellen: {e}")

    return actions_taken


def send_due_followups(gmail_service, db: dict) -> list:
    """
    Sendet alle fälligen Follow-up E-Mails.
    Prüft für jeden Kandidaten ob Follow-up 1, 2 oder 3 fällig ist.
    """
    actions_taken = []
    today = datetime.today().date()

    for candidate in db.get("candidates", []):
        # Überspringen wenn bereits geantwortet oder abgelehnt
        if candidate.get("status") in ["replied", "declined", "booked"]:
            continue

        last_contact = datetime.strptime(
            candidate.get("last_contact", str(today)), "%Y-%m-%d"
        ).date()
        days_since = (today - last_contact).days
        followup_num = candidate.get("followup_count", 0)

        # Prüfe ob Follow-up fällig
        is_due = (
            (followup_num == 0 and days_since >= CONFIG["FOLLOWUP_1_DAYS"]) or
            (followup_num == 1 and days_since >= CONFIG["FOLLOWUP_2_DAYS"]) or
            (followup_num == 2 and days_since >= CONFIG["FOLLOWUP_3_DAYS"])
        )

        if not is_due:
            continue

        # KI generiert Follow-up
        followup_prompt = f"""Erstelle Follow-up E-Mail Nr. {followup_num + 1} für diesen Kandidaten.

Kandidat: {candidate.get('name', 'Kandidat')}
Aktuelle Firma: {candidate.get('current_company', 'unbekannt')}
Stelle: {candidate.get('job_title', 'Account Manager IT Security')}
Tage seit letztem Kontakt: {days_since}

Follow-up {followup_num + 1} von 3:
- 1. Follow-up (Tag 5): Freundliche Erinnerung, konkreter Mehrwert
- 2. Follow-up (Tag 10): Anderer Blickwinkel, CYQUEO Vorteil betonen
- 3. Follow-up (Tag 15): Letzter Versuch, Tür offen lassen

Format:
BETREFF: [Betreff]
MAIL: [vollständige Mail]"""

        ai_response = ask_ai(followup_prompt, max_tokens=400)
        lines = ai_response.strip().split('\n')
        subject = ""
        mail_lines = []
        in_mail = False

        for line in lines:
            if line.startswith("BETREFF:"):
                subject = line.replace("BETREFF:", "").strip()
            elif line.startswith("MAIL:"):
                in_mail = True
                c = line.replace("MAIL:", "").strip()
                if c:
                    mail_lines.append(c)
            elif in_mail:
                mail_lines.append(line)

        mail_body = '\n'.join(mail_lines).strip()

        if mail_body and candidate.get("email"):
            success = send_email(
                gmail_service,
                to=candidate["email"],
                subject=subject or f"Follow-up: IT Security Position bei CYQUEO",
                body=mail_body
            )
            if success:
                candidate["followup_count"] = followup_num + 1
                candidate["last_contact"] = str(today)
                if followup_num >= 2:
                    candidate["status"] = "sequence_complete"
                actions_taken.append({
                    'action': f'Follow-up {followup_num + 1} gesendet',
                    'candidate': candidate.get('name', candidate.get('email')),
                    'detail': subject
                })

    save_db(db)
    return actions_taken


def send_daily_report(gmail_service, all_actions: list, run_time: str):
    """Sendet den täglichen Zusammenfassungs-Report."""
    if gmail_service is None:
        log.info("Gmail nicht verbunden — Report wird nur geloggt")
        log.info(f"Report: {len(all_actions)} Aktionen heute")
        return
    today = datetime.today().strftime("%d.%m.%Y")

    if not all_actions:
        report_body = f"""Guten Morgen,

der Covenex Recruiter Agent hat heute ({today}) seine Runde gemacht.

Heute waren keine Aktionen nötig — alle Kandidaten sind auf dem aktuellen Stand.

Nächster Lauf: morgen um {CONFIG['DAILY_RUN_TIME']} Uhr.

Covenex Recruiter Agent"""
    else:
        actions_text = '\n'.join([
            f"  • {a['action']}: {a['candidate']} ({a.get('detail', '')})"
            for a in all_actions
        ])
        report_body = f"""Guten Morgen,

der Covenex Recruiter Agent hat heute ({today}) folgende Aktionen durchgeführt:

{actions_text}

Gesamt: {len(all_actions)} Aktionen

Nächster Lauf: morgen um {CONFIG['DAILY_RUN_TIME']} Uhr.
Kandidaten in Pipeline: Bitte im Covenex Recruiter Dashboard prüfen.

Covenex Recruiter Agent"""

    send_email(
        gmail_service,
        to=CONFIG["REPORT_EMAIL"],
        subject=f"Covenex Agent Report · {today} · {len(all_actions)} Aktionen",
        body=report_body
    )
    log.info(f"Tages-Report gesendet an {CONFIG['REPORT_EMAIL']}")


# ══════════════════════════════════════════════════════════════
# HAUPT-AGENT-LOOP
# ══════════════════════════════════════════════════════════════
def run_agent():
    """
    Der Haupt-Loop des Agenten.
    Läuft täglich automatisch und führt alle Aktionen durch.
    """
    start_time = datetime.now().strftime("%H:%M:%S")
    log.info("=" * 60)
    log.info(f"COVENEX AGENT STARTET — {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    log.info("=" * 60)

    all_actions = []

    # Konfiguration prüfen
    if CONFIG["ANTHROPIC_API_KEY"] == "HIER_API_KEY_EINTRAGEN":
        log.error("❌ Anthropic API Key nicht eingetragen!")
        return

    if not CONFIG["GMAIL_CLIENT_ID"]:
        log.error("❌ Gmail Client ID nicht eingetragen!")
        return

    try:
        # Google Services initialisieren (optional beim ersten Start)
        log.info("Google Services verbinden...")
        try:
            gmail_service, calendar_service = get_google_services()
            log.info("✓ Gmail + Calendar verbunden")
        except Exception as ge:
            log.warning(f"Gmail nicht verfügbar (noch nicht eingerichtet): {ge}")
            log.info("Agent läuft im Demo-Modus ohne Gmail")
            send_daily_report(None, [], start_time)
            return

        # Datenbank laden
        db = load_db()
        db = reset_daily_counter(db)

        # ── SCHRITT 1: Posteingang prüfen ──────────────────
        log.info("SCHRITT 1: Posteingang prüfen...")
        replies = check_inbox(gmail_service)
        if replies:
            inbox_actions = process_inbox_replies(gmail_service, calendar_service, replies)
            all_actions.extend(inbox_actions)
            log.info(f"  → {len(inbox_actions)} Antworten bearbeitet")
        else:
            log.info("  → Keine neuen Antworten")

        # ── SCHRITT 2: Follow-ups senden ───────────────────
        log.info("SCHRITT 2: Fällige Follow-ups prüfen...")
        followup_actions = send_due_followups(gmail_service, db)
        all_actions.extend(followup_actions)
        log.info(f"  → {len(followup_actions)} Follow-ups gesendet")

        # ── SCHRITT 3: Tages-Report ────────────────────────
        log.info("SCHRITT 3: Tages-Report senden...")
        send_daily_report(gmail_service, all_actions, start_time)

        log.info("=" * 60)
        log.info(f"AGENT FERTIG — {len(all_actions)} Aktionen gesamt")
        log.info("=" * 60)

    except Exception as e:
        log.error(f"Agent-Fehler: {e}", exc_info=True)


# ══════════════════════════════════════════════════════════════
# API ENDPUNKTE (für das Frontend / Dashboard)
# ══════════════════════════════════════════════════════════════
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Erlaubt Aufrufe vom Frontend

@app.route('/health', methods=['GET'])
def health():
    """Health Check — Railway prüft ob der Service läuft."""
    return jsonify({
        "status": "running",
        "time": datetime.now().isoformat(),
        "next_run": CONFIG["DAILY_RUN_TIME"]
    })

@app.route('/status', methods=['GET'])
def status():
    """Gibt aktuellen Status zurück."""
    db = load_db()
    return jsonify({
        "candidates_total": len(db.get("candidates", [])),
        "emails_sent_today": db.get("sent_today", 0),
        "max_per_day": CONFIG["MAX_EMAILS_PER_DAY"],
        "daily_run_time": CONFIG["DAILY_RUN_TIME"],
        "last_reset": db.get("last_reset", "")
    })

@app.route('/run-now', methods=['POST'])
def run_now():
    """Manueller Trigger — startet den Agent sofort."""
    log.info("Manueller Agent-Start via API")
    run_agent()
    return jsonify({"message": "Agent erfolgreich ausgeführt", "time": datetime.now().isoformat()})

@app.route('/candidates', methods=['GET'])
def get_candidates():
    """Gibt alle Kandidaten zurück."""
    db = load_db()
    return jsonify(db.get("candidates", []))

@app.route('/candidates', methods=['POST'])
def add_candidate():
    """Fügt einen neuen Kandidaten hinzu."""
    data = request.json
    db = load_db()
    candidate = {
        "id": f"cand_{int(time.time())}",
        "name": data.get("name", ""),
        "email": data.get("email", ""),
        "current_company": data.get("current_company", ""),
        "job_title": data.get("job_title", "Account Manager IT Security"),
        "region": data.get("region", ""),
        "source": data.get("source", ""),
        "match_score": data.get("match_score", 0),
        "status": "new",
        "added_date": str(datetime.today().date()),
        "last_contact": "",
        "followup_count": 0
    }
    db["candidates"].append(candidate)
    save_db(db)
    log.info(f"Kandidat hinzugefügt: {candidate['name']}")
    return jsonify({"message": "Kandidat hinzugefügt", "id": candidate["id"]})

@app.route('/ask', methods=['POST'])
def ask():
    """KI-Anfrage direkt ans Backend."""
    data = request.json
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "Kein Prompt"}), 400
    response = ask_ai(prompt)
    return jsonify({"response": response})


# ══════════════════════════════════════════════════════════════
# START
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import threading

    log.info("╔══════════════════════════════════════╗")
    log.info("║  COVENEX RECRUITER AGENT startet    ║")
    log.info("╚══════════════════════════════════════╝")

    # Täglichen Job schedulen
    run_time = CONFIG["DAILY_RUN_TIME"]
    schedule.every().day.at(run_time).do(run_agent)
    log.info(f"Agent läuft täglich um {run_time} Uhr")

    # Scheduler in separatem Thread starten
    def run_scheduler():
        log.info("Scheduler gestartet")
        while True:
            schedule.run_pending()
            time.sleep(60)

    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

    # Einmalig beim Start ausführen (optional)
    # run_agent()

    # Flask API starten
    port = int(os.getenv("PORT", 8080))
    log.info(f"API läuft auf Port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
