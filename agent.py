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
CORS(app)
app.secret_key = os.getenv('SECRET_KEY', 'covenex-secret-2025')



# ── GMAIL OAUTH ROUTES ─────────────────────────────────────
REDIRECT_URI = "https://covenex-agent.onrender.com/oauth/callback"

def get_oauth_url():
    """Baut die Google OAuth URL manuell zusammen — ohne PKCE."""
    import urllib.parse
    params = {
        "client_id": CONFIG["GMAIL_CLIENT_ID"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent"
    }
    return "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)

@app.route("/auth/start", methods=["GET"])
def auth_start():
    """Startet den Gmail OAuth Flow."""
    if not CONFIG["GMAIL_CLIENT_ID"]:
        return "GMAIL_CLIENT_ID fehlt!", 400
    import flask
    return flask.redirect(get_oauth_url())

@app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    """Empfängt den OAuth Callback von Google und tauscht Code gegen Token."""
    import flask, requests as req_lib
    code = flask.request.args.get("code")
    error = flask.request.args.get("error")
    
    if error:
        return f"Google hat abgelehnt: {error}", 400
    if not code:
        return "Kein Code erhalten!", 400
    
    # Token direkt via requests holen — kein PKCE Problem
    try:
        resp = req_lib.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": CONFIG["GMAIL_CLIENT_ID"],
            "client_secret": CONFIG["GMAIL_CLIENT_SECRET"],
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code"
        })
        token_data = resp.json()
        
        if "error" in token_data:
            return f"Token Fehler: {token_data}", 400
        
        # Als Google Credentials speichern
        from google.oauth2.credentials import Credentials
        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CONFIG["GMAIL_CLIENT_ID"],
            client_secret=CONFIG["GMAIL_CLIENT_SECRET"],
            scopes=SCOPES
        )
        with open("token.pickle", "wb") as f:
            pickle.dump(creds, f)
        
        log.info("✓ Gmail OAuth erfolgreich! Token gespeichert.")
        return """
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#0f1c2e;color:white">
        <h1 style="color:#27ae60;font-size:48px">✓</h1>
        <h2 style="color:white;margin-bottom:16px">Gmail erfolgreich verbunden!</h2>
        <p style="color:rgba(255,255,255,0.6)">Der Covenex Agent kann jetzt E-Mails senden und empfangen.</p>
        <p style="margin-top:30px">
          <a href="/" style="background:#185FA5;color:white;padding:14px 28px;border-radius:10px;text-decoration:none;font-size:14px">
            Zum Dashboard →
          </a>
        </p>
        </body></html>
        """, 200
        
    except Exception as e:
        log.error(f"OAuth Callback Fehler: {e}")
        return f"Fehler: {str(e)}", 500


# ── DASHBOARD ROUTE ────────────────────────────────────────
DASHBOARD_HTML = base64.b64decode("PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImRlIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9IlVURi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9IndpZHRoPWRldmljZS13aWR0aCwgaW5pdGlhbC1zY2FsZT0xLjAiPgo8dGl0bGU+Q292ZW5leCBBZ2VudCDCtyBEYXNoYm9hcmQ8L3RpdGxlPgo8bGluayBocmVmPSJodHRwczovL2ZvbnRzLmdvb2dsZWFwaXMuY29tL2NzczI/ZmFtaWx5PURNK1NhbnM6d2dodEAzMDA7NDAwOzUwMDs2MDAmZGlzcGxheT1zd2FwIiByZWw9InN0eWxlc2hlZXQiPgo8c3R5bGU+Cip7Ym94LXNpemluZzpib3JkZXItYm94O21hcmdpbjowO3BhZGRpbmc6MH0KYm9keXtmb250LWZhbWlseTonRE0gU2Fucycsc3lzdGVtLXVpLHNhbnMtc2VyaWY7YmFja2dyb3VuZDojMGYxYzJlO2NvbG9yOndoaXRlO21pbi1oZWlnaHQ6MTAwdmg7cGFkZGluZzoyNHB4fQoucGFnZXttYXgtd2lkdGg6OTAwcHg7bWFyZ2luOjAgYXV0b30KCi8qIEhFQURFUiAqLwouaGVhZGVye2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47bWFyZ2luLWJvdHRvbToyOHB4fQoubG9nb3tmb250LXNpemU6MThweDtmb250LXdlaWdodDo2MDA7bGV0dGVyLXNwYWNpbmc6LTAuMDJlbX0KLmxvZ28gc3Bhbntjb2xvcjojNGE5MGQ5fQoubGl2ZS1iYWRnZXtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4cHg7YmFja2dyb3VuZDpyZ2JhKDM5LDE3NCw5NiwwLjE1KTtib3JkZXI6MXB4IHNvbGlkIHJnYmEoMzksMTc0LDk2LDAuMyk7Ym9yZGVyLXJhZGl1czoyMHB4O3BhZGRpbmc6NnB4IDE0cHg7Zm9udC1zaXplOjEycHg7Y29sb3I6IzVkYmE4NX0KLmxpdmUtZG90e3dpZHRoOjhweDtoZWlnaHQ6OHB4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6IzI3YWU2MDthbmltYXRpb246cHVsc2UgMnMgaW5maW5pdGV9CkBrZXlmcmFtZXMgcHVsc2V7MCUsMTAwJXtvcGFjaXR5OjF9NTAle29wYWNpdHk6MC40fX0KCi8qIEFHRU5UIFVSTCAqLwouYWdlbnQtdXJse2JhY2tncm91bmQ6cmdiYSgyNTUsMjU1LDI1NSwwLjA1KTtib3JkZXI6MXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsMC4xKTtib3JkZXItcmFkaXVzOjEwcHg7cGFkZGluZzoxMHB4IDE2cHg7bWFyZ2luLWJvdHRvbToyMHB4O2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHg7Zm9udC1zaXplOjEycHg7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjUpfQouYWdlbnQtdXJsIGlucHV0e2JhY2tncm91bmQ6bm9uZTtib3JkZXI6bm9uZTtjb2xvcjojNGE5MGQ5O2ZvbnQtc2l6ZToxMnB4O2ZvbnQtZmFtaWx5OmluaGVyaXQ7ZmxleDoxO291dGxpbmU6bm9uZTtjdXJzb3I6cG9pbnRlcn0KLmFnZW50LXVybC1sYWJlbHtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuMyk7d2hpdGUtc3BhY2U6bm93cmFwfQoKLyogU1RBVFMgKi8KLnN0YXRze2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDQsMWZyKTtnYXA6MTBweDttYXJnaW4tYm90dG9tOjIwcHh9Ci5zdGF0e2JhY2tncm91bmQ6cmdiYSgyNTUsMjU1LDI1NSwwLjA1KTtib3JkZXI6MXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsMC4wOCk7Ym9yZGVyLXJhZGl1czoxMnB4O3BhZGRpbmc6MTZweH0KLnN0YXQtdmFse2ZvbnQtc2l6ZToyOHB4O2ZvbnQtd2VpZ2h0OjYwMDttYXJnaW4tYm90dG9tOjRweH0KLnN0YXQtbGJse2ZvbnQtc2l6ZToxMXB4O2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsMC40KX0KLnN0YXQtc3Vie2ZvbnQtc2l6ZToxMHB4O2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsMC4zKTttYXJnaW4tdG9wOjNweH0KCi8qIENBUkRTICovCi5ncmlkLTJ7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczoxZnIgMWZyO2dhcDoxMnB4O21hcmdpbi1ib3R0b206MTJweH0KLmNhcmR7YmFja2dyb3VuZDpyZ2JhKDI1NSwyNTUsMjU1LDAuMDQpO2JvcmRlcjoxcHggc29saWQgcmdiYSgyNTUsMjU1LDI1NSwwLjA4KTtib3JkZXItcmFkaXVzOjEycHg7cGFkZGluZzoxNnB4fQouY2FyZC10aXRsZXtmb250LXNpemU6MTFweDtmb250LXdlaWdodDo2MDA7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjQpO2xldHRlci1zcGFjaW5nOjAuMDVlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7bWFyZ2luLWJvdHRvbToxMnB4fQoKLyogTkVYVCBSVU4gKi8KLm5leHQtcnVue3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjBweH0KLm5leHQtcnVuLXRpbWV7Zm9udC1zaXplOjQ4cHg7Zm9udC13ZWlnaHQ6NjAwO2NvbG9yOiM0YTkwZDk7bGluZS1oZWlnaHQ6MX0KLm5leHQtcnVuLWxhYmVse2ZvbnQtc2l6ZToxMnB4O2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsMC40KTttYXJnaW4tdG9wOjZweH0KLm5leHQtcnVuLWNvdW50ZG93bntmb250LXNpemU6MTRweDtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuNik7bWFyZ2luLXRvcDo4cHh9CgovKiBBQ1RJT05TICovCi5hY3Rpb25ze2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjhweH0KLmFjdGlvbi1idG57cGFkZGluZzoxMnB4IDE2cHg7Ym9yZGVyLXJhZGl1czoxMHB4O2ZvbnQtc2l6ZToxM3B4O2ZvbnQtd2VpZ2h0OjUwMDtjdXJzb3I6cG9pbnRlcjtmb250LWZhbWlseTppbmhlcml0O2JvcmRlcjpub25lO3RyYW5zaXRpb246YWxsIDAuMTVzO3RleHQtYWxpZ246bGVmdDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4fQouYnRuLXJ1bntiYWNrZ3JvdW5kOiMxODVGQTU7Y29sb3I6d2hpdGV9Ci5idG4tcnVuOmhvdmVye2JhY2tncm91bmQ6IzBkNGE4YX0KLmJ0bi1ydW46ZGlzYWJsZWR7YmFja2dyb3VuZDpyZ2JhKDI0LDk1LDE2NSwwLjQpO2N1cnNvcjpub3QtYWxsb3dlZH0KLmJ0bi1yZWZyZXNoe2JhY2tncm91bmQ6cmdiYSgyNTUsMjU1LDI1NSwwLjA3KTtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuOCk7Ym9yZGVyOjFweCBzb2xpZCByZ2JhKDI1NSwyNTUsMjU1LDAuMSl9Ci5idG4tcmVmcmVzaDpob3ZlcntiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC4xMil9Ci5idG4taWNvbntmb250LXNpemU6MTZweH0KCi8qIExPRyAqLwoubG9nLWFyZWF7YmFja2dyb3VuZDpyZ2JhKDAsMCwwLDAuMyk7Ym9yZGVyLXJhZGl1czoxMHB4O3BhZGRpbmc6MTJweDtmb250LWZhbWlseTonQ291cmllciBOZXcnLG1vbm9zcGFjZTtmb250LXNpemU6MTFweDtsaW5lLWhlaWdodDoxLjg7bWF4LWhlaWdodDoyMDBweDtvdmVyZmxvdy15OmF1dG87Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjYpfQoubG9nLWxpbmV7ZGlzcGxheTpibG9ja30KLmxvZy1saW5lLmluZm97Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjYpfQoubG9nLWxpbmUuc3VjY2Vzc3tjb2xvcjojNWRiYTg1fQoubG9nLWxpbmUuZXJyb3J7Y29sb3I6I2ZmOGE5YX0KLmxvZy1saW5lLndhcm57Y29sb3I6I2Y1Yzg0Mn0KCi8qIENBTkRJREFURVMgKi8KLmNhbmQtbGlzdHtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2dhcDo2cHg7bWF4LWhlaWdodDoyMjBweDtvdmVyZmxvdy15OmF1dG99Ci5jYW5kLWl0ZW17ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjhweCAxMHB4O2JhY2tncm91bmQ6cmdiYSgyNTUsMjU1LDI1NSwwLjA0KTtib3JkZXItcmFkaXVzOjhweDtmb250LXNpemU6MTJweH0KLmNhbmQtYXZ7d2lkdGg6MjhweDtoZWlnaHQ6MjhweDtib3JkZXItcmFkaXVzOjUwJTtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Zm9udC1zaXplOjEwcHg7Zm9udC13ZWlnaHQ6NjAwO2ZsZXgtc2hyaW5rOjA7YmFja2dyb3VuZDpyZ2JhKDc0LDE0NCwyMTcsMC4yKTtjb2xvcjojN2ViM2U4fQouY2FuZC1uYW1le2ZsZXg6MTtmb250LXdlaWdodDo1MDB9Ci5jYW5kLWNvbXBhbnl7Zm9udC1zaXplOjEwcHg7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjQpfQouY2FuZC1zdGF0dXN7Zm9udC1zaXplOjEwcHg7cGFkZGluZzoycHggN3B4O2JvcmRlci1yYWRpdXM6OHB4fQouY3MtbmV3e2JhY2tncm91bmQ6cmdiYSg3NCwxNDQsMjE3LDAuMik7Y29sb3I6IzdlYjNlOH0KLmNzLXNlbnR7YmFja2dyb3VuZDpyZ2JhKDE1MCwxMDAsMjAwLDAuMik7Y29sb3I6I2MwOGFlOH0KLmNzLXJlcHtiYWNrZ3JvdW5kOnJnYmEoMzksMTc0LDk2LDAuMik7Y29sb3I6IzVkYmE4NX0KLmNzLWludHtiYWNrZ3JvdW5kOnJnYmEoMzAsMTgwLDEyMCwwLjE1KTtjb2xvcjojNWRkYmE4fQoKLyogU1RBVFVTIElORElDQVRPUiAqLwouc3RhdHVzLXJvd3tkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4cHg7cGFkZGluZzo4cHggMDtib3JkZXItYm90dG9tOjFweCBzb2xpZCByZ2JhKDI1NSwyNTUsMjU1LDAuMDYpO2ZvbnQtc2l6ZToxMnB4fQouc3RhdHVzLXJvdzpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206bm9uZX0KLnN0YXR1cy1kb3Qtc217d2lkdGg6N3B4O2hlaWdodDo3cHg7Ym9yZGVyLXJhZGl1czo1MCU7ZmxleC1zaHJpbms6MH0KLmRvdC1ncmVlbntiYWNrZ3JvdW5kOiMyN2FlNjB9Ci5kb3QtYW1iZXJ7YmFja2dyb3VuZDojZjVjODQyfQouZG90LXJlZHtiYWNrZ3JvdW5kOiNlNzRjM2N9Ci5kb3QtZ3JheXtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC4yKX0KLnN0YXR1cy1sYWJlbHtmbGV4OjE7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjcpfQouc3RhdHVzLXZhbHtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuNCk7Zm9udC1zaXplOjExcHh9CgovKiBFTVBUWSBTVEFURSAqLwouZW1wdHl7dGV4dC1hbGlnbjpjZW50ZXI7cGFkZGluZzoyMHB4O2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsMC4zKTtmb250LXNpemU6MTJweH0KCi8qIExPQURJTkcgKi8KLmxvYWRpbmd7b3BhY2l0eTowLjU7Zm9udC1zdHlsZTppdGFsaWM7Zm9udC1zaXplOjExcHg7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjQpfQoKLyogU1BJTk5FUiAqLwouc3Bpbm5lcntkaXNwbGF5OmlubGluZS1ibG9jazt3aWR0aDoxMnB4O2hlaWdodDoxMnB4O2JvcmRlcjoycHggc29saWQgcmdiYSgyNTUsMjU1LDI1NSwwLjIpO2JvcmRlci10b3AtY29sb3I6d2hpdGU7Ym9yZGVyLXJhZGl1czo1MCU7YW5pbWF0aW9uOnNwaW4gMC44cyBsaW5lYXIgaW5maW5pdGU7bWFyZ2luLXJpZ2h0OjZweH0KQGtleWZyYW1lcyBzcGlue3Rve3RyYW5zZm9ybTpyb3RhdGUoMzYwZGVnKX19CgouZnVsbC1jYXJke21hcmdpbi1ib3R0b206MTJweH0KLnRvYXN0e3Bvc2l0aW9uOmZpeGVkO2JvdHRvbToyMHB4O3JpZ2h0OjIwcHg7YmFja2dyb3VuZDojMjdhZTYwO2NvbG9yOndoaXRlO3BhZGRpbmc6MTBweCAxOHB4O2JvcmRlci1yYWRpdXM6MTBweDtmb250LXNpemU6MTJweDtmb250LXdlaWdodDo1MDA7b3BhY2l0eTowO3RyYW5zaXRpb246b3BhY2l0eSAwLjNzO3BvaW50ZXItZXZlbnRzOm5vbmU7ei1pbmRleDo5OTl9Ci50b2FzdC5zaG93e29wYWNpdHk6MX0KLnRvYXN0LmVycm9ye2JhY2tncm91bmQ6I2U3NGMzY30KPC9zdHlsZT4KPC9oZWFkPgo8Ym9keT4KPGRpdiBjbGFzcz0icGFnZSI+CgogIDwhLS0gSEVBREVSIC0tPgogIDxkaXYgY2xhc3M9ImhlYWRlciI+CiAgICA8ZGl2IGNsYXNzPSJsb2dvIj5Db252ZTxzcGFuPm5leDwvc3Bhbj4gwrcgQWdlbnQgRGFzaGJvYXJkPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJsaXZlLWJhZGdlIiBpZD0ibGl2ZS1iYWRnZSI+CiAgICAgIDxzcGFuIGNsYXNzPSJsaXZlLWRvdCI+PC9zcGFuPgogICAgICA8c3BhbiBpZD0ibGl2ZS10ZXh0Ij5WZXJiaW5kZS4uLjwvc3Bhbj4KICAgIDwvZGl2PgogIDwvZGl2PgoKICA8IS0tIEFHRU5UIFVSTCBJTlBVVCAtLT4KICA8ZGl2IGNsYXNzPSJhZ2VudC11cmwiPgogICAgPHNwYW4gY2xhc3M9ImFnZW50LXVybC1sYWJlbCI+QWdlbnQgVVJMOjwvc3Bhbj4KICAgIDxpbnB1dCB0eXBlPSJ0ZXh0IiBpZD0iYWdlbnQtdXJsIiB2YWx1ZT0iaHR0cHM6Ly9jb3ZlbmV4LWFnZW50Lm9ucmVuZGVyLmNvbSIgcGxhY2Vob2xkZXI9Imh0dHBzOi8vZGVpbmUtdXJsLm9ucmVuZGVyLmNvbSI+CiAgICA8YnV0dG9uIG9uY2xpY2s9ImxvYWRBbGwoKSIgc3R5bGU9ImJhY2tncm91bmQ6IzE4NUZBNTtjb2xvcjp3aGl0ZTtib3JkZXI6bm9uZTtib3JkZXItcmFkaXVzOjdweDtwYWRkaW5nOjVweCAxMnB4O2ZvbnQtc2l6ZToxMXB4O2N1cnNvcjpwb2ludGVyO2ZvbnQtZmFtaWx5OmluaGVyaXQiPlZlcmJpbmRlbjwvYnV0dG9uPgogIDwvZGl2PgoKICA8IS0tIFNUQVRTIFJPVyAtLT4KICA8ZGl2IGNsYXNzPSJzdGF0cyI+CiAgICA8ZGl2IGNsYXNzPSJzdGF0Ij4KICAgICAgPGRpdiBjbGFzcz0ic3RhdC12YWwiIGlkPSJzdGF0LWNhbmRpZGF0ZXMiPuKAlDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGF0LWxibCI+S2FuZGlkYXRlbiB0b3RhbDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGF0LXN1YiIgaWQ9InN0YXQtY2FuZGlkYXRlcy1zdWIiPndpcmQgZ2VsYWRlbi4uLjwvZGl2PgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzdGF0Ij4KICAgICAgPGRpdiBjbGFzcz0ic3RhdC12YWwiIGlkPSJzdGF0LWVtYWlscyI+4oCUPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtbGJsIj5FLU1haWxzIGhldXRlPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtc3ViIiBpZD0ic3RhdC1lbWFpbHMtc3ViIj53aXJkIGdlbGFkZW4uLi48L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0ic3RhdCI+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtdmFsIiBpZD0ic3RhdC1uZXh0Ij7igJQ8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RhdC1sYmwiPk7DpGNoc3RlciBMYXVmPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtc3ViIiBpZD0ic3RhdC1uZXh0LXN1YiI+dMOkZ2xpY2ggYXV0b21hdGlzY2g8L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0ic3RhdCI+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtdmFsIiBpZD0ic3RhdC1zdGF0dXMiPuKAlDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGF0LWxibCI+QWdlbnQgU3RhdHVzPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtc3ViIiBpZD0ic3RhdC1zdGF0dXMtc3ViIj53aXJkIGdlcHLDvGZ0Li4uPC9kaXY+CiAgICA8L2Rpdj4KICA8L2Rpdj4KCiAgPGRpdiBjbGFzcz0iZ3JpZC0yIj4KCiAgICA8IS0tIE5FWFQgUlVOICsgQUNUSU9OUyAtLT4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjEycHgiPgogICAgICA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgICAgICA8ZGl2IGNsYXNzPSJjYXJkLXRpdGxlIj7ij7AgTsOkY2hzdGVyIGF1dG9tYXRpc2NoZXIgTGF1ZjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9Im5leHQtcnVuIj4KICAgICAgICAgIDxkaXYgY2xhc3M9Im5leHQtcnVuLXRpbWUiIGlkPSJuZXh0LXJ1bi10aW1lIj4wNzozMDwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0ibmV4dC1ydW4tbGFiZWwiPlVociDCtyB0w6RnbGljaCBhdXRvbWF0aXNjaDwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0ibmV4dC1ydW4tY291bnRkb3duIiBpZD0iY291bnRkb3duIj5iZXJlY2huZS4uLjwvZGl2PgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICAgICAgPGRpdiBjbGFzcz0iY2FyZC10aXRsZSI+8J+OriBTdGV1ZXJ1bmc8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJhY3Rpb25zIj4KICAgICAgICAgIDxidXR0b24gY2xhc3M9ImFjdGlvbi1idG4gYnRuLXJ1biIgaWQ9InJ1bi1idG4iIG9uY2xpY2s9InJ1bkFnZW50KCkiPgogICAgICAgICAgICA8c3BhbiBjbGFzcz0iYnRuLWljb24iPuKWtjwvc3Bhbj4gQWdlbnQgamV0enQgc3RhcnRlbgogICAgICAgICAgPC9idXR0b24+CiAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJhY3Rpb24tYnRuIGJ0bi1yZWZyZXNoIiBvbmNsaWNrPSJsb2FkQWxsKCkiPgogICAgICAgICAgICA8c3BhbiBjbGFzcz0iYnRuLWljb24iPuKGuzwvc3Bhbj4gU3RhdHVzIGFrdHVhbGlzaWVyZW4KICAgICAgICAgIDwvYnV0dG9uPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6MTBweDtmb250LXNpemU6MTBweDtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuMjUpO2xpbmUtaGVpZ2h0OjEuNSI+CiAgICAgICAgICAiQWdlbnQgc3RhcnRlbiIgbMO2c3Qgc29mb3J0IGVpbmVuIGtvbXBsZXR0ZW4gRHVyY2hsYXVmIGF1cyDigJQgR21haWwgcHLDvGZlbiwgRm9sbG93LXVwcyBzZW5kZW4sIFJlcG9ydCBzY2hpY2tlbi4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj4KCiAgICA8IS0tIFNZU1RFTSBTVEFUVVMgLS0+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgICAgPGRpdiBjbGFzcz0iY2FyZC10aXRsZSI+8J+UjCBTeXN0ZW0gU3RhdHVzPC9kaXY+CiAgICAgIDxkaXYgaWQ9InN5c3RlbS1zdGF0dXMiPgogICAgICAgIDxkaXYgY2xhc3M9InN0YXR1cy1yb3ciPgogICAgICAgICAgPGRpdiBjbGFzcz0ic3RhdHVzLWRvdC1zbSBkb3QtZ3JheSIgaWQ9ImRvdC1hZ2VudCI+PC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0dXMtbGFiZWwiPkFnZW50IChSZW5kZXIpPC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0dXMtdmFsIiBpZD0idmFsLWFnZW50Ij5wcsO8ZmUuLi48L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0dXMtcm93Ij4KICAgICAgICAgIDxkaXYgY2xhc3M9InN0YXR1cy1kb3Qtc20gZG90LWdyYXkiIGlkPSJkb3QtYWkiPjwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0ic3RhdHVzLWxhYmVsIj5BbnRocm9waWMgS0k8L2Rpdj4KICAgICAgICAgIDxkaXYgY2xhc3M9InN0YXR1cy12YWwiIGlkPSJ2YWwtYWkiPuKAlDwvZGl2PgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9InN0YXR1cy1yb3ciPgogICAgICAgICAgPGRpdiBjbGFzcz0ic3RhdHVzLWRvdC1zbSBkb3QtZ3JheSIgaWQ9ImRvdC1nbWFpbCI+PC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0dXMtbGFiZWwiPkdtYWlsPC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0dXMtdmFsIiBpZD0idmFsLWdtYWlsIj5ub2NoIG5pY2h0IGVpbmdlcmljaHRldDwvZGl2PgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9InN0YXR1cy1yb3ciPgogICAgICAgICAgPGRpdiBjbGFzcz0ic3RhdHVzLWRvdC1zbSBkb3QtZ3JheSIgaWQ9ImRvdC1jYWxlbmRhciI+PC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0dXMtbGFiZWwiPkdvb2dsZSBDYWxlbmRhcjwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0ic3RhdHVzLXZhbCIgaWQ9InZhbC1jYWxlbmRhciI+bm9jaCBuaWNodCBlaW5nZXJpY2h0ZXQ8L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0dXMtcm93Ij4KICAgICAgICAgIDxkaXYgY2xhc3M9InN0YXR1cy1kb3Qtc20gZG90LWdyYXkiIGlkPSJkb3Qtc2NoZWR1bGUiPjwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0ic3RhdHVzLWxhYmVsIj5Uw6RnbGljaGVyIFNjaGVkdWxlPC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0dXMtdmFsIiBpZD0idmFsLXNjaGVkdWxlIj7igJQ8L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6MTJweDtwYWRkaW5nLXRvcDoxMnB4O2JvcmRlci10b3A6MXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsMC4wNikiPgogICAgICAgIDxkaXYgY2xhc3M9ImNhcmQtdGl0bGUiPvCfk4ogSGV1dGU8L2Rpdj4KICAgICAgICA8ZGl2IGlkPSJ0b2RheS1zdGF0cyIgY2xhc3M9ImxvYWRpbmciPndpcmQgZ2VsYWRlbi4uLjwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgoKICA8L2Rpdj4KCiAgPCEtLSBMT0cgLS0+CiAgPGRpdiBjbGFzcz0iY2FyZCBmdWxsLWNhcmQiPgogICAgPGRpdiBjbGFzcz0iY2FyZC10aXRsZSIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW4iPgogICAgICA8c3Bhbj7wn5OLIExpdmUgTG9nPC9zcGFuPgogICAgICA8YnV0dG9uIG9uY2xpY2s9ImNsZWFyTG9nKCkiIHN0eWxlPSJiYWNrZ3JvdW5kOm5vbmU7Ym9yZGVyOm5vbmU7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjMpO2N1cnNvcjpwb2ludGVyO2ZvbnQtc2l6ZToxMXB4O2ZvbnQtZmFtaWx5OmluaGVyaXQiPmxlZXJlbjwvYnV0dG9uPgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJsb2ctYXJlYSIgaWQ9ImxvZy1hcmVhIj4KICAgICAgPHNwYW4gY2xhc3M9ImxvZy1saW5lIGluZm8iPkNvdmVuZXggQWdlbnQgRGFzaGJvYXJkIGdlc3RhcnRldC4uLjwvc3Bhbj4KICAgIDwvZGl2PgogIDwvZGl2PgoKICA8IS0tIENBTkRJREFURVMgLS0+CiAgPGRpdiBjbGFzcz0iY2FyZCBmdWxsLWNhcmQiPgogICAgPGRpdiBjbGFzcz0iY2FyZC10aXRsZSI+8J+RpCBLYW5kaWRhdGVuIGluIFBpcGVsaW5lPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJjYW5kLWxpc3QiIGlkPSJjYW5kLWxpc3QiPgogICAgICA8ZGl2IGNsYXNzPSJlbXB0eSI+VmVyYmluZGUgbWl0IEFnZW50IHVtIEthbmRpZGF0ZW4genUgbGFkZW4uLi48L2Rpdj4KICAgIDwvZGl2PgogIDwvZGl2PgoKPC9kaXY+Cgo8ZGl2IGNsYXNzPSJ0b2FzdCIgaWQ9InRvYXN0Ij48L2Rpdj4KCjxzY3JpcHQ+CmxldCBhZ2VudFVybCA9ICdodHRwczovL2NvdmVuZXgtYWdlbnQub25yZW5kZXIuY29tJzsKbGV0IGxhc3RTdGF0dXMgPSBudWxsOwoKZnVuY3Rpb24gZ2V0VXJsKCkgewogIHJldHVybiBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYWdlbnQtdXJsJykudmFsdWUudHJpbSgpLnJlcGxhY2UoL1wvJC8sICcnKTsKfQoKZnVuY3Rpb24gbG9nKG1zZywgdHlwZSA9ICdpbmZvJykgewogIGNvbnN0IGFyZWEgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9nLWFyZWEnKTsKICBjb25zdCBub3cgPSBuZXcgRGF0ZSgpLnRvTG9jYWxlVGltZVN0cmluZygnZGUtREUnKTsKICBjb25zdCBsaW5lID0gZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgnc3BhbicpOwogIGxpbmUuY2xhc3NOYW1lID0gYGxvZy1saW5lICR7dHlwZX1gOwogIGxpbmUudGV4dENvbnRlbnQgPSBgWyR7bm93fV0gJHttc2d9YDsKICBhcmVhLmFwcGVuZENoaWxkKGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2JyJykpOwogIGFyZWEuYXBwZW5kQ2hpbGQobGluZSk7CiAgYXJlYS5zY3JvbGxUb3AgPSBhcmVhLnNjcm9sbEhlaWdodDsKfQoKZnVuY3Rpb24gY2xlYXJMb2coKSB7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xvZy1hcmVhJykuaW5uZXJIVE1MID0gJzxzcGFuIGNsYXNzPSJsb2ctbGluZSBpbmZvIj5Mb2cgZ2VsZWVydC48L3NwYW4+JzsKfQoKZnVuY3Rpb24gc2hvd1RvYXN0KG1zZywgaXNFcnJvciA9IGZhbHNlKSB7CiAgY29uc3QgdCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd0b2FzdCcpOwogIHQudGV4dENvbnRlbnQgPSBtc2c7CiAgdC5jbGFzc05hbWUgPSBgdG9hc3QgJHtpc0Vycm9yID8gJ2Vycm9yJyA6ICcnfSBzaG93YDsKICBzZXRUaW1lb3V0KCgpID0+IHQuY2xhc3NOYW1lID0gJ3RvYXN0JywgMzAwMCk7Cn0KCmZ1bmN0aW9uIHNldERvdChpZCwgY29sb3IpIHsKICBjb25zdCBlbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKTsKICBpZiAoZWwpIHsgZWwuY2xhc3NOYW1lID0gYHN0YXR1cy1kb3Qtc20gZG90LSR7Y29sb3J9YDsgfQp9Cgphc3luYyBmdW5jdGlvbiBsb2FkU3RhdHVzKCkgewogIGNvbnN0IHVybCA9IGdldFVybCgpOwogIHRyeSB7CiAgICBjb25zdCByID0gYXdhaXQgZmV0Y2goYCR7dXJsfS9oZWFsdGhgLCB7c2lnbmFsOiBBYm9ydFNpZ25hbC50aW1lb3V0KDgwMDApfSk7CiAgICBpZiAoci5vaykgewogICAgICBjb25zdCBkID0gYXdhaXQgci5qc29uKCk7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsaXZlLXRleHQnKS50ZXh0Q29udGVudCA9ICdMaXZlIMK3IGzDpHVmdCc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsaXZlLWJhZGdlJykuc3R5bGUuYmFja2dyb3VuZCA9ICdyZ2JhKDM5LDE3NCw5NiwwLjE1KSc7CiAgICAgIHNldERvdCgnZG90LWFnZW50JywgJ2dyZWVuJyk7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd2YWwtYWdlbnQnKS50ZXh0Q29udGVudCA9ICfinJMgT25saW5lJzsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXQtc3RhdHVzJykudGV4dENvbnRlbnQgPSAn4pyTJzsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXQtc3RhdHVzLXN1YicpLnRleHRDb250ZW50ID0gJ0FnZW50IGzDpHVmdCc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdGF0LW5leHQnKS50ZXh0Q29udGVudCA9IGQubmV4dF9ydW4gfHwgJzA3OjMwJzsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3ZhbC1zY2hlZHVsZScpLnRleHRDb250ZW50ID0gYHTDpGdsaWNoICR7ZC5uZXh0X3J1biB8fCAnMDc6MzAnfWA7CiAgICAgIHNldERvdCgnZG90LXNjaGVkdWxlJywgJ2dyZWVuJyk7CiAgICAgIGxvZygnQWdlbnQgZXJyZWljaGJhciDigJQgU3RhdHVzOiBMaXZlJywgJ3N1Y2Nlc3MnKTsKICAgICAgcmV0dXJuIHRydWU7CiAgICB9CiAgfSBjYXRjaChlKSB7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGl2ZS10ZXh0JykudGV4dENvbnRlbnQgPSAnTmljaHQgZXJyZWljaGJhcic7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGl2ZS1iYWRnZScpLnN0eWxlLmJhY2tncm91bmQgPSAncmdiYSgyMzEsNzYsNjAsMC4xNSknOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xpdmUtYmFkZ2UnKS5zdHlsZS5ib3JkZXJDb2xvciA9ICdyZ2JhKDIzMSw3Niw2MCwwLjMpJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsaXZlLWJhZGdlJykuc3R5bGUuY29sb3IgPSAnI2ZmOGE5YSc7CiAgICBzZXREb3QoJ2RvdC1hZ2VudCcsICdyZWQnKTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd2YWwtYWdlbnQnKS50ZXh0Q29udGVudCA9ICfinJcgT2ZmbGluZSc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RhdC1zdGF0dXMnKS50ZXh0Q29udGVudCA9ICfinJcnOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXQtc3RhdHVzLXN1YicpLnRleHRDb250ZW50ID0gJ25pY2h0IGVycmVpY2hiYXInOwogICAgbG9nKGBBZ2VudCBuaWNodCBlcnJlaWNoYmFyOiAke2UubWVzc2FnZX1gLCAnZXJyb3InKTsKICAgIHJldHVybiBmYWxzZTsKICB9Cn0KCmFzeW5jIGZ1bmN0aW9uIGxvYWRTdGF0cygpIHsKICBjb25zdCB1cmwgPSBnZXRVcmwoKTsKICB0cnkgewogICAgY29uc3QgciA9IGF3YWl0IGZldGNoKGAke3VybH0vc3RhdHVzYCwge3NpZ25hbDogQWJvcnRTaWduYWwudGltZW91dCg4MDAwKX0pOwogICAgaWYgKHIub2spIHsKICAgICAgY29uc3QgZCA9IGF3YWl0IHIuanNvbigpOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RhdC1lbWFpbHMnKS50ZXh0Q29udGVudCA9IGQuZW1haWxzX3NlbnRfdG9kYXkgfHwgJzAnOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RhdC1lbWFpbHMtc3ViJykudGV4dENvbnRlbnQgPSBgbWF4ICR7ZC5tYXhfcGVyX2RheSB8fCAzMH0vVGFnYDsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RvZGF5LXN0YXRzJykuaW5uZXJIVE1MID0gYAogICAgICAgIDxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMXB4O2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsMC41KTtsaW5lLWhlaWdodDoxLjgiPgogICAgICAgICAgRS1NYWlscyBnZXNlbmRldDogPHNwYW4gc3R5bGU9ImNvbG9yOndoaXRlIj4ke2QuZW1haWxzX3NlbnRfdG9kYXkgfHwgMH08L3NwYW4+PGJyPgogICAgICAgICAgVGFnZXNsaW1pdDogPHNwYW4gc3R5bGU9ImNvbG9yOndoaXRlIj4ke2QubWF4X3Blcl9kYXkgfHwgMzB9PC9zcGFuPjxicj4KICAgICAgICAgIExldHp0ZXIgUmVzZXQ6IDxzcGFuIHN0eWxlPSJjb2xvcjp3aGl0ZSI+JHtkLmxhc3RfcmVzZXQgfHwgJ+KAlCd9PC9zcGFuPgogICAgICAgIDwvZGl2PmA7CiAgICAgIHNldERvdCgnZG90LWFpJywgJ2dyZWVuJyk7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd2YWwtYWknKS50ZXh0Q29udGVudCA9ICfinJMgVmVyYnVuZGVuJzsKICAgICAgbG9nKGBTdGF0cyBnZWxhZGVuIOKAlCAke2QuZW1haWxzX3NlbnRfdG9kYXkgfHwgMH0gRS1NYWlscyBoZXV0ZWAsICdpbmZvJyk7CiAgICB9CiAgfSBjYXRjaChlKSB7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndG9kYXktc3RhdHMnKS50ZXh0Q29udGVudCA9ICdLZWluZSBEYXRlbiB2ZXJmw7xnYmFyJzsKICB9Cn0KCmFzeW5jIGZ1bmN0aW9uIGxvYWRDYW5kaWRhdGVzKCkgewogIGNvbnN0IHVybCA9IGdldFVybCgpOwogIHRyeSB7CiAgICBjb25zdCByID0gYXdhaXQgZmV0Y2goYCR7dXJsfS9jYW5kaWRhdGVzYCwge3NpZ25hbDogQWJvcnRTaWduYWwudGltZW91dCg4MDAwKX0pOwogICAgaWYgKHIub2spIHsKICAgICAgY29uc3QgY2FuZGlkYXRlcyA9IGF3YWl0IHIuanNvbigpOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RhdC1jYW5kaWRhdGVzJykudGV4dENvbnRlbnQgPSBjYW5kaWRhdGVzLmxlbmd0aCB8fCAnMCc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdGF0LWNhbmRpZGF0ZXMtc3ViJykudGV4dENvbnRlbnQgPSAnaW4gUGlwZWxpbmUnOwoKICAgICAgY29uc3QgbGlzdCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdjYW5kLWxpc3QnKTsKICAgICAgaWYgKCFjYW5kaWRhdGVzLmxlbmd0aCkgewogICAgICAgIGxpc3QuaW5uZXJIVE1MID0gJzxkaXYgY2xhc3M9ImVtcHR5Ij5Ob2NoIGtlaW5lIEthbmRpZGF0ZW4g4oCUIEFnZW50IGzDpHVmdCB0w6RnbGljaCB1bmQgZsO8Z3QgbmV1ZSBoaW56dTwvZGl2Pic7CiAgICAgICAgcmV0dXJuOwogICAgICB9CgogICAgICBsaXN0LmlubmVySFRNTCA9IGNhbmRpZGF0ZXMuc2xpY2UoMCwgMTApLm1hcChjID0+IHsKICAgICAgICBjb25zdCBpbml0aWFscyA9IChjLm5hbWUgfHwgJ1hYJykuc3BsaXQoJyAnKS5tYXAobj0+blswXSkuam9pbignJykuc3Vic3RyaW5nKDAsMikudG9VcHBlckNhc2UoKTsKICAgICAgICBjb25zdCBzdGF0dXNDbGFzcyA9IHtuZXc6J2NzLW5ldycsc2VudDonY3Mtc2VudCcscmVwbGllZDonY3MtcmVwJyxib29rZWQ6J2NzLWludCd9W2Muc3RhdHVzXSB8fCAnY3MtbmV3JzsKICAgICAgICBjb25zdCBzdGF0dXNMYWJlbCA9IHtuZXc6J05ldScsc2VudDonS29udGFrdGllcnQnLHJlcGxpZWQ6J0dlYW50d29ydGV0Jyxib29rZWQ6J1Rlcm1pbicsc2VxdWVuY2VfY29tcGxldGU6J0FiZ2VzY2hsb3NzZW4nfVtjLnN0YXR1c10gfHwgYy5zdGF0dXM7CiAgICAgICAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYW5kLWl0ZW0iPgogICAgICAgICAgPGRpdiBjbGFzcz0iY2FuZC1hdiI+JHtpbml0aWFsc308L2Rpdj4KICAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MSI+CiAgICAgICAgICAgIDxkaXYgY2xhc3M9ImNhbmQtbmFtZSI+JHtjLm5hbWUgfHwgJ1VuYmVrYW5udCd9PC9kaXY+CiAgICAgICAgICAgIDxkaXYgY2xhc3M9ImNhbmQtY29tcGFueSI+JHtjLmN1cnJlbnRfY29tcGFueSB8fCAnJ30gwrcgJHtjLnJlZ2lvbiB8fCAnJ308L2Rpdj4KICAgICAgICAgIDwvZGl2PgogICAgICAgICAgPHNwYW4gY2xhc3M9ImNhbmQtc3RhdHVzICR7c3RhdHVzQ2xhc3N9Ij4ke3N0YXR1c0xhYmVsfTwvc3Bhbj4KICAgICAgICA8L2Rpdj5gOwogICAgICB9KS5qb2luKCcnKTsKCiAgICAgIGlmIChjYW5kaWRhdGVzLmxlbmd0aCA+IDEwKSB7CiAgICAgICAgbGlzdC5pbm5lckhUTUwgKz0gYDxkaXYgY2xhc3M9ImVtcHR5Ij4rICR7Y2FuZGlkYXRlcy5sZW5ndGggLSAxMH0gd2VpdGVyZSBLYW5kaWRhdGVuPC9kaXY+YDsKICAgICAgfQogICAgICBsb2coYCR7Y2FuZGlkYXRlcy5sZW5ndGh9IEthbmRpZGF0ZW4gZ2VsYWRlbmAsICdzdWNjZXNzJyk7CiAgICB9CiAgfSBjYXRjaChlKSB7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnY2FuZC1saXN0JykuaW5uZXJIVE1MID0gJzxkaXYgY2xhc3M9ImVtcHR5Ij5LYW5kaWRhdGVuIGtvbm50ZW4gbmljaHQgZ2VsYWRlbiB3ZXJkZW48L2Rpdj4nOwogIH0KfQoKYXN5bmMgZnVuY3Rpb24gcnVuQWdlbnQoKSB7CiAgY29uc3QgdXJsID0gZ2V0VXJsKCk7CiAgY29uc3QgYnRuID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3J1bi1idG4nKTsKICBidG4uZGlzYWJsZWQgPSB0cnVlOwogIGJ0bi5pbm5lckhUTUwgPSAnPHNwYW4gY2xhc3M9InNwaW5uZXIiPjwvc3Bhbj4gQWdlbnQgbMOkdWZ0Li4uJzsKICBsb2coJ01hbnVlbGxlciBBZ2VudC1TdGFydCB3aXJkIGF1c2dlbMO2c3QuLi4nLCAnd2FybicpOwoKICB0cnkgewogICAgY29uc3QgciA9IGF3YWl0IGZldGNoKGAke3VybH0vcnVuLW5vd2AsIHsKICAgICAgbWV0aG9kOiAnUE9TVCcsCiAgICAgIHNpZ25hbDogQWJvcnRTaWduYWwudGltZW91dCg2MDAwMCkKICAgIH0pOwogICAgaWYgKHIub2spIHsKICAgICAgY29uc3QgZCA9IGF3YWl0IHIuanNvbigpOwogICAgICBsb2coJ+KckyBBZ2VudCBlcmZvbGdyZWljaCBhdXNnZWbDvGhydCEnLCAnc3VjY2VzcycpOwogICAgICBzaG93VG9hc3QoJ+KckyBBZ2VudCB3dXJkZSBhdXNnZWbDvGhydCEnKTsKICAgICAgc2V0VGltZW91dChsb2FkQWxsLCAyMDAwKTsKICAgIH0gZWxzZSB7CiAgICAgIGxvZygnRmVobGVyIGJlaW0gU3RhcnRlbiBkZXMgQWdlbnRzJywgJ2Vycm9yJyk7CiAgICAgIHNob3dUb2FzdCgnRmVobGVyIGJlaW0gU3RhcnRlbicsIHRydWUpOwogICAgfQogIH0gY2F0Y2goZSkgewogICAgbG9nKGBBZ2VudC1GZWhsZXI6ICR7ZS5tZXNzYWdlfWAsICdlcnJvcicpOwogICAgc2hvd1RvYXN0KCdWZXJiaW5kdW5nc2ZlaGxlcicsIHRydWUpOwogIH0gZmluYWxseSB7CiAgICBidG4uZGlzYWJsZWQgPSBmYWxzZTsKICAgIGJ0bi5pbm5lckhUTUwgPSAnPHNwYW4gY2xhc3M9ImJ0bi1pY29uIj7ilrY8L3NwYW4+IEFnZW50IGpldHp0IHN0YXJ0ZW4nOwogIH0KfQoKZnVuY3Rpb24gdXBkYXRlQ291bnRkb3duKCkgewogIGNvbnN0IG5vdyA9IG5ldyBEYXRlKCk7CiAgY29uc3QgcnVuVGltZSA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCduZXh0LXJ1bi10aW1lJykudGV4dENvbnRlbnQ7CiAgY29uc3QgW2gsIG1dID0gcnVuVGltZS5zcGxpdCgnOicpLm1hcChOdW1iZXIpOwogIGNvbnN0IG5leHQgPSBuZXcgRGF0ZSgpOwogIG5leHQuc2V0SG91cnMoaCwgbSwgMCwgMCk7CiAgaWYgKG5leHQgPD0gbm93KSBuZXh0LnNldERhdGUobmV4dC5nZXREYXRlKCkgKyAxKTsKICBjb25zdCBkaWZmID0gbmV4dCAtIG5vdzsKICBjb25zdCBob3VycyA9IE1hdGguZmxvb3IoZGlmZiAvIDM2MDAwMDApOwogIGNvbnN0IG1pbnMgPSBNYXRoLmZsb29yKChkaWZmICUgMzYwMDAwMCkgLyA2MDAwMCk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2NvdW50ZG93bicpLnRleHRDb250ZW50ID0gYGluICR7aG91cnN9aCAke21pbnN9bWluYDsKfQoKYXN5bmMgZnVuY3Rpb24gbG9hZEFsbCgpIHsKICBsb2coJ0xhZGUgYWxsZSBEYXRlbi4uLicsICdpbmZvJyk7CiAgY29uc3Qgb2sgPSBhd2FpdCBsb2FkU3RhdHVzKCk7CiAgaWYgKG9rKSB7CiAgICBhd2FpdCBsb2FkU3RhdHMoKTsKICAgIGF3YWl0IGxvYWRDYW5kaWRhdGVzKCk7CiAgfQp9CgovLyBTdGFydApsb2FkQWxsKCk7CnNldEludGVydmFsKHVwZGF0ZUNvdW50ZG93biwgNjAwMDApOwp1cGRhdGVDb3VudGRvd24oKTsKc2V0SW50ZXJ2YWwoKCkgPT4gewogIGxvYWRTdGF0dXMoKTsKICBsb2FkU3RhdHMoKTsKfSwgMzAwMDApOyAvLyBBbGxlIDMwIFNla3VuZGVuIGFrdHVhbGlzaWVyZW4KPC9zY3JpcHQ+CjwvYm9keT4KPC9odG1sPgo=").decode("utf-8")

@app.route("/", methods=["GET"])
def dashboard():
    """Liefert das Agent Dashboard aus."""
    return DASHBOARD_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

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
