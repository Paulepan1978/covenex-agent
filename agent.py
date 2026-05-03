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


# ── RECRUITER APP ROUTE ────────────────────────────────────
RECRUITER_APP_HTML = base64.b64decode("PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImRlIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9IlVURi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9IndpZHRoPWRldmljZS13aWR0aCwgaW5pdGlhbC1zY2FsZT0xLjAiPgo8dGl0bGU+Q292ZW5leCBSZWNydWl0ZXIgwrcgQ1lRVUVPPC90aXRsZT4KPGxpbmsgaHJlZj0iaHR0cHM6Ly9mb250cy5nb29nbGVhcGlzLmNvbS9jc3MyP2ZhbWlseT1ETStTYW5zOml0YWwsd2dodEAwLDMwMDswLDQwMDswLDUwMDswLDYwMDsxLDMwMCZkaXNwbGF5PXN3YXAiIHJlbD0ic3R5bGVzaGVldCI+CjxzdHlsZT4KLyogPT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09CiAgIENPVkVORVggUkVDUlVJVEVSIOKAlCBDWVFVRU8gRWRpdGlvbgogICBWb2xsc3TDpG5kaWdlIEFwcDogU3RlbGxlbi1VcGxvYWQsIFNvdXJjaW5nLCBFLU1haWwsIFBpcGVsaW5lLAogICBDb21wYW55IEludGVsbGlnZW5jZSwgU2NoZWR1bGluZwogICA9PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0gKi8KKntib3gtc2l6aW5nOmJvcmRlci1ib3g7bWFyZ2luOjA7cGFkZGluZzowfQpodG1se2ZvbnQtc2l6ZToxM3B4fQpib2R5e2ZvbnQtZmFtaWx5OidETSBTYW5zJyxzeXN0ZW0tdWksc2Fucy1zZXJpZjtiYWNrZ3JvdW5kOiNmMGYzZjc7Y29sb3I6IzFhMjMzNTttaW4taGVpZ2h0OjEwMHZoO2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW59CgovKiDilIDilIAgTEFZT1VUIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAqLwouYXBwLXNoZWxse2Rpc3BsYXk6ZmxleDtoZWlnaHQ6MTAwdmg7b3ZlcmZsb3c6aGlkZGVufQouc2lkZWJhcnt3aWR0aDoyMDBweDtiYWNrZ3JvdW5kOiMwZjFjMmU7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjtmbGV4LXNocmluazowO292ZXJmbG93LXk6YXV0b30KLm1haW4tYXJlYXtmbGV4OjE7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjtvdmVyZmxvdzpoaWRkZW47bWluLXdpZHRoOjB9Ci50b3BiYXJ7aGVpZ2h0OjUycHg7YmFja2dyb3VuZDp3aGl0ZTtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZGNlNmYwO2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7cGFkZGluZzowIDIwcHg7Z2FwOjEycHg7ZmxleC1zaHJpbms6MH0KLmNvbnRlbnR7ZmxleDoxO292ZXJmbG93LXk6YXV0bztwYWRkaW5nOjIwcHh9Ci5haS1kcmF3ZXJ7d2lkdGg6MjQwcHg7YmFja2dyb3VuZDojZjhmYWZjO2JvcmRlci1sZWZ0OjFweCBzb2xpZCAjZGNlNmYwO2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47ZmxleC1zaHJpbms6MH0KCi8qIOKUgOKUgCBTSURFQkFSIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAqLwouc2ItbG9nb3twYWRkaW5nOjE2cHggMTRweCAxNHB4O2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsMC4wNyl9Ci5zYi13b3JkbWFya3tmb250LXNpemU6MTVweDtmb250LXdlaWdodDo2MDA7Y29sb3I6d2hpdGU7bGV0dGVyLXNwYWNpbmc6LTAuMDJlbX0KLnNiLXdvcmRtYXJrIHNwYW57Y29sb3I6IzRhOTBkOX0KLnNiLXN1Yntmb250LXNpemU6MTBweDtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuMzUpO21hcmdpbi10b3A6MnB4fQouc2ItY29tcGFueXttYXJnaW46MTBweCAxMHB4IDZweDtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC4wNik7Ym9yZGVyOjAuNXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsMC4xKTtib3JkZXItcmFkaXVzOjhweDtwYWRkaW5nOjhweCAxMHB4fQouc2ItY29tcGFueS1uYW1le2ZvbnQtc2l6ZToxMXB4O2ZvbnQtd2VpZ2h0OjUwMDtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuODUpfQouc2ItY29tcGFueS1zdWJ7Zm9udC1zaXplOjlweDtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuMzUpO21hcmdpbi10b3A6MXB4fQouc2Itc2Vje2ZvbnQtc2l6ZTo5cHg7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjMpO3BhZGRpbmc6MTBweCAxNHB4IDRweDtsZXR0ZXItc3BhY2luZzowLjA3ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlfQouc2ItaXRlbXtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4cHg7cGFkZGluZzo4cHggMTRweDtjdXJzb3I6cG9pbnRlcjtmb250LXNpemU6MTJweDtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuNTUpO3Bvc2l0aW9uOnJlbGF0aXZlO3RyYW5zaXRpb246YWxsIDAuMTJzO2JvcmRlci1yYWRpdXM6MH0KLnNiLWl0ZW06aG92ZXJ7YmFja2dyb3VuZDpyZ2JhKDI1NSwyNTUsMjU1LDAuMDYpO2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsMC44NSl9Ci5zYi1pdGVtLmFjdGl2ZXtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC4wOCk7Y29sb3I6d2hpdGU7Zm9udC13ZWlnaHQ6NTAwfQouc2ItaXRlbS5hY3RpdmU6OmJlZm9yZXtjb250ZW50OicnO3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6MDt0b3A6NHB4O2JvdHRvbTo0cHg7d2lkdGg6MnB4O2JhY2tncm91bmQ6IzRhOTBkOTtib3JkZXItcmFkaXVzOjAgMnB4IDJweCAwfQouc2ItaWNvbntmb250LXNpemU6MTNweDt3aWR0aDoxNnB4O3RleHQtYWxpZ246Y2VudGVyO2ZsZXgtc2hyaW5rOjB9Ci5zYi1iYWRnZXttYXJnaW4tbGVmdDphdXRvO2ZvbnQtc2l6ZTo5cHg7cGFkZGluZzoxcHggNXB4O2JvcmRlci1yYWRpdXM6OHB4O2ZvbnQtd2VpZ2h0OjYwMH0KLnNiLWJhZGdlLWJsdWV7YmFja2dyb3VuZDpyZ2JhKDc0LDE0NCwyMTcsMC4yNSk7Y29sb3I6IzdlYjNlOH0KLnNiLWJhZGdlLXJlZHtiYWNrZ3JvdW5kOnJnYmEoMjIwLDUzLDY5LDAuMjUpO2NvbG9yOiNmZjhhOWF9Ci5zYi1iYWRnZS1ncmVlbntiYWNrZ3JvdW5kOnJnYmEoMzksMTc0LDk2LDAuMik7Y29sb3I6IzVkYmE4NX0KLnNiLWZvb3RlcnttYXJnaW4tdG9wOmF1dG87cGFkZGluZzoxMnB4IDE0cHg7Ym9yZGVyLXRvcDoxcHggc29saWQgcmdiYSgyNTUsMjU1LDI1NSwwLjA2KX0KLnNiLWdtYWlse2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjZweDtmb250LXNpemU6MTBweDtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuNCl9Ci5zYi1nbWFpbC1kb3R7d2lkdGg6NnB4O2hlaWdodDo2cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDojMjdhZTYwO2ZsZXgtc2hyaW5rOjB9CgovKiDilIDilIAgVE9QQkFSIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAqLwoudGItdGl0bGV7Zm9udC1zaXplOjE0cHg7Zm9udC13ZWlnaHQ6NTAwO2ZsZXg6MX0KLnRiLWpvYi1waWxse2ZvbnQtc2l6ZToxMXB4O3BhZGRpbmc6M3B4IDEwcHg7Ym9yZGVyLXJhZGl1czoyMHB4O2JhY2tncm91bmQ6I2VlZjVmZDtjb2xvcjojMTg1RkE1O2JvcmRlcjowLjVweCBzb2xpZCAjYjVkNGY0O2N1cnNvcjpwb2ludGVyO3RyYW5zaXRpb246YWxsIDAuMTVzfQoudGItam9iLXBpbGwuYWN0aXZle2JhY2tncm91bmQ6IzE4NUZBNTtjb2xvcjp3aGl0ZTtib3JkZXItY29sb3I6IzE4NUZBNX0KLmJ0bntwYWRkaW5nOjZweCAxNHB4O2ZvbnQtc2l6ZToxMXB4O2JvcmRlcjoxcHggc29saWQgI2RjZTZmMDtib3JkZXItcmFkaXVzOjhweDtjdXJzb3I6cG9pbnRlcjtiYWNrZ3JvdW5kOndoaXRlO2NvbG9yOiMxYTIzMzU7Zm9udC1mYW1pbHk6aW5oZXJpdDt0cmFuc2l0aW9uOmFsbCAwLjE1czt3aGl0ZS1zcGFjZTpub3dyYXB9Ci5idG46aG92ZXJ7YmFja2dyb3VuZDojZjBmNGY4O2JvcmRlci1jb2xvcjojYzVkNWU4fQouYnRuLXByaW1hcnl7YmFja2dyb3VuZDojMTg1RkE1O2NvbG9yOndoaXRlO2JvcmRlci1jb2xvcjojMTg1RkE1fQouYnRuLXByaW1hcnk6aG92ZXJ7YmFja2dyb3VuZDojMGQ0YThhfQouYnRuLWdyZWVue2JhY2tncm91bmQ6IzI3YWU2MDtjb2xvcjp3aGl0ZTtib3JkZXItY29sb3I6IzI3YWU2MH0KLmJ0bi1ncmVlbjpob3ZlcntiYWNrZ3JvdW5kOiMxZTg0NDl9Ci5idG4tc217cGFkZGluZzo0cHggMTBweDtmb250LXNpemU6MTBweH0KLmJ0bi1kYW5nZXJ7YmFja2dyb3VuZDojZmZmMGYwO2NvbG9yOiNjMDM5MmI7Ym9yZGVyLWNvbG9yOiNmNWM2YzZ9CgovKiDilIDilIAgVklFV1Mg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAICovCi52aWV3e2Rpc3BsYXk6bm9uZX0KLnZpZXcuYWN0aXZle2Rpc3BsYXk6YmxvY2t9CgovKiDilIDilIAgQ09NTU9OIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAqLwouY2FyZHtiYWNrZ3JvdW5kOndoaXRlO2JvcmRlcjoxcHggc29saWQgI2U4ZWVmNTtib3JkZXItcmFkaXVzOjEycHg7cGFkZGluZzoxNnB4O21hcmdpbi1ib3R0b206MTJweH0KLmNhcmQ6bGFzdC1jaGlsZHttYXJnaW4tYm90dG9tOjB9Ci5jYXJkLXRpdGxle2ZvbnQtc2l6ZToxMXB4O2ZvbnQtd2VpZ2h0OjYwMDtjb2xvcjojN2E4ZmE2O21hcmdpbi1ib3R0b206MTJweDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo2cHg7bGV0dGVyLXNwYWNpbmc6MC4wNGVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZX0KLmdyaWQtMntkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjFmciAxZnI7Z2FwOjEycHh9Ci5ncmlkLTN7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoMywxZnIpO2dhcDoxMHB4fQouZ3JpZC00e2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDQsMWZyKTtnYXA6OHB4fQouc2VjLXRpdGxle2ZvbnQtc2l6ZToxMnB4O2ZvbnQtd2VpZ2h0OjUwMDtjb2xvcjojN2E4ZmE2O21hcmdpbi1ib3R0b206MTBweDttYXJnaW4tdG9wOjE2cHh9Ci5zZWMtdGl0bGU6Zmlyc3QtY2hpbGR7bWFyZ2luLXRvcDowfQoudGFne2ZvbnQtc2l6ZToxMHB4O3BhZGRpbmc6MnB4IDhweDtib3JkZXItcmFkaXVzOjEwcHg7ZGlzcGxheTppbmxpbmUtYmxvY2t9Ci50YWctYmx1ZXtiYWNrZ3JvdW5kOiNlZWY1ZmQ7Y29sb3I6IzE4NUZBNX0KLnRhZy1ncmVlbntiYWNrZ3JvdW5kOiNlYWY2ZWY7Y29sb3I6IzFlODQ0OX0KLnRhZy1yZWR7YmFja2dyb3VuZDojZmZmMGYwO2NvbG9yOiNjMDM5MmJ9Ci50YWctYW1iZXJ7YmFja2dyb3VuZDojZmZmOGVlO2NvbG9yOiNkMzU0MDB9Ci50YWctcHVycGxle2JhY2tncm91bmQ6I2YzZjBmZjtjb2xvcjojNmMzZmM3fQoudGFnLWdyYXl7YmFja2dyb3VuZDojZjBmM2Y3O2NvbG9yOiM3YThmYTZ9Ci5pbmZvLWJveHtiYWNrZ3JvdW5kOiNlZWY1ZmQ7Ym9yZGVyLWxlZnQ6M3B4IHNvbGlkICMxODVGQTU7cGFkZGluZzo5cHggMTJweDtib3JkZXItcmFkaXVzOjAgOHB4IDhweCAwO2ZvbnQtc2l6ZToxMXB4O2NvbG9yOiMxODVGQTU7bGluZS1oZWlnaHQ6MS42O21hcmdpbjo4cHggMH0KLndhcm4tYm94e2JhY2tncm91bmQ6I2ZmZjBmMDtib3JkZXItbGVmdDozcHggc29saWQgI2MwMzkyYjtwYWRkaW5nOjlweCAxMnB4O2JvcmRlci1yYWRpdXM6MCA4cHggOHB4IDA7Zm9udC1zaXplOjExcHg7Y29sb3I6I2MwMzkyYjtsaW5lLWhlaWdodDoxLjY7bWFyZ2luOjhweCAwfQouZ29vZC1ib3h7YmFja2dyb3VuZDojZWFmNmVmO2JvcmRlci1sZWZ0OjNweCBzb2xpZCAjMjdhZTYwO3BhZGRpbmc6OXB4IDEycHg7Ym9yZGVyLXJhZGl1czowIDhweCA4cHggMDtmb250LXNpemU6MTFweDtjb2xvcjojMWU4NDQ5O2xpbmUtaGVpZ2h0OjEuNjttYXJnaW46OHB4IDB9Ci5pbnB1dC1maWVsZHt3aWR0aDoxMDAlO3BhZGRpbmc6OHB4IDExcHg7Zm9udC1zaXplOjEycHg7Ym9yZGVyOjFweCBzb2xpZCAjZGNlNmYwO2JvcmRlci1yYWRpdXM6OHB4O2JhY2tncm91bmQ6d2hpdGU7Y29sb3I6IzFhMjMzNTtvdXRsaW5lOm5vbmU7Zm9udC1mYW1pbHk6aW5oZXJpdDt0cmFuc2l0aW9uOmJvcmRlci1jb2xvciAwLjE1c30KLmlucHV0LWZpZWxkOmZvY3Vze2JvcmRlci1jb2xvcjojMTg1RkE1fQoudGV4dGFyZWEtZmllbGR7d2lkdGg6MTAwJTttaW4taGVpZ2h0OjkwcHg7cGFkZGluZzo5cHggMTFweDtmb250LXNpemU6MTJweDtib3JkZXI6MXB4IHNvbGlkICNkY2U2ZjA7Ym9yZGVyLXJhZGl1czo4cHg7YmFja2dyb3VuZDp3aGl0ZTtjb2xvcjojMWEyMzM1O291dGxpbmU6bm9uZTtmb250LWZhbWlseTppbmhlcml0O3Jlc2l6ZTp2ZXJ0aWNhbDtsaW5lLWhlaWdodDoxLjU7dHJhbnNpdGlvbjpib3JkZXItY29sb3IgMC4xNXN9Ci50ZXh0YXJlYS1maWVsZDpmb2N1c3tib3JkZXItY29sb3I6IzE4NUZBNX0KLmxhYmVse2ZvbnQtc2l6ZToxMXB4O2NvbG9yOiM3YThmYTY7bWFyZ2luLWJvdHRvbTo0cHg7ZGlzcGxheTpibG9jaztmb250LXdlaWdodDo1MDB9Ci5mb3JtLXJvd3ttYXJnaW4tYm90dG9tOjEycHh9CgovKiDilIDilIAgU1RBVFMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAICovCi5zdGF0LWdyaWR7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoNCwxZnIpO2dhcDoxMHB4O21hcmdpbi1ib3R0b206MTZweH0KLnN0YXQtY2FyZHtiYWNrZ3JvdW5kOndoaXRlO2JvcmRlcjoxcHggc29saWQgI2U4ZWVmNTtib3JkZXItcmFkaXVzOjEycHg7cGFkZGluZzoxNHB4fQouc3RhdC12YWx7Zm9udC1zaXplOjI0cHg7Zm9udC13ZWlnaHQ6NjAwO2xpbmUtaGVpZ2h0OjF9Ci5zdGF0LWxibHtmb250LXNpemU6MTBweDtjb2xvcjojN2E4ZmE2O21hcmdpbi10b3A6NHB4fQouc3RhdC1kZWx0YXtmb250LXNpemU6MTBweDttYXJnaW4tdG9wOjNweH0KLmRlbHRhLXVwe2NvbG9yOiMyN2FlNjB9Ci5kZWx0YS1kbntjb2xvcjojYzAzOTJifQoKLyog4pSA4pSAIFBJUEVMSU5FIFRBQkxFIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAqLwoucHRhYmxle3dpZHRoOjEwMCU7Ym9yZGVyLWNvbGxhcHNlOmNvbGxhcHNlfQoucHRhYmxlIHRoe2ZvbnQtc2l6ZToxMHB4O2NvbG9yOiM3YThmYTY7dGV4dC1hbGlnbjpsZWZ0O3BhZGRpbmc6NnB4IDhweDtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZThlZWY1O2ZvbnQtd2VpZ2h0OjYwMDtsZXR0ZXItc3BhY2luZzowLjAzZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlfQoucHRhYmxlIHRke3BhZGRpbmc6OXB4IDhweDtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZjBmM2Y3O2ZvbnQtc2l6ZToxMnB4O3ZlcnRpY2FsLWFsaWduOm1pZGRsZX0KLnB0YWJsZSB0cjpob3ZlciB0ZHtiYWNrZ3JvdW5kOiNmYWZiZmN9Ci5wdGFibGUgdHI6bGFzdC1jaGlsZCB0ZHtib3JkZXItYm90dG9tOm5vbmV9Ci5hdnt3aWR0aDoyOHB4O2hlaWdodDoyOHB4O2JvcmRlci1yYWRpdXM6NTAlO2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtmb250LXNpemU6MTBweDtmb250LXdlaWdodDo2MDA7ZmxleC1zaHJpbms6MH0KLmF2LXR7YmFja2dyb3VuZDojZTFmNWVlO2NvbG9yOiMwZTZlNGZ9Ci5hdi1ie2JhY2tncm91bmQ6I2VlZjVmZDtjb2xvcjojMTg1RkE1fQouYXYtcHtiYWNrZ3JvdW5kOiNmMGVkZmY7Y29sb3I6IzZjM2ZjN30KLmF2LWF7YmFja2dyb3VuZDojZmZmNmU4O2NvbG9yOiNiNzU5MGF9Ci5hdi1ye2JhY2tncm91bmQ6I2ZmZjBmMDtjb2xvcjojYzAzOTJifQoubmN7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OHB4fQouc3B7Zm9udC1zaXplOjEwcHg7cGFkZGluZzoycHggOHB4O2JvcmRlci1yYWRpdXM6MTBweDtkaXNwbGF5OmlubGluZS1ibG9jaztmb250LXdlaWdodDo1MDB9Ci5zcC1uZXd7YmFja2dyb3VuZDojZWVmNWZkO2NvbG9yOiMxODVGQTV9Ci5zcC1zZW50e2JhY2tncm91bmQ6I2YwZWRmZjtjb2xvcjojNmMzZmM3fQouc3Atb3BlbntiYWNrZ3JvdW5kOiNmZmY4ZWU7Y29sb3I6I2QzNTQwMH0KLnNwLXJlcHtiYWNrZ3JvdW5kOiNlYWY2ZWY7Y29sb3I6IzFlODQ0OX0KLnNwLWludHtiYWNrZ3JvdW5kOiNlMWY1ZWU7Y29sb3I6IzBlNmU0Zn0KLm1hdGNoLWF7YmFja2dyb3VuZDojZWFmNmVmO2NvbG9yOiMxZTg0NDk7Zm9udC1zaXplOjEwcHg7cGFkZGluZzoycHggOHB4O2JvcmRlci1yYWRpdXM6MTBweDtmb250LXdlaWdodDo2MDB9Ci5tYXRjaC1ie2JhY2tncm91bmQ6I2VlZjVmZDtjb2xvcjojMTg1RkE1O2ZvbnQtc2l6ZToxMHB4O3BhZGRpbmc6MnB4IDhweDtib3JkZXItcmFkaXVzOjEwcHg7Zm9udC13ZWlnaHQ6NjAwfQouc3JjLXBpbGx7Zm9udC1zaXplOjlweDtwYWRkaW5nOjFweCA2cHg7Ym9yZGVyLXJhZGl1czo4cHg7Zm9udC13ZWlnaHQ6NTAwfQouc3JjLWxpe2JhY2tncm91bmQ6I2VlZjVmZDtjb2xvcjojMTg1RkE1fQouc3JjLWFwe2JhY2tncm91bmQ6I2YwZWRmZjtjb2xvcjojNmMzZmM3fQouc3JjLWlue2JhY2tncm91bmQ6I2VhZjZlZjtjb2xvcjojMWU4NDQ5fQouc3JjLXhpe2JhY2tncm91bmQ6I2ZmZjhlZTtjb2xvcjojZDM1NDAwfQoucmVnLXBpbGx7Zm9udC1zaXplOjlweDtwYWRkaW5nOjFweCA2cHg7Ym9yZGVyLXJhZGl1czo4cHg7YmFja2dyb3VuZDojZjBmM2Y3O2NvbG9yOiM3YThmYTZ9CgovKiDilIDilIAgVVBMT0FEIFpPTkUg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAICovCi51cGxvYWQtem9uZXtib3JkZXI6MnB4IGRhc2hlZCAjYzVkNWU4O2JvcmRlci1yYWRpdXM6MTJweDtwYWRkaW5nOjMycHggMjBweDt0ZXh0LWFsaWduOmNlbnRlcjtjdXJzb3I6cG9pbnRlcjt0cmFuc2l0aW9uOmFsbCAwLjJzO2JhY2tncm91bmQ6d2hpdGU7cG9zaXRpb246cmVsYXRpdmV9Ci51cGxvYWQtem9uZTpob3ZlciwudXBsb2FkLXpvbmUuZHJhZy1vdmVye2JvcmRlci1jb2xvcjojMTg1RkE1O2JhY2tncm91bmQ6I2Y0ZjhmZn0KLnVwbG9hZC1pY29ue2ZvbnQtc2l6ZTozNnB4O21hcmdpbi1ib3R0b206MTBweH0KLnVwbG9hZC10aXRsZXtmb250LXNpemU6MTRweDtmb250LXdlaWdodDo1MDA7bWFyZ2luLWJvdHRvbTo0cHh9Ci51cGxvYWQtc3Vie2ZvbnQtc2l6ZToxMnB4O2NvbG9yOiM3YThmYTZ9CiNmaWxlLWlucHV0e2Rpc3BsYXk6bm9uZX0KLnJlcS1jaGlwe2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo1cHg7Zm9udC1zaXplOjExcHg7cGFkZGluZzo0cHggMTBweDtib3JkZXItcmFkaXVzOjhweDtiYWNrZ3JvdW5kOiNmMGYzZjc7Ym9yZGVyOjFweCBzb2xpZCAjZTBlOGYwO21hcmdpbjozcHh9Ci5yZXEtY2hpcC5tdXN0e2JhY2tncm91bmQ6I2VhZjZlZjtib3JkZXItY29sb3I6I2E4ZDliYztjb2xvcjojMWU4NDQ5fQoucmVxLWNoaXAubmljZXtiYWNrZ3JvdW5kOiNlZWY1ZmQ7Ym9yZGVyLWNvbG9yOiNiNWQ0ZjQ7Y29sb3I6IzE4NUZBNX0KLnJlcS1jaGlwLmF2b2lke2JhY2tncm91bmQ6I2ZmZjBmMDtib3JkZXItY29sb3I6I2Y1YzZjNjtjb2xvcjojYzAzOTJifQouam9iLWNhcmR7Ym9yZGVyOjFweCBzb2xpZCAjZThlZWY1O2JvcmRlci1yYWRpdXM6MTJweDtwYWRkaW5nOjE0cHg7bWFyZ2luLWJvdHRvbToxMHB4O2JhY2tncm91bmQ6d2hpdGU7Y3Vyc29yOnBvaW50ZXI7dHJhbnNpdGlvbjpib3JkZXItY29sb3IgMC4xNXN9Ci5qb2ItY2FyZDpob3Zlcntib3JkZXItY29sb3I6IzE4NUZBNX0KLmpvYi1jYXJkLnNlbGVjdGVke2JvcmRlci1jb2xvcjojMTg1RkE1O2JvcmRlci13aWR0aDoycHg7YmFja2dyb3VuZDojZjhmYmZmfQouam9iLXN0YXR1cy1hY3RpdmV7YmFja2dyb3VuZDojZWFmNmVmO2NvbG9yOiMxZTg0NDk7Zm9udC1zaXplOjEwcHg7cGFkZGluZzoycHggOHB4O2JvcmRlci1yYWRpdXM6MTBweDtmb250LXdlaWdodDo1MDB9Ci5qb2Itc3RhdHVzLWRyYWZ0e2JhY2tncm91bmQ6I2ZmZjhlZTtjb2xvcjojZDM1NDAwO2ZvbnQtc2l6ZToxMHB4O3BhZGRpbmc6MnB4IDhweDtib3JkZXItcmFkaXVzOjEwcHg7Zm9udC13ZWlnaHQ6NTAwfQoKLyog4pSA4pSAIFNPVVJDSU5HIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAqLwouc291cmNlLWNhcmR7Ym9yZGVyLXJhZGl1czoxMHB4O3BhZGRpbmc6MTJweCAxNHB4O21hcmdpbi1ib3R0b206OHB4O2JvcmRlcjoxcHggc29saWQgdHJhbnNwYXJlbnQ7cG9zaXRpb246cmVsYXRpdmV9Ci5zb3VyY2UtY2FyZC50aWVyMXtiYWNrZ3JvdW5kOiNmMGY5ZWE7Ym9yZGVyLWNvbG9yOiNiOGRkYTB9Ci5zb3VyY2UtY2FyZC50aWVyMntiYWNrZ3JvdW5kOiNlZWY1ZmQ7Ym9yZGVyLWNvbG9yOiNiNWQ0ZjR9Ci5zb3VyY2UtY2FyZC5hdm9pZHtiYWNrZ3JvdW5kOiNmZmY1ZjU7Ym9yZGVyLWNvbG9yOiNmNWM2YzZ9Ci5zcmMtcmFua3twb3NpdGlvbjphYnNvbHV0ZTt0b3A6MTBweDtyaWdodDoxMHB4O2ZvbnQtc2l6ZTo5cHg7cGFkZGluZzoycHggOHB4O2JvcmRlci1yYWRpdXM6OHB4O2ZvbnQtd2VpZ2h0OjcwMH0KLnItYmVzdHtiYWNrZ3JvdW5kOiMyN2FlNjA7Y29sb3I6d2hpdGV9Ci5yLWdvb2R7YmFja2dyb3VuZDojMTg1RkE1O2NvbG9yOndoaXRlfQouci1hdm9pZHtiYWNrZ3JvdW5kOiNjMDM5MmI7Y29sb3I6d2hpdGV9Ci5jaGlwe2ZvbnQtc2l6ZToxMHB4O3BhZGRpbmc6MnB4IDdweDtib3JkZXItcmFkaXVzOjhweDtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC44KTtib3JkZXI6MC41cHggc29saWQgI2UwZThmMDtjb2xvcjojMWEyMzM1O2Rpc3BsYXk6aW5saW5lLWJsb2NrO21hcmdpbjoycHh9Ci5ib29sLWJveHtiYWNrZ3JvdW5kOiNmNGY2Zjk7Ym9yZGVyLXJhZGl1czo4cHg7cGFkZGluZzoxMHB4IDEycHg7Zm9udC1mYW1pbHk6J0NvdXJpZXIgTmV3Jyxtb25vc3BhY2U7Zm9udC1zaXplOjEwcHg7Y29sb3I6IzE4NUZBNTtsaW5lLWhlaWdodDoxLjc7d29yZC1icmVhazpicmVhay1hbGw7bWFyZ2luOjhweCAwO2JvcmRlcjoxcHggc29saWQgI2UwZThmMH0KCi8qIOKUgOKUgCBFTUFJTCAvIFNFUVVFTkNFUyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KLnNlcS10cmFja3tkaXNwbGF5OmZsZXg7cG9zaXRpb246cmVsYXRpdmU7bWFyZ2luOjE2cHggMH0KLnNlcS10cmFjazo6YmVmb3Jle2NvbnRlbnQ6Jyc7cG9zaXRpb246YWJzb2x1dGU7dG9wOjE4cHg7bGVmdDoxOHB4O3JpZ2h0OjE4cHg7aGVpZ2h0OjFweDtiYWNrZ3JvdW5kOiNlOGVlZjV9Ci5zZXEtc3RlcHtmbGV4OjE7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjRweDtwb3NpdGlvbjpyZWxhdGl2ZTt6LWluZGV4OjE7Y3Vyc29yOnBvaW50ZXJ9Ci5zZXEtY2lyY2xle3dpZHRoOjM2cHg7aGVpZ2h0OjM2cHg7Ym9yZGVyLXJhZGl1czo1MCU7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2ZvbnQtc2l6ZToxMXB4O2ZvbnQtd2VpZ2h0OjYwMDtib3JkZXI6MXB4IHNvbGlkICNlOGVlZjU7YmFja2dyb3VuZDp3aGl0ZTt0cmFuc2l0aW9uOmFsbCAwLjE1c30KLnNlcS1jaXJjbGU6aG92ZXJ7Ym9yZGVyLWNvbG9yOiMxODVGQTU7YmFja2dyb3VuZDojZWVmNWZkfQouc2VxLWNpcmNsZS5zLWRvbmV7YmFja2dyb3VuZDojZWFmNmVmO2JvcmRlci1jb2xvcjojNWRiYTg1O2NvbG9yOiMxZTg0NDl9Ci5zZXEtY2lyY2xlLnMtYWN0aXZle2JhY2tncm91bmQ6IzE4NUZBNTtjb2xvcjp3aGl0ZTtib3JkZXItY29sb3I6IzE4NUZBNX0KLnNlcS1jaXJjbGUucy1wZW5ke2JhY2tncm91bmQ6I2YwZjNmNztjb2xvcjojYTBiMGMwO2JvcmRlci1jb2xvcjojZTBlOGYwfQouc2VxLWxibHtmb250LXNpemU6MTBweDtjb2xvcjojN2E4ZmE2O3RleHQtYWxpZ246Y2VudGVyfQouc2VxLWRheXtmb250LXNpemU6OXB4O2NvbG9yOiNiMGJjYzh9Ci5pbmJveC1pdGVte2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHg7cGFkZGluZzoxMHB4IDEycHg7Ym9yZGVyOjFweCBzb2xpZCAjZThlZWY1O2JvcmRlci1yYWRpdXM6MTBweDttYXJnaW4tYm90dG9tOjZweDtjdXJzb3I6cG9pbnRlcjt0cmFuc2l0aW9uOmJvcmRlci1jb2xvciAwLjE1cztiYWNrZ3JvdW5kOndoaXRlfQouaW5ib3gtaXRlbTpob3Zlcntib3JkZXItY29sb3I6I2M1ZDVlOH0KLmluYm94LWl0ZW0udW5yZWFke2JvcmRlci1sZWZ0OjNweCBzb2xpZCAjMTg1RkE1fQouaW5ib3gtaXRlbS5yZXBsaWVke2JvcmRlci1sZWZ0OjNweCBzb2xpZCAjMjdhZTYwfQoKLyog4pSA4pSAIENBTEVOREFSIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAqLwouY2FsLW1pbml7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoNywxZnIpO2dhcDoycHg7bWFyZ2luOjhweCAwfQouY2FsLWhkcntmb250LXNpemU6OXB4O2NvbG9yOiNiMGJjYzg7dGV4dC1hbGlnbjpjZW50ZXI7cGFkZGluZzoycHh9Ci5jYWwtZGF5e3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6NXB4IDJweDtmb250LXNpemU6MTBweDtib3JkZXItcmFkaXVzOjZweDtjdXJzb3I6cG9pbnRlcjt0cmFuc2l0aW9uOmJhY2tncm91bmQgMC4xMnN9Ci5jYWwtZGF5OmhvdmVye2JhY2tncm91bmQ6I2YwZjNmN30KLmNhbC1kYXkuaGFzLWV2e2JhY2tncm91bmQ6I2VlZjVmZDtjb2xvcjojMTg1RkE1O2ZvbnQtd2VpZ2h0OjYwMH0KLmNhbC1kYXkudG9kYXl7YmFja2dyb3VuZDojMTg1RkE1O2NvbG9yOndoaXRlO2ZvbnQtd2VpZ2h0OjYwMH0KLmV2LWl0ZW17ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjEwcHggMTJweDtib3JkZXI6MXB4IHNvbGlkICNlOGVlZjU7Ym9yZGVyLXJhZGl1czoxMHB4O21hcmdpbi1ib3R0b206OHB4O2JhY2tncm91bmQ6d2hpdGV9Ci5ldi10aW1le2ZvbnQtc2l6ZToxMXB4O2ZvbnQtd2VpZ2h0OjYwMDtjb2xvcjojMTg1RkE1O3dpZHRoOjcycHg7ZmxleC1zaHJpbms6MH0KLmV2LWJhZGdle2ZvbnQtc2l6ZTo5cHg7cGFkZGluZzoycHggN3B4O2JvcmRlci1yYWRpdXM6OHB4O2ZsZXgtc2hyaW5rOjB9Ci5ldi1tZWV0e2JhY2tncm91bmQ6I2VlZjVmZDtjb2xvcjojMTg1RkE1fQouZXYtcGhvbmV7YmFja2dyb3VuZDojZWFmNmVmO2NvbG9yOiMxZTg0NDl9Ci5ldi1hdXRve2JhY2tncm91bmQ6I2YwZWRmZjtjb2xvcjojNmMzZmM3fQoKLyog4pSA4pSAIElOVEVMTElHRU5DRSDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KLmRuYS1iYW5uZXJ7YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoMTM1ZGVnLCMwZjFjMmUgMCUsIzFhMzA1MCAxMDAlKTtib3JkZXItcmFkaXVzOjE0cHg7cGFkZGluZzoyMHB4IDIycHg7Y29sb3I6d2hpdGU7bWFyZ2luLWJvdHRvbToxMnB4O3Bvc2l0aW9uOnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbn0KLmRuYS1iYW5uZXI6OmJlZm9yZXtjb250ZW50OicnO3Bvc2l0aW9uOmFic29sdXRlO3RvcDotODBweDtyaWdodDotODBweDt3aWR0aDoyMjBweDtoZWlnaHQ6MjIwcHg7YmFja2dyb3VuZDpyYWRpYWwtZ3JhZGllbnQoY2lyY2xlLHJnYmEoNzQsMTQ0LDIxNywwLjEyKSx0cmFuc3BhcmVudCA3MCUpfQouZG5hLWtwaXN7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoNCwxZnIpO2dhcDo4cHg7bWFyZ2luLXRvcDoxNHB4fQouZG5hLWtwaXtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC4wNyk7Ym9yZGVyOjAuNXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsMC4xKTtib3JkZXItcmFkaXVzOjEwcHg7cGFkZGluZzoxMHB4IDEycHg7dGV4dC1hbGlnbjpjZW50ZXJ9Ci5kbmEta3BpLXZ7Zm9udC1zaXplOjIwcHg7Zm9udC13ZWlnaHQ6NzAwO2NvbG9yOndoaXRlfQouZG5hLWtwaS1se2ZvbnQtc2l6ZTo5cHg7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwwLjQpO21hcmdpbi10b3A6MnB4O2xpbmUtaGVpZ2h0OjEuM30KLmZsYWctaXRlbXtkaXNwbGF5OmZsZXg7Z2FwOjEwcHg7cGFkZGluZzo5cHggMDtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZjBmM2Y3fQouZmxhZy1pdGVtOmxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTpub25lfQouZmxhZy1pY29ue2ZvbnQtc2l6ZToxNnB4O2ZsZXgtc2hyaW5rOjA7bWFyZ2luLXRvcDoycHh9Ci5mbGFnLXRpdGxle2ZvbnQtc2l6ZToxMXB4O2ZvbnQtd2VpZ2h0OjUwMDttYXJnaW4tYm90dG9tOjJweH0KLmZsYWctZGVzY3tmb250LXNpemU6MTBweDtjb2xvcjojN2E4ZmE2O2xpbmUtaGVpZ2h0OjEuNX0KLmZsYWctc2lne2ZvbnQtc2l6ZToxMHB4O21hcmdpbi10b3A6M3B4O3BhZGRpbmc6MnB4IDdweDtib3JkZXItcmFkaXVzOjZweDtkaXNwbGF5OmlubGluZS1ibG9ja30KLnNpZy1ye2JhY2tncm91bmQ6I2ZmZjBmMDtjb2xvcjojYzAzOTJifQouc2lnLWd7YmFja2dyb3VuZDojZWFmNmVmO2NvbG9yOiMxZTg0NDl9Ci5tYXRjaC1iYXItcm93e21hcmdpbi1ib3R0b206OXB4fQoubWItbGFiZWxze2Rpc3BsYXk6ZmxleDtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjttYXJnaW4tYm90dG9tOjNweH0KLm1iLWxibHtmb250LXNpemU6MTBweDtjb2xvcjojN2E4ZmE2fQoubWItcGN0e2ZvbnQtc2l6ZToxMHB4O2ZvbnQtd2VpZ2h0OjYwMH0KLm1iLXRyYWNre2hlaWdodDo1cHg7YmFja2dyb3VuZDojZThlZWY1O2JvcmRlci1yYWRpdXM6MTBweDtvdmVyZmxvdzpoaWRkZW59Ci5tYi1maWxse2hlaWdodDoxMDAlO2JvcmRlci1yYWRpdXM6MTBweDt0cmFuc2l0aW9uOndpZHRoIDAuOHMgZWFzZX0KCi8qIOKUgOKUgCBDViBTQ1JFRU5FUiDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KLmN2LXJlc3VsdHtiYWNrZ3JvdW5kOiNmOGZhZmM7Ym9yZGVyLXJhZGl1czoxMHB4O3BhZGRpbmc6MTRweDttYXJnaW4tdG9wOjEwcHg7Zm9udC1zaXplOjEycHg7bGluZS1oZWlnaHQ6MS43O2Rpc3BsYXk6bm9uZTtib3JkZXI6MXB4IHNvbGlkICNlOGVlZjV9Ci5jdi1yZXN1bHQuc2hvd3tkaXNwbGF5OmJsb2NrfQoKLyog4pSA4pSAIEFJIERSQVdFUiDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KLmFpLWRyYXdlci1oZWFkZXJ7cGFkZGluZzoxNHB4IDE0cHggMTBweDtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZThlZWY1O2ZsZXgtc2hyaW5rOjB9Ci5haS1kcmF3ZXItdGl0bGV7Zm9udC1zaXplOjEycHg7Zm9udC13ZWlnaHQ6NjAwO2NvbG9yOiMxYTIzMzV9Ci5haS1kcmF3ZXItc3Vie2ZvbnQtc2l6ZToxMHB4O2NvbG9yOiM3YThmYTY7bWFyZ2luLXRvcDoxcHh9Ci5haS1tc2dze2ZsZXg6MTtvdmVyZmxvdy15OmF1dG87cGFkZGluZzoxMHB4O2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjZweH0KLmFte2JhY2tncm91bmQ6d2hpdGU7Ym9yZGVyOjFweCBzb2xpZCAjZThlZWY1O2JvcmRlci1yYWRpdXM6OHB4O3BhZGRpbmc6OHB4IDEwcHg7Zm9udC1zaXplOjExcHg7bGluZS1oZWlnaHQ6MS41NTtjb2xvcjojNWE2YTdlfQouYW0udXtiYWNrZ3JvdW5kOiNlZWY1ZmQ7Ym9yZGVyLWNvbG9yOiNiNWQ0ZjQ7Y29sb3I6IzE4NUZBNTt0ZXh0LWFsaWduOnJpZ2h0fQouYW0ubHtjb2xvcjojYTBiMGMwO2ZvbnQtc3R5bGU6aXRhbGljfQouYWktcXVpY2t7cGFkZGluZzo4cHggMTBweDtib3JkZXItdG9wOjFweCBzb2xpZCAjZThlZWY1O2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjRweDtmbGV4LXNocmluazowfQoucWJ7Zm9udC1zaXplOjEwcHg7cGFkZGluZzo1cHggOHB4O2JvcmRlcjoxcHggc29saWQgI2U4ZWVmNTtib3JkZXItcmFkaXVzOjdweDtjdXJzb3I6cG9pbnRlcjtiYWNrZ3JvdW5kOndoaXRlO2NvbG9yOiM1YTZhN2U7dGV4dC1hbGlnbjpsZWZ0O2ZvbnQtZmFtaWx5OmluaGVyaXQ7dHJhbnNpdGlvbjphbGwgMC4xNXM7bGluZS1oZWlnaHQ6MS4zfQoucWI6aG92ZXJ7YmFja2dyb3VuZDojZjBmNGY4O2NvbG9yOiMxYTIzMzU7Ym9yZGVyLWNvbG9yOiNjNWQ1ZTh9Ci5haS1pbnAtcm93e3BhZGRpbmc6MTBweDtib3JkZXItdG9wOjFweCBzb2xpZCAjZThlZWY1O2Rpc3BsYXk6ZmxleDtnYXA6NXB4O2ZsZXgtc2hyaW5rOjB9Ci5haS1pbnB7ZmxleDoxO3BhZGRpbmc6N3B4IDEwcHg7Zm9udC1zaXplOjExcHg7Ym9yZGVyOjFweCBzb2xpZCAjZGNlNmYwO2JvcmRlci1yYWRpdXM6OHB4O2JhY2tncm91bmQ6d2hpdGU7Y29sb3I6IzFhMjMzNTtvdXRsaW5lOm5vbmU7Zm9udC1mYW1pbHk6aW5oZXJpdH0KLmFpLWlucDpmb2N1c3tib3JkZXItY29sb3I6IzE4NUZBNX0KLmFpLXNlbmR7cGFkZGluZzo3cHggMTBweDtiYWNrZ3JvdW5kOiMxODVGQTU7Y29sb3I6d2hpdGU7Ym9yZGVyOm5vbmU7Ym9yZGVyLXJhZGl1czo4cHg7Y3Vyc29yOnBvaW50ZXI7Zm9udC1zaXplOjExcHg7Zm9udC1mYW1pbHk6aW5oZXJpdDt0cmFuc2l0aW9uOmJhY2tncm91bmQgMC4xNXN9Ci5haS1zZW5kOmhvdmVye2JhY2tncm91bmQ6IzBkNGE4YX0KCi8qIOKUgOKUgCBTRVRVUCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KLnNldHVwLXN0ZXB7YmFja2dyb3VuZDp3aGl0ZTtib3JkZXI6MXB4IHNvbGlkICNlOGVlZjU7Ym9yZGVyLXJhZGl1czoxMnB4O3BhZGRpbmc6MTZweDttYXJnaW4tYm90dG9tOjEwcHh9Ci5zZXR1cC1zdGVwLmRvbmV7Ym9yZGVyLWNvbG9yOiNhOGQ5YmM7YmFja2dyb3VuZDojZjZmZGY5fQouc2V0dXAtc3RlcC5hY3RpdmUtc3RlcHtib3JkZXItY29sb3I6I2I1ZDRmNDtiYWNrZ3JvdW5kOiNmNGY4ZmZ9Ci5zdGVwLWhkcntkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMnB4fQouc3RlcC1udW17d2lkdGg6MzBweDtoZWlnaHQ6MzBweDtib3JkZXItcmFkaXVzOjUwJTtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Zm9udC1zaXplOjEycHg7Zm9udC13ZWlnaHQ6NjAwO2ZsZXgtc2hyaW5rOjB9Ci5zbi1kb25le2JhY2tncm91bmQ6IzI3YWU2MDtjb2xvcjp3aGl0ZX0KLnNuLWFjdGl2ZXtiYWNrZ3JvdW5kOiMxODVGQTU7Y29sb3I6d2hpdGV9Ci5zbi1wZW5ke2JhY2tncm91bmQ6I2YwZjNmNztib3JkZXI6MXB4IHNvbGlkICNkY2U2ZjA7Y29sb3I6I2EwYjBjMH0KLnN0ZXAtYm9keXttYXJnaW4tdG9wOjEycHg7cGFkZGluZy10b3A6MTJweDtib3JkZXItdG9wOjFweCBzb2xpZCAjZThlZWY1fQo8L3N0eWxlPgo8L2hlYWQ+Cjxib2R5Pgo8ZGl2IGNsYXNzPSJhcHAtc2hlbGwiPgoKPCEtLSDilZDilZDilZAgU0lERUJBUiDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAgLS0+CjxkaXYgY2xhc3M9InNpZGViYXIiPgogIDxkaXYgY2xhc3M9InNiLWxvZ28iPgogICAgPGRpdiBjbGFzcz0ic2Itd29yZG1hcmsiPkNvbnZlPHNwYW4+bmV4PC9zcGFuPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ic2Itc3ViIj5SZWNydWl0ZXIgwrcgQ1lRVUVPIEVkaXRpb248L2Rpdj4KICA8L2Rpdj4KICA8ZGl2IGNsYXNzPSJzYi1jb21wYW55Ij4KICAgIDxkaXYgY2xhc3M9InNiLWNvbXBhbnktbmFtZSI+Q1lRVUVPIEdtYkg8L2Rpdj4KICAgIDxkaXYgY2xhc3M9InNiLWNvbXBhbnktc3ViIj5jeXF1ZW8uY29tIMK3IE3DvG5jaGVuIMK3IERFLXdlaXQ8L2Rpdj4KICA8L2Rpdj4KICA8ZGl2IGNsYXNzPSJzYi1zZWMiPlN0ZWxsZW48L2Rpdj4KICA8ZGl2IGNsYXNzPSJzYi1pdGVtIiBvbmNsaWNrPSJzaG93Vmlldygnam9icycpIj4KICAgIDxzcGFuIGNsYXNzPSJzYi1pY29uIj7wn5OLPC9zcGFuPiBTdGVsbGVudmVyd2FsdHVuZwogICAgPHNwYW4gY2xhc3M9InNiLWJhZGdlIHNiLWJhZGdlLWdyZWVuIj4yPC9zcGFuPgogIDwvZGl2PgogIDxkaXYgY2xhc3M9InNiLXNlYyI+S2FuZGlkYXRlbjwvZGl2PgogIDxkaXYgY2xhc3M9InNiLWl0ZW0gYWN0aXZlIiBvbmNsaWNrPSJzaG93VmlldygncGlwZWxpbmUnKSI+CiAgICA8c3BhbiBjbGFzcz0ic2ItaWNvbiI+4peIPC9zcGFuPiBQaXBlbGluZQogICAgPHNwYW4gY2xhc3M9InNiLWJhZGdlIHNiLWJhZGdlLWJsdWUiPjUyPC9zcGFuPgogIDwvZGl2PgogIDxkaXYgY2xhc3M9InNiLWl0ZW0iIG9uY2xpY2s9InNob3dWaWV3KCdzb3VyY2luZycpIj4KICAgIDxzcGFuIGNsYXNzPSJzYi1pY29uIj7iipU8L3NwYW4+IFNvdXJjaW5nCiAgPC9kaXY+CiAgPGRpdiBjbGFzcz0ic2Itc2VjIj5Lb21tdW5pa2F0aW9uPC9kaXY+CiAgPGRpdiBjbGFzcz0ic2ItaXRlbSIgb25jbGljaz0ic2hvd1ZpZXcoJ2VtYWlsJykiPgogICAgPHNwYW4gY2xhc3M9InNiLWljb24iPuKciTwvc3Bhbj4gRS1NYWlsIFNlcXVlbnplbgogICAgPHNwYW4gY2xhc3M9InNiLWJhZGdlIHNiLWJhZGdlLXJlZCI+NTwvc3Bhbj4KICA8L2Rpdj4KICA8ZGl2IGNsYXNzPSJzYi1pdGVtIiBvbmNsaWNrPSJzaG93VmlldygnaW5ib3gnKSI+CiAgICA8c3BhbiBjbGFzcz0ic2ItaWNvbiI+8J+TpTwvc3Bhbj4gQW50d29ydGVuCiAgICA8c3BhbiBjbGFzcz0ic2ItYmFkZ2Ugc2ItYmFkZ2UtcmVkIj40PC9zcGFuPgogIDwvZGl2PgogIDxkaXYgY2xhc3M9InNiLXNlYyI+QWJzY2hsdXNzPC9kaXY+CiAgPGRpdiBjbGFzcz0ic2ItaXRlbSIgb25jbGljaz0ic2hvd1ZpZXcoJ2NhbGVuZGFyJykiPgogICAgPHNwYW4gY2xhc3M9InNiLWljb24iPuKXtzwvc3Bhbj4gS2FsZW5kZXIKICAgIDxzcGFuIGNsYXNzPSJzYi1iYWRnZSBzYi1iYWRnZS1ncmVlbiI+NTwvc3Bhbj4KICA8L2Rpdj4KICA8ZGl2IGNsYXNzPSJzYi1zZWMiPkVpbnN0ZWxsdW5nZW48L2Rpdj4KICA8ZGl2IGNsYXNzPSJzYi1pdGVtIiBvbmNsaWNrPSJzaG93VmlldygnaW50ZWxsaWdlbmNlJykiPgogICAgPHNwYW4gY2xhc3M9InNiLWljb24iPvCfp6A8L3NwYW4+IENvbXBhbnkgSW50ZWwKICA8L2Rpdj4KICA8ZGl2IGNsYXNzPSJzYi1pdGVtIiBvbmNsaWNrPSJzaG93Vmlldygnc2V0dXAnKSI+CiAgICA8c3BhbiBjbGFzcz0ic2ItaWNvbiI+4pqZPC9zcGFuPiBHbWFpbCBTZXR1cAogICAgPHNwYW4gY2xhc3M9InNiLWJhZGdlIHNiLWJhZGdlLXJlZCIgaWQ9InNldHVwLWJhZGdlIj4hPC9zcGFuPgogIDwvZGl2PgogIDxkaXYgY2xhc3M9InNiLWZvb3RlciI+CiAgICA8ZGl2IGNsYXNzPSJzYi1nbWFpbCI+CiAgICAgIDxzcGFuIGNsYXNzPSJzYi1nbWFpbC1kb3QiIGlkPSJnbWFpbC1kb3QiPjwvc3Bhbj4KICAgICAgPHNwYW4gaWQ9ImdtYWlsLWxhYmVsIj5HbWFpbCBuaWNodCB2ZXJidW5kZW48L3NwYW4+CiAgICA8L2Rpdj4KICA8L2Rpdj4KPC9kaXY+Cgo8IS0tIOKVkOKVkOKVkCBNQUlOIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAtLT4KPGRpdiBjbGFzcz0ibWFpbi1hcmVhIj4KICA8IS0tIFRPUEJBUiAtLT4KICA8ZGl2IGNsYXNzPSJ0b3BiYXIiPgogICAgPGRpdiBjbGFzcz0idGItdGl0bGUiIGlkPSJ2aWV3LXRpdGxlIj5LYW5kaWRhdGVuIFBpcGVsaW5lPC9kaXY+CiAgICA8c3BhbiBjbGFzcz0idGItam9iLXBpbGwgYWN0aXZlIiBvbmNsaWNrPSJmaWx0ZXJKb2IodGhpcywnYW0nKSI+QWNjb3VudCBNYW5hZ2VyPC9zcGFuPgogICAgPHNwYW4gY2xhc3M9InRiLWpvYi1waWxsIiBvbmNsaWNrPSJmaWx0ZXJKb2IodGhpcywndmknKSI+VmVydHJpZWJzaW5uZW5kaWVuc3Q8L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjEiPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBvbmNsaWNrPSJydW5BSSgnRXJzdGVsbGUgVGFnZXMtUmVwb3J0IGbDvHIgQ1lRVUVPIFJlY3J1aXRpbmc6IFBpcGVsaW5lLVN0YXR1cywgQW50d29ydGVuLCBuw6RjaHN0ZSBTY2hyaXR0ZSBoZXV0ZS4nKSI+UmVwb3J0IOKGlzwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1wcmltYXJ5IiBvbmNsaWNrPSJzaG93Vmlldygnam9icycpIj4rIFN0ZWxsZSBob2NobGFkZW48L2J1dHRvbj4KICA8L2Rpdj4KCiAgPCEtLSBDT05URU5UIC0tPgogIDxkaXYgY2xhc3M9ImNvbnRlbnQiPgoKICAgIDwhLS0g4pWQ4pWQIFBJUEVMSU5FIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAtLT4KICAgIDxkaXYgaWQ9InZpZXctcGlwZWxpbmUiIGNsYXNzPSJ2aWV3IGFjdGl2ZSI+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtZ3JpZCI+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RhdC1jYXJkIj48ZGl2IGNsYXNzPSJzdGF0LXZhbCI+NTI8L2Rpdj48ZGl2IGNsYXNzPSJzdGF0LWxibCI+S2FuZGlkYXRlbiBnZXNhbXQ8L2Rpdj48ZGl2IGNsYXNzPSJzdGF0LWRlbHRhIGRlbHRhLXVwIj7ihpEgMTggZGllc2UgV29jaGU8L2Rpdj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0LWNhcmQiPjxkaXYgY2xhc3M9InN0YXQtdmFsIj40MSU8L2Rpdj48ZGl2IGNsYXNzPSJzdGF0LWxibCI+QW50d29ydHF1b3RlPC9kaXY+PGRpdiBjbGFzcz0ic3RhdC1kZWx0YSBkZWx0YS11cCI+4oaRIDclIEhPLUFyZ3VtZW50PC9kaXY+PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RhdC1jYXJkIj48ZGl2IGNsYXNzPSJzdGF0LXZhbCI+NTwvZGl2PjxkaXYgY2xhc3M9InN0YXQtbGJsIj5RdWVsbGVuIGFrdGl2PC9kaXY+PGRpdiBjbGFzcz0ic3RhdC1kZWx0YSIgc3R5bGU9ImNvbG9yOiM3YThmYTYiPkxJIMK3IFhpbmcgwrcgQXBvbGxvIMK3IEluZGVlZCDCtyBFdmVudHM8L2Rpdj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0LWNhcmQiPjxkaXYgY2xhc3M9InN0YXQtdmFsIj41PC9kaXY+PGRpdiBjbGFzcz0ic3RhdC1sYmwiPlRlcm1pbmUgZGllc2UgV29jaGU8L2Rpdj48ZGl2IGNsYXNzPSJzdGF0LWRlbHRhIGRlbHRhLXVwIj7ihpEgMiB2cy4gVm9yd29jaGU8L2Rpdj48L2Rpdj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJwYWRkaW5nOjA7b3ZlcmZsb3c6aGlkZGVuIj4KICAgICAgICA8dGFibGUgY2xhc3M9InB0YWJsZSI+CiAgICAgICAgICA8dGhlYWQ+PHRyPgogICAgICAgICAgICA8dGggc3R5bGU9InBhZGRpbmc6MTBweCAxMnB4Ij5LYW5kaWRhdDwvdGg+PHRoPlN0ZWxsZTwvdGg+PHRoPk1hdGNoPC90aD4KICAgICAgICAgICAgPHRoPlJlZ2lvbjwvdGg+PHRoPlF1ZWxsZTwvdGg+PHRoPlN0YXR1czwvdGg+PHRoPkxldHp0ZSBBa3Rpb248L3RoPjx0aD48L3RoPgogICAgICAgICAgPC90cj48L3RoZWFkPgogICAgICAgICAgPHRib2R5PgogICAgICAgICAgICA8dHI+PHRkIHN0eWxlPSJwYWRkaW5nOjEwcHggMTJweCI+PGRpdiBjbGFzcz0ibmMiPjxkaXYgY2xhc3M9ImF2IGF2LXQiPkxIPC9kaXY+TGF1cmEgSG9mZm1hbm48L2Rpdj48L3RkPjx0ZD48c3BhbiBjbGFzcz0idGFnIHRhZy1ibHVlIj5BTTwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0ibWF0Y2gtYSI+OTElPC9zcGFuPjwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJyZWctcGlsbCI+U8O8ZDwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0ic3JjLXBpbGwgc3JjLWxpIj5MaW5rZWRJbjwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0ic3Agc3AtaW50Ij5JbnRlcnZpZXcgTWk8L3NwYW4+PC90ZD48dGQgc3R5bGU9ImZvbnQtc2l6ZToxMHB4O2NvbG9yOiMyN2FlNjAiPmhldXRlPC90ZD48dGQ+PGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSIgb25jbGljaz0icnVuQUkoJ0xhdXJhIEhvZmZtYW5uLCBJbnRlcnZpZXcgTWl0dHdvY2ggZsO8ciBDWVFVRU8gQWNjb3VudCBNYW5hZ2VyLiBFcnN0ZWxsZSBBZ2VuZGEgbWl0IDUgSHVudGVyLUNoZWNrIEZyYWdlbi4nKSI+QWdlbmRhIOKGlzwvYnV0dG9uPjwvdGQ+PC90cj4KICAgICAgICAgICAgPHRyPjx0ZCBzdHlsZT0icGFkZGluZzoxMHB4IDEycHgiPjxkaXYgY2xhc3M9Im5jIj48ZGl2IGNsYXNzPSJhdiBhdi1iIj5NUjwvZGl2Pk1hcmt1cyBSaWNodGVyPC9kaXY+PC90ZD48dGQ+PHNwYW4gY2xhc3M9InRhZyB0YWctYmx1ZSI+QU08L3NwYW4+PC90ZD48dGQ+PHNwYW4gY2xhc3M9Im1hdGNoLWEiPjg4JTwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0icmVnLXBpbGwiPk5vcmQ8L3NwYW4+PC90ZD48dGQ+PHNwYW4gY2xhc3M9InNyYy1waWxsIHNyYy1hcCI+QXBvbGxvPC9zcGFuPjwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJzcCBzcC1yZXAiPkdlYW50d29ydGV0PC9zcGFuPjwvdGQ+PHRkIHN0eWxlPSJmb250LXNpemU6MTBweDtjb2xvcjojN2E4ZmE2Ij5nZXN0ZXJuPC90ZD48dGQ+PGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSIgb25jbGljaz0icnVuQUkoJ01hcmt1cyBSaWNodGVyLCBDb21wdXRhY2VudGVyIEhhbWJ1cmcsIGhhdCBhdWYgQ1lRVUVPIEFjY291bnQgTWFuYWdlciBNYWlsIGdlYW50d29ydGV0LiBBbnR3b3J0bWFpbCBtaXQgVGVybWludm9yc2NobMOkZ2VuLicpIj5BbnR3b3J0ZW4g4oaXPC9idXR0b24+PC90ZD48L3RyPgogICAgICAgICAgICA8dHI+PHRkIHN0eWxlPSJwYWRkaW5nOjEwcHggMTJweCI+PGRpdiBjbGFzcz0ibmMiPjxkaXYgY2xhc3M9ImF2IGF2LXAiPlNLPC9kaXY+U2FuZHJhIEtyw7xnZXI8L2Rpdj48L3RkPjx0ZD48c3BhbiBjbGFzcz0idGFnIHRhZy1ibHVlIj5BTTwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0ibWF0Y2gtYSI+ODUlPC9zcGFuPjwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJyZWctcGlsbCI+V2VzdDwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0ic3JjLXBpbGwgc3JjLWluIj5JbmRlZWQ8L3NwYW4+PC90ZD48dGQ+PHNwYW4gY2xhc3M9InNwIHNwLW9wZW4iPkdlw7ZmZm5ldDwvc3Bhbj48L3RkPjx0ZCBzdHlsZT0iZm9udC1zaXplOjEwcHg7Y29sb3I6I2QzNTQwMCI+dm9yIDMgVGFnZW48L3RkPjx0ZD48YnV0dG9uIGNsYXNzPSJidG4gYnRuLXNtIiBvbmNsaWNrPSJydW5BSSgnU2FuZHJhIEtyw7xnZXIsIEJlY2h0bGUgTlJXLCBoYXQgRS1NYWlsIGdlw7ZmZm5ldCBhYmVyIG5pY2h0IGdlYW50d29ydGV0LiBGb2xsb3ctdXAgbmFjaCAzIFRhZ2VuIGbDvHIgQ1lRVUVPLicpIj5Gb2xsb3ctdXAg4oaXPC9idXR0b24+PC90ZD48L3RyPgogICAgICAgICAgICA8dHI+PHRkIHN0eWxlPSJwYWRkaW5nOjEwcHggMTJweCI+PGRpdiBjbGFzcz0ibmMiPjxkaXYgY2xhc3M9ImF2IGF2LWEiPkZIPC9kaXY+RmVsaXggSGFydG1hbm48L2Rpdj48L3RkPjx0ZD48c3BhbiBjbGFzcz0idGFnIHRhZy1ncmF5Ij5WSTwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0ibWF0Y2gtYiI+NzklPC9zcGFuPjwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJyZWctcGlsbCI+U8O8ZDwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0ic3JjLXBpbGwgc3JjLXhpIj5YaW5nPC9zcGFuPjwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJzcCBzcC1zZW50Ij5HZXNlbmRldDwvc3Bhbj48L3RkPjx0ZCBzdHlsZT0iZm9udC1zaXplOjEwcHg7Y29sb3I6IzdhOGZhNiI+aGV1dGU8L3RkPjx0ZD48YnV0dG9uIGNsYXNzPSJidG4gYnRuLXNtIiBvbmNsaWNrPSJydW5BSSgnRmVsaXggSGFydG1hbm4sIFREIFN5bm5leCBNw7xuY2hlbiwgVmVydHJpZWJzaW5uZW5kaWVuc3QgQ1lRVUVPLiBGb2xsb3ctdXAgaW4gNSBUYWdlbiBwbGFuZW4uJykiPlNlcXVlbnog4oaXPC9idXR0b24+PC90ZD48L3RyPgogICAgICAgICAgICA8dHI+PHRkIHN0eWxlPSJwYWRkaW5nOjEwcHggMTJweCI+PGRpdiBjbGFzcz0ibmMiPjxkaXYgY2xhc3M9ImF2IGF2LWIiPkFXPC9kaXY+QW5uYSBXb2xmPC9kaXY+PC90ZD48dGQ+PHNwYW4gY2xhc3M9InRhZyB0YWctZ3JheSI+Vkk8L3NwYW4+PC90ZD48dGQ+PHNwYW4gY2xhc3M9Im1hdGNoLWIiPjc2JTwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0icmVnLXBpbGwiPk5vcmQ8L3NwYW4+PC90ZD48dGQ+PHNwYW4gY2xhc3M9InNyYy1waWxsIHNyYy1pbiI+SW5kZWVkPC9zcGFuPjwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJzcCBzcC1yZXAiPkdlYW50d29ydGV0PC9zcGFuPjwvdGQ+PHRkIHN0eWxlPSJmb250LXNpemU6MTBweDtjb2xvcjojMjdhZTYwIj5oZXV0ZTwvdGQ+PHRkPjxidXR0b24gY2xhc3M9ImJ0biBidG4tc20iIG9uY2xpY2s9InJ1bkFJKCdBbm5hIFdvbGYsIEluZ3JhbSBNaWNybyBIYW1idXJnLCBoYXQgYXVmIFZlcnRyaWVic2lubmVuZGllbnN0IENZUVVFTyBNYWlsIHBvc2l0aXYgZ2VhbnR3b3J0ZXQuIFRlcm1pbnZvcnNjaGzDpGdlIHNlbmRlbi4nKSI+VGVybWluIOKGlzwvYnV0dG9uPjwvdGQ+PC90cj4KICAgICAgICAgIDwvdGJvZHk+CiAgICAgICAgPC90YWJsZT4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj4KCiAgICA8IS0tIOKVkOKVkCBKT0JTIC8gU1RFTExFTi1VUExPQUQg4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQIC0tPgogICAgPGRpdiBpZD0idmlldy1qb2JzIiBjbGFzcz0idmlldyI+CiAgICAgIDwhLS0gVVBMT0FEIFpPTkUgLS0+CiAgICAgIDxkaXYgY2xhc3M9ImNhcmQiPgogICAgICAgIDxkaXYgY2xhc3M9ImNhcmQtdGl0bGUiPvCfk4QgTmV1ZSBTdGVsbGUgaG9jaGxhZGVuPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0idXBsb2FkLXpvbmUiIGlkPSJ1cGxvYWQtem9uZSIgb25jbGljaz0iZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2ZpbGUtaW5wdXQnKS5jbGljaygpIiBvbmRyYWdvdmVyPSJldmVudC5wcmV2ZW50RGVmYXVsdCgpO3RoaXMuY2xhc3NMaXN0LmFkZCgnZHJhZy1vdmVyJykiIG9uZHJhZ2xlYXZlPSJ0aGlzLmNsYXNzTGlzdC5yZW1vdmUoJ2RyYWctb3ZlcicpIiBvbmRyb3A9ImhhbmRsZURyb3AoZXZlbnQpIj4KICAgICAgICAgIDxpbnB1dCB0eXBlPSJmaWxlIiBpZD0iZmlsZS1pbnB1dCIgYWNjZXB0PSIucGRmLC5kb2MsLmRvY3gsLnR4dCIgb25jaGFuZ2U9ImhhbmRsZUZpbGVTZWxlY3QoZXZlbnQpIj4KICAgICAgICAgIDxkaXYgY2xhc3M9InVwbG9hZC1pY29uIj7wn5OEPC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJ1cGxvYWQtdGl0bGUiPlN0ZWxsZW5iZXNjaHJlaWJ1bmcgaGllciBhYmxlZ2VuPC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJ1cGxvYWQtc3ViIj5QREYsIFdvcmQgKC5kb2N4KSBvZGVyIFRleHQgwrcgS0kgZXh0cmFoaWVydCBhdXRvbWF0aXNjaCBhbGxlIEFuZm9yZGVydW5nZW48L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgICA8ZGl2IGlkPSJ1cGxvYWQtc3RhdHVzIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4O2Rpc3BsYXk6bm9uZSI+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJpbmZvLWJveCIgaWQ9InVwbG9hZC1tc2ciPkRhdGVpIHdpcmQgYW5hbHlzaWVydC4uLjwvZGl2PgogICAgICAgICAgPGRpdiBpZD0iZXh0cmFjdGVkLXJlcXMiIHN0eWxlPSJkaXNwbGF5Om5vbmU7bWFyZ2luLXRvcDoxMHB4Ij4KICAgICAgICAgICAgPGRpdiBjbGFzcz0ibGFiZWwiPktJLWV4dHJhaGllcnRlIEFuZm9yZGVydW5nZW4g4oCUIGJpdHRlIHByw7xmZW4gdW5kIGFucGFzc2VuOjwvZGl2PgogICAgICAgICAgICA8ZGl2IGlkPSJyZXFzLWNvbnRlbnQiPjwvZGl2PgogICAgICAgICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjhweDttYXJnaW4tdG9wOjEwcHgiPgogICAgICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tcHJpbWFyeSIgb25jbGljaz0iY29uZmlybUpvYigpIj7inJMgQmVzdMOkdGlnZW4gJiBTb3VyY2luZyBzdGFydGVuPC9idXR0b24+CiAgICAgICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBvbmNsaWNrPSJydW5BSSgnT3B0aW1pZXJlIGRpZXNlIFN0ZWxsZW5iZXNjaHJlaWJ1bmcgZsO8ciBDWVFVRU8gQWNjb3VudCBNYW5hZ2VyOiBIdW50ZXItS3VsdHVyIGJldG9uZW4sIEhvbWVvZmZpY2UsIFNlY3VyaXR5LVNwZXppYWxpc2llcnVuZyBhbHMgS2FycmllcmVzY2hyaXR0LiBXYXMgd8O8cmRlc3QgZHUgw6RuZGVybj8nKSI+S0kgb3B0aW1pZXJlbiDihpc8L2J1dHRvbj4KICAgICAgICAgICAgPC9kaXY+CiAgICAgICAgICA8L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgICA8IS0tIE1BTlVBTCBJTlBVVCAtLT4KICAgICAgICA8ZGl2IHN0eWxlPSJtYXJnaW4tdG9wOjE0cHg7cGFkZGluZy10b3A6MTRweDtib3JkZXItdG9wOjFweCBzb2xpZCAjZThlZWY1Ij4KICAgICAgICAgIDxkaXYgY2xhc3M9ImxhYmVsIj5PZGVyOiBTdGVsbGVuYmVzY2hyZWlidW5nIGRpcmVrdCBlaW5mw7xnZW48L2Rpdj4KICAgICAgICAgIDx0ZXh0YXJlYSBjbGFzcz0idGV4dGFyZWEtZmllbGQiIGlkPSJtYW51YWwtam9iIiBwbGFjZWhvbGRlcj0iU3RlbGxlbmJlc2NocmVpYnVuZyBoaWVyIGVpbmbDvGdlbi4uLiIgc3R5bGU9Im1pbi1oZWlnaHQ6MTIwcHgiPjwvdGV4dGFyZWE+CiAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLXByaW1hcnkiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgb25jbGljaz0iYW5hbHl6ZU1hbnVhbEpvYigpIj5LSSBhbmFseXNpZXJlbiDihpc8L2J1dHRvbj4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CgogICAgICA8IS0tIEFDVElWRSBKT0JTIC0tPgogICAgICA8ZGl2IGNsYXNzPSJzZWMtdGl0bGUiPkFrdGl2ZSBTdGVsbGVuPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImpvYi1jYXJkIHNlbGVjdGVkIiBpZD0iam9iLWFtIj4KICAgICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpzcGFjZS1iZXR3ZWVuO21hcmdpbi1ib3R0b206MTBweCI+CiAgICAgICAgICA8ZGl2PgogICAgICAgICAgICA8ZGl2IHN0eWxlPSJmb250LXNpemU6MTNweDtmb250LXdlaWdodDo1MDAiPkFjY291bnQgTWFuYWdlciBJVCBTZWN1cml0eSAobS93L2QpPC9kaXY+CiAgICAgICAgICAgIDxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMXB4O2NvbG9yOiM3YThmYTYiPk3DvG5jaGVuIC8gRGV1dHNjaGxhbmR3ZWl0IFJlbW90ZSDCtyBWb2xsemVpdCDCtyBzZWl0IDEyLjA0LjIwMjU8L2Rpdj4KICAgICAgICAgIDwvZGl2PgogICAgICAgICAgPHNwYW4gY2xhc3M9ImpvYi1zdGF0dXMtYWN0aXZlIj5Ba3RpdiDCtyAzMSBLYW5kaWRhdGVuPC9zcGFuPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtmbGV4LXdyYXA6d3JhcDtnYXA6NHB4O21hcmdpbi1ib3R0b206MTBweCI+CiAgICAgICAgICA8c3BhbiBjbGFzcz0icmVxLWNoaXAgbXVzdCI+4pyTIFJlc2VsbGVyL1N5c3RlbWhhdXMgRXJmYWhydW5nPC9zcGFuPgogICAgICAgICAgPHNwYW4gY2xhc3M9InJlcS1jaGlwIG11c3QiPuKckyBJVCBTZWN1cml0eSAvIElULUzDtnN1bmdzdmVydHJpZWI8L3NwYW4+CiAgICAgICAgICA8c3BhbiBjbGFzcz0icmVxLWNoaXAgbXVzdCI+4pyTIEh1bnRlci1NZW50YWxpdMOkdDwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJyZXEtY2hpcCBuaWNlIj5+IERpc3RyaWJ1dGlvbiBCYWNrZ3JvdW5kPC9zcGFuPgogICAgICAgICAgPHNwYW4gY2xhc3M9InJlcS1jaGlwIG5pY2UiPn4gTVNTUC9TZWN1cml0eSBLZW5udG5pc3NlPC9zcGFuPgogICAgICAgICAgPHNwYW4gY2xhc3M9InJlcS1jaGlwIGF2b2lkIj7inJcgS2VpbmUgSGVyc3RlbGxlcjwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJyZXEtY2hpcCBhdm9pZCI+4pyXIEtlaW4gU2VuaW9yIEVudGVycHJpc2UgQU08L3NwYW4+CiAgICAgICAgPC9kaXY+CiAgICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo2cHgiPgogICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSIgb25jbGljaz0icnVuQUkoJ0Vyc3RlbGxlIG9wdGltaWVydGVuIEJvb2xlYW4gU2VhcmNoIFN0cmluZyBmw7xyIExpbmtlZEluIFNhbGVzIE5hdmlnYXRvciBmw7xyIENZUVVFTyBBY2NvdW50IE1hbmFnZXIuIEZva3VzOiBJVC1SZXNlbGxlciAoQmVjaHRsZSwgQ29tcHV0YWNlbnRlciwgQ2FuY29tKSB1bmQgRGlzdHJpYnV0aW9uIChJbmdyYW0sIEFycm93KS4gS2VpbmUgSGVyc3RlbGxlci4nKSI+Qm9vbGVhbiBTdHJpbmcg4oaXPC9idXR0b24+CiAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLXNtIiBvbmNsaWNrPSJydW5BSSgnRXJzdGVsbGUgdm9sbHN0w6RuZGlnZSBFcnN0LUUtTWFpbCBmw7xyIEFjY291bnQgTWFuYWdlciBiZWkgQ29tcHV0YWNlbnRlciBmw7xyIENZUVVFTy4gQmV0cmVmZiArIE1haWwgYXVmIERldXRzY2guJykiPkUtTWFpbCBWb3JsYWdlIOKGlzwvYnV0dG9uPgogICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSBidG4tcHJpbWFyeSIgb25jbGljaz0ic2hvd1ZpZXcoJ3NvdXJjaW5nJykiPlNvdXJjaW5nIHN0YXJ0ZW4g4oaSPC9idXR0b24+CiAgICAgICAgPC9kaXY+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJqb2ItY2FyZCIgaWQ9ImpvYi12aSI+CiAgICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjttYXJnaW4tYm90dG9tOjEwcHgiPgogICAgICAgICAgPGRpdj4KICAgICAgICAgICAgPGRpdiBzdHlsZT0iZm9udC1zaXplOjEzcHg7Zm9udC13ZWlnaHQ6NTAwIj5WZXJ0cmllYnNpbm5lbmRpZW5zdCBJVCBTZWN1cml0eSAobS93L2QpPC9kaXY+CiAgICAgICAgICAgIDxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMXB4O2NvbG9yOiM3YThmYTYiPk3DvG5jaGVuIC8gSG9tZW9mZmljZSDCtyBWb2xsemVpdCDCtyBzZWl0IDIwLjA0LjIwMjU8L2Rpdj4KICAgICAgICAgIDwvZGl2PgogICAgICAgICAgPHNwYW4gY2xhc3M9ImpvYi1zdGF0dXMtYWN0aXZlIj5Ba3RpdiDCtyAyMSBLYW5kaWRhdGVuPC9zcGFuPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtmbGV4LXdyYXA6d3JhcDtnYXA6NHB4O21hcmdpbi1ib3R0b206MTBweCI+CiAgICAgICAgICA8c3BhbiBjbGFzcz0icmVxLWNoaXAgbXVzdCI+4pyTIEluc2lkZSBTYWxlcyAvIERpc3RyaWJ1dGlvbjwvc3Bhbj4KICAgICAgICAgIDxzcGFuIGNsYXNzPSJyZXEtY2hpcCBtdXN0Ij7inJMgSVQtUHJvZHVrdGtlbm50bmlzc2U8L3NwYW4+CiAgICAgICAgICA8c3BhbiBjbGFzcz0icmVxLWNoaXAgbmljZSI+fiBJVCBTZWN1cml0eSBFcmZhaHJ1bmc8L3NwYW4+CiAgICAgICAgICA8c3BhbiBjbGFzcz0icmVxLWNoaXAgYXZvaWQiPuKclyBLZWluIHJlaW5lciBCYWNrb2ZmaWNlPC9zcGFuPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6NnB4Ij4KICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tc20iIG9uY2xpY2s9InJ1bkFJKCdCb29sZWFuIFNlYXJjaCBTdHJpbmcgZsO8ciBWZXJ0cmllYnNpbm5lbmRpZW5zdCBJVCBTZWN1cml0eSBDWVFVRU8uIEZva3VzOiBEaXN0cmlidXRpb24gKEluZ3JhbSwgQXJyb3csIEFsc28sIFREIFN5bm5leCksIEluc2lkZSBTYWxlcyBJVCwgZGV1dHNjaGxhbmR3ZWl0LicpIj5Cb29sZWFuIFN0cmluZyDihpc8L2J1dHRvbj4KICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tc20gYnRuLXByaW1hcnkiIG9uY2xpY2s9InNob3dWaWV3KCdzb3VyY2luZycpIj5Tb3VyY2luZyBzdGFydGVuIOKGkjwvYnV0dG9uPgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgoKICAgIDwhLS0g4pWQ4pWQIFNPVVJDSU5HIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAtLT4KICAgIDxkaXYgaWQ9InZpZXctc291cmNpbmciIGNsYXNzPSJ2aWV3Ij4KICAgICAgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICAgICAgPGRpdiBjbGFzcz0iY2FyZC10aXRsZSI+8J+OryBTb3VyY2luZy1TdHJhdGVnaWUgwrcgQ1lRVUVPIFZlcnRyaWVicy1ETkE8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJncmlkLTIiIHN0eWxlPSJtYXJnaW4tYm90dG9tOjEycHgiPgogICAgICAgICAgPGRpdj4KICAgICAgICAgICAgPGRpdiBjbGFzcz0ibGFiZWwiPlN0ZWxsZTwvZGl2PgogICAgICAgICAgICA8c2VsZWN0IGNsYXNzPSJpbnB1dC1maWVsZCIgaWQ9InNyYy1qb2IiPgogICAgICAgICAgICAgIDxvcHRpb24+QWNjb3VudCBNYW5hZ2VyIElUIFNlY3VyaXR5PC9vcHRpb24+CiAgICAgICAgICAgICAgPG9wdGlvbj5WZXJ0cmllYnNpbm5lbmRpZW5zdCBJVCBTZWN1cml0eTwvb3B0aW9uPgogICAgICAgICAgICA8L3NlbGVjdD4KICAgICAgICAgIDwvZGl2PgogICAgICAgICAgPGRpdj4KICAgICAgICAgICAgPGRpdiBjbGFzcz0ibGFiZWwiPlBsYXR0Zm9ybTwvZGl2PgogICAgICAgICAgICA8c2VsZWN0IGNsYXNzPSJpbnB1dC1maWVsZCIgaWQ9InNyYy1wbGF0Zm9ybSI+CiAgICAgICAgICAgICAgPG9wdGlvbj5MaW5rZWRJbiBTYWxlcyBOYXZpZ2F0b3I8L29wdGlvbj4KICAgICAgICAgICAgICA8b3B0aW9uPkFwb2xsby5pbzwvb3B0aW9uPgogICAgICAgICAgICAgIDxvcHRpb24+SW5kZWVkIC8gU3RlcFN0b25lIENWLURCPC9vcHRpb24+CiAgICAgICAgICAgICAgPG9wdGlvbj5YaW5nPC9vcHRpb24+CiAgICAgICAgICAgICAgPG9wdGlvbj5BbGxlIGdsZWljaHplaXRpZzwvb3B0aW9uPgogICAgICAgICAgICA8L3NlbGVjdD4KICAgICAgICAgIDwvZGl2PgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6OHB4O21hcmdpbi1ib3R0b206MTJweCI+CiAgICAgICAgICA8aW5wdXQgY2xhc3M9ImlucHV0LWZpZWxkIiBpZD0ic3JjLWtleXdvcmRzIiBwbGFjZWhvbGRlcj0iWnVzYXR6LUtleXdvcmRzIHouQi4gQmVjaHRsZSwgTlJXLCBJbmdyYW0gTWljcm8uLi4iIHN0eWxlPSJmbGV4OjEiPgogICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1wcmltYXJ5IiBvbmNsaWNrPSJkb1NvdXJjaW5nKCkiPkJvb2xlYW4gU3RyaW5nIGdlbmVyaWVyZW4g4oaXPC9idXR0b24+CiAgICAgICAgPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0id2Fybi1ib3giPvCfmqkgQXV0b21hdGlzY2ggYXVzZ2VzY2hsb3NzZW46IENyb3dkU3RyaWtlIMK3IFpzY2FsZXIgwrcgUGFsbyBBbHRvIMK3IFNlbnRpbmVsT25lIMK3IEZvcnRpbmV0IGFscyBBcmJlaXRnZWJlciDCtyAiRW50ZXJwcmlzZS9TdHJhdGVnaWMvR2xvYmFsIEFjY291bnQgTWFuYWdlciIgYWxzIEpvYnRpdGVsPC9kaXY+CiAgICAgIDwvZGl2PgoKICAgICAgPGRpdiBjbGFzcz0iZ3JpZC0yIj4KICAgICAgICA8ZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0ic2VjLXRpdGxlIj5CZXN0ZSBRdWVsbGVuIGbDvHIgQ1lRVUVPPC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJzb3VyY2UtY2FyZCB0aWVyMSI+CiAgICAgICAgICAgIDxzcGFuIGNsYXNzPSJzcmMtcmFuayByLWJlc3QiPlBSSU9SSVTDhFQgMTwvc3Bhbj4KICAgICAgICAgICAgPGRpdiBzdHlsZT0iZm9udC1zaXplOjEycHg7Zm9udC13ZWlnaHQ6NTAwO21hcmdpbi1ib3R0b206NHB4Ij5JVC1SZXNlbGxlciAmIFN5c3RlbWjDpHVzZXI8L2Rpdj4KICAgICAgICAgICAgPGRpdiBzdHlsZT0iZm9udC1zaXplOjExcHg7Y29sb3I6IzVhNmE3ZTttYXJnaW4tYm90dG9tOjZweCI+VGFrdGZyZXF1ZW56IGdld29obnQsIHJlYWxpc3Rpc2NoZSBHZWhhbHRzZXJ3YXJ0dW5nZW4sIElUIFNlY3VyaXR5IEVyZmFocnVuZy48L2Rpdj4KICAgICAgICAgICAgPGRpdj48c3BhbiBjbGFzcz0iY2hpcCI+QmVjaHRsZTwvc3Bhbj48c3BhbiBjbGFzcz0iY2hpcCI+Q29tcHV0YWNlbnRlcjwvc3Bhbj48c3BhbiBjbGFzcz0iY2hpcCI+Q2FuY29tPC9zcGFuPjxzcGFuIGNsYXNzPSJjaGlwIj5BeGlhbnM8L3NwYW4+PHNwYW4gY2xhc3M9ImNoaXAiPkNvbnRyb2x3YXJlPC9zcGFuPjxzcGFuIGNsYXNzPSJjaGlwIj5Db25zY2lhPC9zcGFuPjxzcGFuIGNsYXNzPSJjaGlwIj5TSEQ8L3NwYW4+PHNwYW4gY2xhc3M9ImNoaXAiPkRhbW92bzwvc3Bhbj48L2Rpdj4KICAgICAgICAgIDwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0ic291cmNlLWNhcmQgdGllcjEiPgogICAgICAgICAgICA8c3BhbiBjbGFzcz0ic3JjLXJhbmsgci1iZXN0Ij5QUklPUklUw4RUIDE8L3NwYW4+CiAgICAgICAgICAgIDxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMnB4O2ZvbnQtd2VpZ2h0OjUwMDttYXJnaW4tYm90dG9tOjRweCI+SVQgRGlzdHJpYnV0aW9uPC9kaXY+CiAgICAgICAgICAgIDxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMXB4O2NvbG9yOiM1YTZhN2U7bWFyZ2luLWJvdHRvbTo2cHgiPktlbm5lbiBTZWN1cml0eS1Qcm9kdWt0ZSwgaG9oZSBBa3Rpdml0w6R0IGlzdCBOb3JtYWx6dXN0YW5kLiBJZGVhbCBmw7xyIFZJLjwvZGl2PgogICAgICAgICAgICA8ZGl2PjxzcGFuIGNsYXNzPSJjaGlwIj5JbmdyYW0gTWljcm88L3NwYW4+PHNwYW4gY2xhc3M9ImNoaXAiPkFycm93IEVsZWN0cm9uaWNzPC9zcGFuPjxzcGFuIGNsYXNzPSJjaGlwIj5BbHNvPC9zcGFuPjxzcGFuIGNsYXNzPSJjaGlwIj5URCBTeW5uZXg8L3NwYW4+PHNwYW4gY2xhc3M9ImNoaXAiPkV4Y2x1c2l2ZSBOZXR3b3Jrczwvc3Bhbj48L2Rpdj4KICAgICAgICAgIDwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0ic291cmNlLWNhcmQiIHN0eWxlPSJiYWNrZ3JvdW5kOiNmZmY1ZjU7Ym9yZGVyLWNvbG9yOiNmNWM2YzYiPgogICAgICAgICAgICA8c3BhbiBjbGFzcz0ic3JjLXJhbmsgci1hdm9pZCI+VkVSTUVJREVOPC9zcGFuPgogICAgICAgICAgICA8ZGl2IHN0eWxlPSJmb250LXNpemU6MTJweDtmb250LXdlaWdodDo1MDA7bWFyZ2luLWJvdHRvbTo0cHgiPkRpcmVrdCB2b24gSGVyc3RlbGxlcm48L2Rpdj4KICAgICAgICAgICAgPGRpdiBzdHlsZT0iZm9udC1zaXplOjExcHg7Y29sb3I6I2MwMzkyYiI+R2VoYWx0c2Vyd2FydHVuZyB6dSBob2NoLCBBcmJlaXRzd2Vpc2UgcGFzc3QgbmljaHQuPC9kaXY+CiAgICAgICAgICA8L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgICA8ZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0ic2VjLXRpdGxlIj5LYW5kaWRhdGVuIChnZWZ1bmRlbiAmIGJld2VydGV0KTwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9InBhZGRpbmc6MDtvdmVyZmxvdzpoaWRkZW4iPgogICAgICAgICAgICA8dGFibGUgY2xhc3M9InB0YWJsZSI+CiAgICAgICAgICAgICAgPHRoZWFkPjx0cj48dGggc3R5bGU9InBhZGRpbmc6MTBweCAxMnB4Ij5LYW5kaWRhdDwvdGg+PHRoPk1hdGNoPC90aD48dGg+UXVlbGxlPC90aD48dGg+PC90aD48L3RyPjwvdGhlYWQ+CiAgICAgICAgICAgICAgPHRib2R5PgogICAgICAgICAgICAgICAgPHRyPjx0ZCBzdHlsZT0icGFkZGluZzo5cHggMTJweCI+PGRpdiBjbGFzcz0ibmMiPjxkaXYgY2xhc3M9ImF2IGF2LXQiPlNNPC9kaXY+U2FiaW5lIE1laWVyPC9kaXY+PGRpdiBzdHlsZT0iZm9udC1zaXplOjEwcHg7Y29sb3I6IzdhOGZhNiI+Q29tcHV0YWNlbnRlciDCtyBBTTwvZGl2PjwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJtYXRjaC1hIj45MiU8L3NwYW4+PC90ZD48dGQ+PHNwYW4gY2xhc3M9InNyYy1waWxsIHNyYy1saSI+TGlua2VkSW48L3NwYW4+PC90ZD48dGQ+PGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSIgb25jbGljaz0icnVuQUkoJ0Vyc3RlbGxlIHBlcnNvbmFsaXNpZXJ0ZSBMaW5rZWRJbiBJbk1haWwgdW5kIEUtTWFpbCBmw7xyIFNhYmluZSBNZWllciwgQWNjb3VudCBNYW5hZ2VyIGJlaSBDb21wdXRhY2VudGVyLCBmw7xyIENZUVVFTy4gQmVpZGUgdm9sbHN0w6RuZGlnLicpIj5BbnNjaHJlaWJlbiDihpc8L2J1dHRvbj48L3RkPjwvdHI+CiAgICAgICAgICAgICAgICA8dHI+PHRkIHN0eWxlPSJwYWRkaW5nOjlweCAxMnB4Ij48ZGl2IGNsYXNzPSJuYyI+PGRpdiBjbGFzcz0iYXYgYXYtYiI+VEs8L2Rpdj5UaG9tYXMgS2xlaW48L2Rpdj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTBweDtjb2xvcjojN2E4ZmE2Ij5JbmdyYW0gTWljcm8gwrcgSW5zaWRlIFNhbGVzPC9kaXY+PC90ZD48dGQ+PHNwYW4gY2xhc3M9Im1hdGNoLWEiPjg3JTwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0ic3JjLXBpbGwgc3JjLWFwIj5BcG9sbG88L3NwYW4+PC90ZD48dGQ+PGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSIgb25jbGljaz0icnVuQUkoJ0UtTWFpbCBmw7xyIFRob21hcyBLbGVpbiwgSW5zaWRlIFNhbGVzIGJlaSBJbmdyYW0gTWljcm8sIGbDvHIgQ1lRVUVPIFZlcnRyaWVic2lubmVuZGllbnN0LiBQcm9kdWt0a2VubnRuaXNzZSBkaXJla3QgZWluc2V0emVuIGFscyBBcmd1bWVudC4nKSI+QW5zY2hyZWliZW4g4oaXPC9idXR0b24+PC90ZD48L3RyPgogICAgICAgICAgICAgICAgPHRyPjx0ZCBzdHlsZT0icGFkZGluZzo5cHggMTJweCI+PGRpdiBjbGFzcz0ibmMiPjxkaXYgY2xhc3M9ImF2IGF2LXAiPkNSPC9kaXY+Q2hyaXN0aW5lIFJvdGg8L2Rpdj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTBweDtjb2xvcjojN2E4ZmE2Ij5CZWNodGxlIMK3IElUIFNlY3VyaXR5PC9kaXY+PC90ZD48dGQ+PHNwYW4gY2xhc3M9Im1hdGNoLWIiPjgyJTwvc3Bhbj48L3RkPjx0ZD48c3BhbiBjbGFzcz0ic3JjLXBpbGwgc3JjLWluIj5JbmRlZWQ8L3NwYW4+PC90ZD48dGQ+PGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSIgb25jbGljaz0icnVuQUkoJ0UtTWFpbCBmw7xyIENocmlzdGluZSBSb3RoLCBJVCBTZWN1cml0eSBTYWxlcyBiZWkgQmVjaHRsZSBBRywgZsO8ciBDWVFVRU8gQWNjb3VudCBNYW5hZ2VyLiBTcGV6aWFsaXNpZXJ1bmcgYWxzIG7DpGNoc3RlciBLYXJyaWVyZXNjaHJpdHQuJykiPkFuc2NocmVpYmVuIOKGlzwvYnV0dG9uPjwvdGQ+PC90cj4KICAgICAgICAgICAgICA8L3Rib2R5PgogICAgICAgICAgICA8L3RhYmxlPgogICAgICAgICAgPC9kaXY+CiAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLXByaW1hcnkiIHN0eWxlPSJ3aWR0aDoxMDAlO21hcmdpbi10b3A6OHB4IiBvbmNsaWNrPSJydW5BSSgnRXJzdGVsbGUgcGVyc29uYWxpc2llcnRlIEUtTWFpbHMgZsO8ciBhbGxlIDMgZ2VmdW5kZW5lbiBLYW5kaWRhdGVuIChTYWJpbmUgTWVpZXIgQ29tcHV0YWNlbnRlciwgVGhvbWFzIEtsZWluIEluZ3JhbSwgQ2hyaXN0aW5lIFJvdGggQmVjaHRsZSkgZsO8ciBDWVFVRU8uIEFsbGUgMyB2b2xsc3TDpG5kaWcgbWl0IEJldHJlZmYuJykiPkFsbGUgMyBnbGVpY2h6ZWl0aWcgYW5zY2hyZWliZW4g4oaXPC9idXR0b24+CiAgICAgICAgPC9kaXY+CiAgICAgIDwvZGl2PgogICAgPC9kaXY+CgogICAgPCEtLSDilZDilZAgRS1NQUlMIFNFUVVFTlpFTiDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAgLS0+CiAgICA8ZGl2IGlkPSJ2aWV3LWVtYWlsIiBjbGFzcz0idmlldyI+CiAgICAgIDxkaXYgY2xhc3M9ImNhcmQiPgogICAgICAgIDxkaXYgY2xhc3M9ImNhcmQtdGl0bGUiPuKfsyBBa3RpdmUgU2VxdWVuemVuPC9kaXY+CiAgICAgICAgPGRpdiBzdHlsZT0iYmFja2dyb3VuZDojZjhmYWZjO2JvcmRlci1yYWRpdXM6MTBweDtwYWRkaW5nOjE0cHg7bWFyZ2luLWJvdHRvbToxMHB4Ij4KICAgICAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47bWFyZ2luLWJvdHRvbTo4cHgiPgogICAgICAgICAgICA8ZGl2PjxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMnB4O2ZvbnQtd2VpZ2h0OjUwMCI+U2FuZHJhIEtyw7xnZXIgwrcgQmVjaHRsZSBOUlcgwrcgQWNjb3VudCBNYW5hZ2VyPC9kaXY+PGRpdiBzdHlsZT0iZm9udC1zaXplOjEwcHg7Y29sb3I6IzdhOGZhNiI+R2VzdGFydGV0IDA5LjA1LiDCtyBFLU1haWwgZ2XDtmZmbmV0LCBrZWluZSBBbnR3b3J0PC9kaXY+PC9kaXY+CiAgICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tc20gYnRuLWRhbmdlciI+U3RvcHBlbjwvYnV0dG9uPgogICAgICAgICAgPC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJzZXEtdHJhY2siPgogICAgICAgICAgICA8ZGl2IGNsYXNzPSJzZXEtc3RlcCI+PGRpdiBjbGFzcz0ic2VxLWNpcmNsZSBzLWRvbmUiPuKckzwvZGl2PjxkaXYgY2xhc3M9InNlcS1sYmwiPkVyc3QtTWFpbDwvZGl2PjxkaXYgY2xhc3M9InNlcS1kYXkiPjA5LjA1LjwvZGl2PjwvZGl2PgogICAgICAgICAgICA8ZGl2IGNsYXNzPSJzZXEtc3RlcCI+PGRpdiBjbGFzcz0ic2VxLWNpcmNsZSBzLWFjdGl2ZSIgb25jbGljaz0icnVuQUkoJ0ZvbGxvdy11cCBUZXh0IGbDvHIgU2FuZHJhIEtyw7xnZXIsIEJlY2h0bGUgTlJXLCBkaWUgRS1NYWlsIGdlw7ZmZm5ldCBhYmVyIG5pY2h0IGdlYW50d29ydGV0IGhhdC4gQ1lRVUVPIEFjY291bnQgTWFuYWdlci4gSGV1dGUgc2VuZGVuLicpIj7ilrY8L2Rpdj48ZGl2IGNsYXNzPSJzZXEtbGJsIj5Gb2xsb3ctdXAgMTwvZGl2PjxkaXYgY2xhc3M9InNlcS1kYXkiPkhldXRlPC9kaXY+PC9kaXY+CiAgICAgICAgICAgIDxkaXYgY2xhc3M9InNlcS1zdGVwIj48ZGl2IGNsYXNzPSJzZXEtY2lyY2xlIHMtcGVuZCI+MzwvZGl2PjxkaXYgY2xhc3M9InNlcS1sYmwiPkZvbGxvdy11cCAyPC9kaXY+PGRpdiBjbGFzcz0ic2VxLWRheSI+MTkuMDUuPC9kaXY+PC9kaXY+CiAgICAgICAgICAgIDxkaXYgY2xhc3M9InNlcS1zdGVwIj48ZGl2IGNsYXNzPSJzZXEtY2lyY2xlIHMtcGVuZCI+NDwvZGl2PjxkaXYgY2xhc3M9InNlcS1sYmwiPkFic2NobHVzczwvZGl2PjxkaXYgY2xhc3M9InNlcS1kYXkiPjI0LjA1LjwvZGl2PjwvZGl2PgogICAgICAgICAgPC9kaXY+CiAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLXNtIiBvbmNsaWNrPSJydW5BSSgnWmVpZ2UgYWxsZSA0IEUtTWFpbHMgZGVyIFNlcXVlbnogZsO8ciBTYW5kcmEgS3LDvGdlciBCZWNodGxlIGbDvHIgQ1lRVUVPIEFjY291bnQgTWFuYWdlci4gQWxsZSB2b2xsc3TDpG5kaWcuJykiPkFsbGUgRS1NYWlscyBhbnNlaGVuIOKGlzwvYnV0dG9uPgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICAgICAgPGRpdiBjbGFzcz0iY2FyZC10aXRsZSI+4pyJIEUtTWFpbCBWb3JsYWdlbiDCtyBDWVFVRU8gRE5BPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0iZ3JpZC0yIj4KICAgICAgICAgIDxkaXY+CiAgICAgICAgICAgIDxkaXYgY2xhc3M9ImxhYmVsIj5Gw7xyIFJlc2VsbGVyLUthbmRpZGF0ZW48L2Rpdj4KICAgICAgICAgICAgPGRpdiBjbGFzcz0iZ29vZC1ib3giPkJldHJlZmY6ICJWb20gU3lzdGVtaGF1cyB6dW0gTVNTUC1TcGV6aWFsaXN0ZW4g4oCUIENZUVVFTyI8L2Rpdj4KICAgICAgICAgICAgPHRleHRhcmVhIGNsYXNzPSJ0ZXh0YXJlYS1maWVsZCIgaWQ9InRwbC1yZXNlbGxlciIgc3R5bGU9Im1pbi1oZWlnaHQ6MTEwcHgiPkhhbGxvIFtWb3JuYW1lXSwKCklocmUgRXJmYWhydW5nIGJlaSBbU3lzdGVtaGF1c10gaW0gSVQgU2VjdXJpdHkgVmVydHJpZWIgaGF0IG1pY2ggYXVmIFtQbGF0dGZvcm1dIGF1Zm1lcmtzYW0gZ2VtYWNodC4KCldpciBzaW5kIENZUVVFTyDigJQgZWluIHdhY2hzZW5kZXIgTVNTUCBhdXMgTcO8bmNoZW4gbWl0IDIwKyBKYWhyZW4gRXJmYWhydW5nIOKAlCB1bmQgc3VjaGVuIGVpbmVuIEFjY291bnQgTWFuYWdlciBkZXIgc2VpbmUgUmVnaW9uIGVpZ2VudmVyYW50d29ydGxpY2ggdm9tIEhvbWVvZmZpY2UgYmV0cmV1dC4KCkjDpHR0ZW4gU2llIDE1IE1pbnV0ZW4gZsO8ciBlaW4ga3VyemVzIEdlc3Byw6RjaD8KCkJlc3RlIEdyw7zDn2U8L3RleHRhcmVhPgogICAgICAgICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjZweDttYXJnaW4tdG9wOjZweCI+CiAgICAgICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSBidG4tcHJpbWFyeSIgb25jbGljaz0icnVuQUkoJ1ZlcmJlc3NlcmUgZGllc2UgRXJzdC1FLU1haWwgZsO8ciBDWVFVRU8gUmVzZWxsZXItS2FuZGlkYXRlbi4gSHVudGVyLUt1bHR1ciB1bmQgS2FycmllcmVzY2hyaXR0IHp1ciBTZWN1cml0eS1TcGV6aWFsaXNpZXJ1bmcgYmV0b25lbi4gTWF4IDEyMCBXw7ZydGVyLCBCZXRyZWZmICsgTWFpbC4nKSI+VmVyYmVzc2VybiDihpc8L2J1dHRvbj4KICAgICAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLXNtIiBvbmNsaWNrPSJydW5BSSgnRXJzdGVsbGUgMyBCZXRyZWZmLVZhcmlhbnRlbiBmw7xyIENZUVVFTyBSZXNlbGxlci1LYW5kaWRhdGVuIEEvQi9DIFRlc3QuJykiPkJldHJlZmYtVmFyaWFudGVuIOKGlzwvYnV0dG9uPgogICAgICAgICAgICA8L2Rpdj4KICAgICAgICAgIDwvZGl2PgogICAgICAgICAgPGRpdj4KICAgICAgICAgICAgPGRpdiBjbGFzcz0ibGFiZWwiPkbDvHIgRGlzdHJpYnV0aW9uLUthbmRpZGF0ZW48L2Rpdj4KICAgICAgICAgICAgPGRpdiBjbGFzcz0iZ29vZC1ib3giPkJldHJlZmY6ICJJaHJlIFByb2R1a3QtRXhwZXJ0aXNlIGRpcmVrdCBiZWltIE1TU1AgZWluc2V0emVuIjwvZGl2PgogICAgICAgICAgICA8dGV4dGFyZWEgY2xhc3M9InRleHRhcmVhLWZpZWxkIiBpZD0idHBsLWRpc3QiIHN0eWxlPSJtaW4taGVpZ2h0OjExMHB4Ij5IYWxsbyBbVm9ybmFtZV0sCgpBbHMgW1JvbGxlXSBiZWkgW0Rpc3RyaWJ1dG9yXSBrZW5uZW4gU2llIGRpZSBJVCBTZWN1cml0eSBQcm9kdWt0ZSBiZXJlaXRzIGJlc3RlbnMuCgpCZWkgQ1lRVUVPIHNldHplbiBTaWUgZGllc2VzIFdpc3NlbiBkaXJla3QgYmVpbSBFbmRrdW5kZW4gZWluIOKAlCBtaXQgZWlnZW5lciBSZWdpb24sIEhvbWVvZmZpY2UgdW5kIGRlciBNw7ZnbGljaGtlaXQsIHNpY2ggYWxzIFNlY3VyaXR5LVNwZXppYWxpc3QgenUgcG9zaXRpb25pZXJlbi4KCkjDpHR0ZW4gU2llIGt1cnogWmVpdCBmw7xyIGVpbiBHZXNwcsOkY2g/CgpCZXN0ZSBHcsO8w59lPC90ZXh0YXJlYT4KICAgICAgICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo2cHg7bWFyZ2luLXRvcDo2cHgiPgogICAgICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tc20gYnRuLXByaW1hcnkiIG9uY2xpY2s9InJ1bkFJKCdWZXJiZXNzZXJlIGRpZXNlIEVyc3QtRS1NYWlsIGbDvHIgQ1lRVUVPIERpc3RyaWJ1dGlvbi1LYW5kaWRhdGVuIChJbmdyYW0sIEFycm93LCBBbHNvKS4gTWVociBFaWdlbnZlcmFudHdvcnR1bmcgdW5kIEthcnJpZXJlZW50d2lja2x1bmcgYWxzIEFyZ3VtZW50ZS4gQmV0cmVmZiArIE1haWwuJykiPlZlcmJlc3Nlcm4g4oaXPC9idXR0b24+CiAgICAgICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSIgb25jbGljaz0icnVuQUkoJ0Vyc3RlbGxlIHZvbGxzdMOkbmRpZ2UgRm9sbG93LXVwIFNlcXVlbnogZsO8ciBEaXN0cmlidXRpb24tS2FuZGlkYXRlbiBiZWkgQ1lRVUVPOiBUYWcgNSwgVGFnIDEwLCBUYWcgMTUuIEFsbGUgMyBNYWlscyB2b2xsc3TDpG5kaWcuJykiPkZvbGxvdy11cCBTZXF1ZW56IOKGlzwvYnV0dG9uPgogICAgICAgICAgICA8L2Rpdj4KICAgICAgICAgIDwvZGl2PgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgoKICAgIDwhLS0g4pWQ4pWQIElOQk9YIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAtLT4KICAgIDxkaXYgaWQ9InZpZXctaW5ib3giIGNsYXNzPSJ2aWV3Ij4KICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2p1c3RpZnktY29udGVudDpzcGFjZS1iZXR3ZWVuO2FsaWduLWl0ZW1zOmNlbnRlcjttYXJnaW4tYm90dG9tOjEycHgiPgogICAgICAgIDxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMnB4O2NvbG9yOiM3YThmYTYiPjQgbmV1ZSBBbnR3b3J0ZW4gwrcgYXV0b21hdGlzY2ggZXJrYW5udCB2aWEgR21haWw8L2Rpdj4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLXNtIGJ0bi1wcmltYXJ5IiBvbmNsaWNrPSJydW5BSSgnRXJzdGVsbGUgS0ktQW50d29ydGVuIGbDvHIgYWxsZSA0IG5ldWVuIEthbmRpZGF0ZW4tQW50d29ydGVuIGF1ZiBDWVFVRU8gUmVjcnVpdGluZyBFLU1haWxzLiBQcm9mZXNzaW9uZWxsLCBtaXQgVGVybWludm9yc2NobGFnLicpIj5BbGxlIGJlYW50d29ydGVuIOKGlzwvYnV0dG9uPgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iaW5ib3gtaXRlbSByZXBsaWVkIiBvbmNsaWNrPSJydW5BSSgnTGF1cmEgSG9mZm1hbm4sIENvbXB1dGFjZW50ZXIsIHNlaHIgaW50ZXJlc3NpZXJ0IGFuIENZUVVFTyBBY2NvdW50IE1hbmFnZXIuIEFudHdvcnRtYWlsIG1pdCBUZXJtaW52b3JzY2hsw6RnZW4gZsO8ciBkaWVzZSBXb2NoZS4nKSI+CiAgICAgICAgPGRpdiBjbGFzcz0iYXYgYXYtdCI+TEg8L2Rpdj4KICAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjEiPjxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMnB4O2ZvbnQtd2VpZ2h0OjUwMCI+TGF1cmEgSG9mZm1hbm48L2Rpdj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTFweDtjb2xvcjojN2E4ZmE2Ij5SZTogVm9tIFN5c3RlbWhhdXMgenVtIE1TU1Ag4oCUIHNlaHIgaW50ZXJlc3NhbnQhPC9kaXY+PC9kaXY+CiAgICAgICAgPHNwYW4gY2xhc3M9InRhZyB0YWctZ3JlZW4iPkludGVyZXNzaWVydDwvc3Bhbj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTBweDtjb2xvcjojYTBiMGMwO21hcmdpbi1sZWZ0OjhweCI+MDk6MTQ8L2Rpdj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImluYm94LWl0ZW0gdW5yZWFkIiBvbmNsaWNrPSJydW5BSSgnTWFya3VzIFJpY2h0ZXIsIEJlY2h0bGUsIGZyYWd0IG5hY2ggRGV0YWlscyB6dSBDWVFVRU8gR2VoYWx0c3N0cnVrdHVyIHVuZCBQcm92aXNpb24uIFByb2Zlc3Npb25lbGxlIEFudHdvcnQgbWl0IFRyYW5zcGFyZW56IGFiZXIgb2huZSBrb25rcmV0ZSBaYWhsZW4uJykiPgogICAgICAgIDxkaXYgY2xhc3M9ImF2IGF2LWIiPk1SPC9kaXY+CiAgICAgICAgPGRpdiBzdHlsZT0iZmxleDoxIj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTJweDtmb250LXdlaWdodDo1MDAiPk1hcmt1cyBSaWNodGVyPC9kaXY+PGRpdiBzdHlsZT0iZm9udC1zaXplOjExcHg7Y29sb3I6IzdhOGZhNiI+UmU6IEZyYWdlIHp1ciBHZWhhbHRzc3RydWt0dXIgYmVpIENZUVVFTzwvZGl2PjwvZGl2PgogICAgICAgIDxzcGFuIGNsYXNzPSJ0YWcgdGFnLWJsdWUiPlLDvGNrZnJhZ2U8L3NwYW4+PGRpdiBzdHlsZT0iZm9udC1zaXplOjEwcHg7Y29sb3I6I2EwYjBjMDttYXJnaW4tbGVmdDo4cHgiPjExOjMyPC9kaXY+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJpbmJveC1pdGVtIHVucmVhZCIgb25jbGljaz0icnVuQUkoJ0FubmEgV29sZiwgSW5ncmFtIE1pY3JvLCBpbnRlcmVzc2llcnQgYW4gQ1lRVUVPIFZlcnRyaWVic2lubmVuZGllbnN0LiBBbnR3b3J0bWFpbCBtaXQgMyBUZXJtaW52b3JzY2hsw6RnZW4uJykiPgogICAgICAgIDxkaXYgY2xhc3M9ImF2IGF2LWIiPkFXPC9kaXY+CiAgICAgICAgPGRpdiBzdHlsZT0iZmxleDoxIj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTJweDtmb250LXdlaWdodDo1MDAiPkFubmEgV29sZjwvZGl2PjxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMXB4O2NvbG9yOiM3YThmYTYiPlJlOiBWZXJ0cmllYnNpbm5lbmRpZW5zdCDigJQga2xpbmd0IGludGVyZXNzYW50PC9kaXY+PC9kaXY+CiAgICAgICAgPHNwYW4gY2xhc3M9InRhZyB0YWctZ3JlZW4iPkludGVyZXNzaWVydDwvc3Bhbj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTBweDtjb2xvcjojYTBiMGMwO21hcmdpbi1sZWZ0OjhweCI+MTQ6MDU8L2Rpdj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImluYm94LWl0ZW0iIG9uY2xpY2s9InJ1bkFJKCdLYW5kaWRhdCBsZWhudCBDWVFVRU8gQW5mcmFnZSBhYiwgYWt0dWVsbCBuaWNodCBvZmZlbi4gUHJvZmVzc2lvbmVsbGUgQW50d29ydCwgVMO8ciBvZmZlbiBoYWx0ZW4sIGluIDYgTW9uYXRlbiB3aWVkZXIgbWVsZGVuLicpIj4KICAgICAgICA8ZGl2IGNsYXNzPSJhdiBhdi1yIj5QVzwvZGl2PgogICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MSI+PGRpdiBzdHlsZT0iZm9udC1zaXplOjEycHg7Zm9udC13ZWlnaHQ6NTAwIj5QZXRlciBXYWduZXI8L2Rpdj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTFweDtjb2xvcjojN2E4ZmE2Ij5SZTogQWt0dWVsbCBrZWluIEludGVyZXNzZTwvZGl2PjwvZGl2PgogICAgICAgIDxzcGFuIGNsYXNzPSJ0YWcgdGFnLWdyYXkiPkFiZ2VsZWhudDwvc3Bhbj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTBweDtjb2xvcjojYTBiMGMwO21hcmdpbi1sZWZ0OjhweCI+Z2VzdGVybjwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgoKICAgIDwhLS0g4pWQ4pWQIENBTEVOREFSIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAtLT4KICAgIDxkaXYgaWQ9InZpZXctY2FsZW5kYXIiIGNsYXNzPSJ2aWV3Ij4KICAgICAgPGRpdiBjbGFzcz0iZ3JpZC0yIj4KICAgICAgICA8ZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICAgICAgICAgIDxkaXYgY2xhc3M9ImNhcmQtdGl0bGUiPuKXtyBNYWkgMjAyNTwvZGl2PgogICAgICAgICAgICA8ZGl2IGNsYXNzPSJjYWwtbWluaSI+CiAgICAgICAgICAgICAgPGRpdiBjbGFzcz0iY2FsLWhkciI+TW88L2Rpdj48ZGl2IGNsYXNzPSJjYWwtaGRyIj5EaTwvZGl2PjxkaXYgY2xhc3M9ImNhbC1oZHIiPk1pPC9kaXY+PGRpdiBjbGFzcz0iY2FsLWhkciI+RG88L2Rpdj48ZGl2IGNsYXNzPSJjYWwtaGRyIj5GcjwvZGl2PjxkaXYgY2xhc3M9ImNhbC1oZHIiPlNhPC9kaXY+PGRpdiBjbGFzcz0iY2FsLWhkciI+U288L2Rpdj4KICAgICAgICAgICAgICA8ZGl2IGNsYXNzPSJjYWwtZGF5Ij48L2Rpdj48ZGl2IGNsYXNzPSJjYWwtZGF5Ij48L2Rpdj48ZGl2IGNsYXNzPSJjYWwtZGF5Ij48L2Rpdj48ZGl2IGNsYXNzPSJjYWwtZGF5Ij4xPC9kaXY+PGRpdiBjbGFzcz0iY2FsLWRheSI+MjwvZGl2PjxkaXYgY2xhc3M9ImNhbC1kYXkiPjM8L2Rpdj48ZGl2IGNsYXNzPSJjYWwtZGF5Ij40PC9kaXY+CiAgICAgICAgICAgICAgPGRpdiBjbGFzcz0iY2FsLWRheSI+NTwvZGl2PjxkaXYgY2xhc3M9ImNhbC1kYXkiPjY8L2Rpdj48ZGl2IGNsYXNzPSJjYWwtZGF5Ij43PC9kaXY+PGRpdiBjbGFzcz0iY2FsLWRheSI+ODwvZGl2PjxkaXYgY2xhc3M9ImNhbC1kYXkiPjk8L2Rpdj48ZGl2IGNsYXNzPSJjYWwtZGF5Ij4xMDwvZGl2PjxkaXYgY2xhc3M9ImNhbC1kYXkiPjExPC9kaXY+CiAgICAgICAgICAgICAgPGRpdiBjbGFzcz0iY2FsLWRheSI+MTI8L2Rpdj48ZGl2IGNsYXNzPSJjYWwtZGF5Ij4xMzwvZGl2PjxkaXYgY2xhc3M9ImNhbC1kYXkgdG9kYXkiPjE0PC9kaXY+PGRpdiBjbGFzcz0iY2FsLWRheSBoYXMtZXYiPjE1PC9kaXY+PGRpdiBjbGFzcz0iY2FsLWRheSBoYXMtZXYiPjE2PC9kaXY+PGRpdiBjbGFzcz0iY2FsLWRheSI+MTc8L2Rpdj48ZGl2IGNsYXNzPSJjYWwtZGF5Ij4xODwvZGl2PgogICAgICAgICAgICAgIDxkaXYgY2xhc3M9ImNhbC1kYXkgaGFzLWV2Ij4xOTwvZGl2PjxkaXYgY2xhc3M9ImNhbC1kYXkgaGFzLWV2Ij4yMDwvZGl2PjxkaXYgY2xhc3M9ImNhbC1kYXkgaGFzLWV2Ij4yMTwvZGl2PjxkaXYgY2xhc3M9ImNhbC1kYXkiPjIyPC9kaXY+PGRpdiBjbGFzcz0iY2FsLWRheSI+MjM8L2Rpdj48ZGl2IGNsYXNzPSJjYWwtZGF5Ij4yNDwvZGl2PjxkaXYgY2xhc3M9ImNhbC1kYXkiPjI1PC9kaXY+CiAgICAgICAgICAgIDwvZGl2PgogICAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLXNtIGJ0bi1wcmltYXJ5IiBzdHlsZT0id2lkdGg6MTAwJTttYXJnaW4tdG9wOjhweCIgb25jbGljaz0icnVuQUkoJ0Vyc3RlbGxlIFRlcm1pbnZvcnNjaGxhZy1NYWlsIGbDvHIgbmV1ZW4gQ1lRVUVPIEthbmRpZGF0ZW4uIDMgWmVpdHNsb3RzIGRpZXNlL27DpGNoc3RlIFdvY2hlLCBHb29nbGUgTWVldCBMaW5rLCBrdXJ6ZSBBZ2VuZGE6IFZvcnN0ZWxsdW5nIENZUVVFTywgUm9sbGUsIEZyYWdlbi4nKSI+KyBUZXJtaW4gZXJzdGVsbGVuIOKGlzwvYnV0dG9uPgogICAgICAgICAgPC9kaXY+CiAgICAgICAgPC9kaXY+CiAgICAgICAgPGRpdj4KICAgICAgICAgIDxkaXYgY2xhc3M9ImNhcmQiPgogICAgICAgICAgICA8ZGl2IGNsYXNzPSJjYXJkLXRpdGxlIj7wn5OFIERpZXNlIFdvY2hlPC9kaXY+CiAgICAgICAgICAgIDxkaXYgY2xhc3M9ImV2LWl0ZW0iPgogICAgICAgICAgICAgIDxkaXYgY2xhc3M9ImV2LXRpbWUiPk1pIDE1OjAwPC9kaXY+CiAgICAgICAgICAgICAgPGRpdiBzdHlsZT0iZmxleDoxIj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTJweDtmb250LXdlaWdodDo1MDAiPkxhdXJhIEhvZmZtYW5uIOKAlCBBY2NvdW50IE1hbmFnZXI8L2Rpdj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTBweDtjb2xvcjojN2E4ZmE2Ij5Db21wdXRhY2VudGVyIOKGkiBDWVFVRU8gwrcgR29vZ2xlIE1lZXQgwrcgNDUgTWluPC9kaXY+PC9kaXY+CiAgICAgICAgICAgICAgPHNwYW4gY2xhc3M9ImV2LWJhZGdlIGV2LW1lZXQiPk1lZXQ8L3NwYW4+CiAgICAgICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSIgb25jbGljaz0icnVuQUkoJ0ludGVydmlldyBMYXVyYSBIb2ZmbWFubiBDWVFVRU8gQWNjb3VudCBNYW5hZ2VyIG1vcmdlbiAxNSBVaHIuIDUgSHVudGVyLUNoZWNrIEZyYWdlbiArIEdlc3Byw6RjaHNsZWl0ZmFkZW4gKyBFcmlubmVydW5nc21haWwuJykiPlByZXAg4oaXPC9idXR0b24+CiAgICAgICAgICAgIDwvZGl2PgogICAgICAgICAgICA8ZGl2IGNsYXNzPSJldi1pdGVtIj4KICAgICAgICAgICAgICA8ZGl2IGNsYXNzPSJldi10aW1lIj5EbyAxMDozMDwvZGl2PgogICAgICAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MSI+PGRpdiBzdHlsZT0iZm9udC1zaXplOjEycHg7Zm9udC13ZWlnaHQ6NTAwIj5NYXJrdXMgUmljaHRlciDigJQgRXJzdGdlc3Byw6RjaDwvZGl2PjxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMHB4O2NvbG9yOiM3YThmYTYiPkJlY2h0bGUgSGFtYnVyZyDCtyBUZWxlZm9uIMK3IDMwIE1pbjwvZGl2PjwvZGl2PgogICAgICAgICAgICAgIDxzcGFuIGNsYXNzPSJldi1iYWRnZSBldi1waG9uZSI+VGVsZWZvbjwvc3Bhbj4KICAgICAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLXNtIiBvbmNsaWNrPSJydW5BSSgnRXJzdGdlc3Byw6RjaCBNYXJrdXMgUmljaHRlciBCZWNodGxlIGbDvHIgQ1lRVUVPIEFjY291bnQgTWFuYWdlci4gR2VzcHLDpGNoc2xlaXRmYWRlbiBtaXQgSHVudGVyLUNoZWNrOiBXaWUgdmllbGUgR2VzcHLDpGNoZSB0w6RnbGljaD8gT3V0Ym91bmQtRXJmYWhydW5nPyBLYWx0YWtxdWlzZT8nKSI+TGVpdGZhZGVuIOKGlzwvYnV0dG9uPgogICAgICAgICAgICA8L2Rpdj4KICAgICAgICAgICAgPGRpdiBjbGFzcz0iZXYtaXRlbSI+CiAgICAgICAgICAgICAgPGRpdiBjbGFzcz0iZXYtdGltZSI+TW8gMDk6MDA8L2Rpdj4KICAgICAgICAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjEiPjxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMnB4O2ZvbnQtd2VpZ2h0OjUwMCI+QXV0bzogNCBGb2xsb3ctdXBzIHdlcmRlbiBnZXNlbmRldDwvZGl2PjxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMHB4O2NvbG9yOiM3YThmYTYiPlNhbmRyYSBLLiwgRmVsaXggSC4sIENocmlzdGluZSBSLiwgVGhvbWFzIEsuPC9kaXY+PC9kaXY+CiAgICAgICAgICAgICAgPHNwYW4gY2xhc3M9ImV2LWJhZGdlIiBzdHlsZT0iYmFja2dyb3VuZDojZjBlZGZmO2NvbG9yOiM2YzNmYzciPkF1dG88L3NwYW4+CiAgICAgICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJ0bi1zbSIgb25jbGljaz0icnVuQUkoJ1ZvcnNjaGF1IGRlciA0IGF1dG9tYXRpc2NoZW4gRm9sbG93LXVwcyBNb250YWcgZsO8ciBTYW5kcmEgS3LDvGdlciwgRmVsaXggSGFydG1hbm4sIENocmlzdGluZSBSb3RoLCBUaG9tYXMgS2xlaW4g4oCUIENZUVVFTyBSZWNydWl0aW5nLicpIj5Wb3JzY2hhdSDihpc8L2J1dHRvbj4KICAgICAgICAgICAgPC9kaXY+CiAgICAgICAgICA8L2Rpdj4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj4KCiAgICA8IS0tIOKVkOKVkCBDT01QQU5ZIElOVEVMTElHRU5DRSDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAgLS0+CiAgICA8ZGl2IGlkPSJ2aWV3LWludGVsbGlnZW5jZSIgY2xhc3M9InZpZXciPgogICAgICA8ZGl2IGNsYXNzPSJkbmEtYmFubmVyIj4KICAgICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6ZmxleC1zdGFydDtnYXA6MTRweDttYXJnaW4tYm90dG9tOjE0cHgiPgogICAgICAgICAgPGRpdiBzdHlsZT0id2lkdGg6NTBweDtoZWlnaHQ6NTBweDtiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCgxMzVkZWcsIzE4NUZBNSwjNGE5MGQ5KTtib3JkZXItcmFkaXVzOjEycHg7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2ZvbnQtc2l6ZToxNnB4O2ZvbnQtd2VpZ2h0OjcwMDtjb2xvcjp3aGl0ZTtmbGV4LXNocmluazowIj5DUTwvZGl2PgogICAgICAgICAgPGRpdj4KICAgICAgICAgICAgPGRpdiBzdHlsZT0iZm9udC1zaXplOjE2cHg7Zm9udC13ZWlnaHQ6NTAwO2NvbG9yOndoaXRlIj5DWVFVRU8gR21iSCDCtyBjeXF1ZW8uY29tPC9kaXY+CiAgICAgICAgICAgIDxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMXB4O2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsMC40KTttYXJnaW4tYm90dG9tOjhweCI+TcO8bmNoZW4gwrcgTVNTUCDCtyAyMCsgSmFocmUgwrcgREFDSCArIEludGVybmF0aW9uYWwgwrcgSG9tZW9mZmljZSBkZXV0c2NobGFuZHdlaXQ8L2Rpdj4KICAgICAgICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2ZsZXgtd3JhcDp3cmFwO2dhcDo0cHgiPgogICAgICAgICAgICAgIDxzcGFuIHN0eWxlPSJmb250LXNpemU6MTBweDtwYWRkaW5nOjJweCA4cHg7Ym9yZGVyLXJhZGl1czoxMHB4O2JhY2tncm91bmQ6cmdiYSgyNTUsMjU1LDI1NSwwLjEpO2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsMC43KSI+TVNTUDwvc3Bhbj4KICAgICAgICAgICAgICA8c3BhbiBzdHlsZT0iZm9udC1zaXplOjEwcHg7cGFkZGluZzoycHggOHB4O2JvcmRlci1yYWRpdXM6MTBweDtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC4xKTtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuNykiPkt1bnVudSBUb3AgQ29tcGFueSAyMDIy4oCTMjAyNjwvc3Bhbj4KICAgICAgICAgICAgICA8c3BhbiBzdHlsZT0iZm9udC1zaXplOjEwcHg7cGFkZGluZzoycHggOHB4O2JvcmRlci1yYWRpdXM6MTBweDtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsMC4xKTtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuNykiPjEsNiBNaW8uIGdlc2Now7x0enRlIE1pdGFyYmVpdGVyPC9zcGFuPgogICAgICAgICAgICAgIDxzcGFuIHN0eWxlPSJmb250LXNpemU6MTBweDtwYWRkaW5nOjJweCA4cHg7Ym9yZGVyLXJhZGl1czoxMHB4O2JhY2tncm91bmQ6cmdiYSgyNTUsMjU1LDI1NSwwLjEpO2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsMC43KSI+MTcgUGFydG5lci1IZXJzdGVsbGVyPC9zcGFuPgogICAgICAgICAgICA8L2Rpdj4KICAgICAgICAgIDwvZGl2PgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImRuYS1rcGlzIj4KICAgICAgICAgIDxkaXYgY2xhc3M9ImRuYS1rcGkiPjxkaXYgY2xhc3M9ImRuYS1rcGktdiI+MTA8L2Rpdj48ZGl2IGNsYXNzPSJkbmEta3BpLWwiPkVudHNjaGVpZGVyZ2VzcHLDpGNoZTxicj5wcm8gVGFnPC9kaXY+PC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJkbmEta3BpIj48ZGl2IGNsYXNzPSJkbmEta3BpLXYiPjU8L2Rpdj48ZGl2IGNsYXNzPSJkbmEta3BpLWwiPlF1YWwuIE9wcG9ydHVuaXRpZXM8YnI+cHJvIFdvY2hlPC9kaXY+PC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJkbmEta3BpIj48ZGl2IGNsYXNzPSJkbmEta3BpLXYiPjk4JTwvZGl2PjxkaXYgY2xhc3M9ImRuYS1rcGktbCI+Q3VzdG9tZXI8YnI+UmV0ZW50aW9uPC9kaXY+PC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJkbmEta3BpIj48ZGl2IGNsYXNzPSJkbmEta3BpLXYiPjkuNzwvZGl2PjxkaXYgY2xhc3M9ImRuYS1rcGktbCI+Q1NBVCBTY29yZTxicj4vIDEwPC9kaXY+PC9kaXY+CiAgICAgICAgPC9kaXY+CiAgICAgICAgPGRpdiBzdHlsZT0iYmFja2dyb3VuZDpyZ2JhKDI1NSwyNTUsMjU1LDAuMDYpO2JvcmRlci1yYWRpdXM6OHB4O3BhZGRpbmc6MTBweCAxMnB4O21hcmdpbi10b3A6MTJweDtmb250LXNpemU6MTFweDtjb2xvcjpyZ2JhKDI1NSwyNTUsMjU1LDAuNjUpO2xpbmUtaGVpZ2h0OjEuNiI+CiAgICAgICAgICA8c3Ryb25nIHN0eWxlPSJjb2xvcjojN2ViM2U4Ij5WZXJ0cmllYnMtRE5BOjwvc3Ryb25nPiBPbGRzY2hvb2wgSHVudGVyLUt1bHR1ci4gMTAgR2VzcHLDpGNoZSB0w6RnbGljaCwgaG9oZSBUYWt0ZnJlcXVlbnouIElkZWFsa2FuZGlkYXRlbiBrb21tZW4gdm9uIDxzdHJvbmcgc3R5bGU9ImNvbG9yOiM3ZWIzZTgiPklULVJlc2VsbGVybiB1bmQgRGlzdHJpYnV0b3Jlbjwvc3Ryb25nPiDigJQgbmljaHQgdm9uIEhlcnN0ZWxsZXJuLiBTZW5pb3IgRW50ZXJwcmlzZSBBTXMgcGFzc2VuIG5pY2h0LiBDWVFVRU8gaXN0IGRlciBLYXJyaWVyZXNjaHJpdHQgenVyIFNlY3VyaXR5LVNwZXppYWxpc2llcnVuZy4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CgogICAgICA8ZGl2IGNsYXNzPSJncmlkLTIiPgogICAgICAgIDxkaXYgY2xhc3M9ImNhcmQiPgogICAgICAgICAgPGRpdiBjbGFzcz0iY2FyZC10aXRsZSI+8J+aqSBSZWQgRmxhZyBEZXRlY3RvcjwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0iZmxhZy1pdGVtIj48ZGl2IGNsYXNzPSJmbGFnLWljb24iPvCfkrg8L2Rpdj48ZGl2PjxkaXYgY2xhc3M9ImZsYWctdGl0bGUiPkhlcnN0ZWxsZXIgYWxzIGxldHp0ZXIgSm9iPC9kaXY+PGRpdiBjbGFzcz0iZmxhZy1kZXNjIj5HZWhhbHRzZXJ3YXJ0dW5nIGlua29tcGF0aWJlbCwgQXJiZWl0c3dlaXNlIGZhbHNjaC48L2Rpdj48c3BhbiBjbGFzcz0iZmxhZy1zaWcgc2lnLXIiPkNyb3dkU3RyaWtlLCBac2NhbGVyLCBTZW50aW5lbE9uZSwgUGFsbyBBbHRvIGltIENWPC9zcGFuPjwvZGl2PjwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0iZmxhZy1pdGVtIj48ZGl2IGNsYXNzPSJmbGFnLWljb24iPvCfkKI8L2Rpdj48ZGl2PjxkaXYgY2xhc3M9ImZsYWctdGl0bGUiPlN0cmF0ZWdpc2NoZXIgU2xvdy1DeWNsZSBWZXJ0cmllYjwvZGl2PjxkaXYgY2xhc3M9ImZsYWctZGVzYyI+S2VubnQgMi0zIERlYWxzL1F1YXJ0YWwg4oCUIENZUVVFTyBicmF1Y2h0IDEwIEdlc3Byw6RjaGUgdMOkZ2xpY2guPC9kaXY+PHNwYW4gY2xhc3M9ImZsYWctc2lnIHNpZy1yIj4iS2V5IEFjY291bnQiLCAiU3RyYXRlZ2ljIiwgIkV4ZWN1dGl2ZSBSZWxhdGlvbnNoaXAiPC9zcGFuPjwvZGl2PjwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0iZmxhZy1pdGVtIj48ZGl2IGNsYXNzPSJmbGFnLWljb24iPvCfmqs8L2Rpdj48ZGl2PjxkaXYgY2xhc3M9ImZsYWctdGl0bGUiPktlaW4gSVQtQmFja2dyb3VuZDwvZGl2PjxkaXYgY2xhc3M9ImZsYWctZGVzYyI+S2FubiBTZWN1cml0eS1Qb3J0Zm9saW8gbmljaHQgZXJrbMOkcmVuLCB6dSBsYW5nZSBFaW5hcmJlaXR1bmcuPC9kaXY+PHNwYW4gY2xhc3M9ImZsYWctc2lnIHNpZy1yIj5GTUNHLCBQaGFybWEsIEZpbmFueiBvaG5lIElUPC9zcGFuPjwvZGl2PjwvZGl2PgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImNhcmQiPgogICAgICAgICAgPGRpdiBjbGFzcz0iY2FyZC10aXRsZSI+4pyFIEdyZWVuIEZsYWdzPC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJmbGFnLWl0ZW0iPjxkaXYgY2xhc3M9ImZsYWctaWNvbiI+4pqhPC9kaXY+PGRpdj48ZGl2IGNsYXNzPSJmbGFnLXRpdGxlIj5Ib2hlIEFrdGl2aXTDpHQgbmFjaHdlaXNiYXI8L2Rpdj48c3BhbiBjbGFzcz0iZmxhZy1zaWcgc2lnLWciPkthbHRha3F1aXNlLCBPdXRib3VuZCwgUXVvdGEgMTAwJSssIE5ldWt1bmRlbmFudGVpbDwvc3Bhbj48L2Rpdj48L2Rpdj4KICAgICAgICAgIDxkaXYgY2xhc3M9ImZsYWctaXRlbSI+PGRpdiBjbGFzcz0iZmxhZy1pY29uIj7wn4+X77iPPC9kaXY+PGRpdj48ZGl2IGNsYXNzPSJmbGFnLXRpdGxlIj5SZXNlbGxlciAvIFN5c3RlbWhhdXMgQmFja2dyb3VuZDwvZGl2PjxzcGFuIGNsYXNzPSJmbGFnLXNpZyBzaWctZyI+QmVjaHRsZSwgQ29tcHV0YWNlbnRlciwgQ2FuY29tLCBBeGlhbnMsIENvbnRyb2x3YXJlPC9zcGFuPjwvZGl2PjwvZGl2PgogICAgICAgICAgPGRpdiBjbGFzcz0iZmxhZy1pdGVtIj48ZGl2IGNsYXNzPSJmbGFnLWljb24iPvCfk6Y8L2Rpdj48ZGl2PjxkaXYgY2xhc3M9ImZsYWctdGl0bGUiPkRpc3RyaWJ1dGlvbiBCYWNrZ3JvdW5kPC9kaXY+PHNwYW4gY2xhc3M9ImZsYWctc2lnIHNpZy1nIj5JbmdyYW0sIEFycm93LCBBbHNvLCBURCBTeW5uZXgg4oCUIFByb2R1a3RlICsgRnJlcXVlbno8L3NwYW4+PC9kaXY+PC9kaXY+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJmbGFnLWl0ZW0iPjxkaXYgY2xhc3M9ImZsYWctaWNvbiI+8J+OrzwvZGl2PjxkaXY+PGRpdiBjbGFzcz0iZmxhZy10aXRsZSI+S2FycmllcmUtTG9naWsgcGFzc3Q8L2Rpdj48c3BhbiBjbGFzcz0iZmxhZy1zaWcgc2lnLWciPldpbGwgU2VjdXJpdHktU3BlemlhbGlzdCB3ZXJkZW4g4oCUIENZUVVFTyBhbHMgbsOkY2hzdGVyIFNjaHJpdHQ8L3NwYW4+PC9kaXY+PC9kaXY+CiAgICAgICAgPC9kaXY+CiAgICAgIDwvZGl2PgoKICAgICAgPCEtLSBDViBTQ1JFRU5FUiAtLT4KICAgICAgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICAgICAgPGRpdiBjbGFzcz0iY2FyZC10aXRsZSI+4pqhIENWIC8gUHJvZmlsIHByw7xmZW48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJpbmZvLWJveCI+TGViZW5zbGF1ZiBvZGVyIExpbmtlZEluLVByb2ZpbCBlaW5mw7xnZW4g4oaSIEtJIGdpYnQgc29mb3J0IOKchSBFSU5MQURFTiBvZGVyIPCfmqkgQUJMRUhORU4gbWl0IEJlZ3LDvG5kdW5nPC9kaXY+CiAgICAgICAgPHRleHRhcmVhIGNsYXNzPSJ0ZXh0YXJlYS1maWVsZCIgaWQ9ImN2LWlucHV0IiBwbGFjZWhvbGRlcj0iTGViZW5zbGF1ZiBvZGVyIExpbmtlZEluLVByb2ZpbCBoaWVyIGVpbmbDvGdlbi4uLiIgc3R5bGU9Im1pbi1oZWlnaHQ6ODBweCI+PC90ZXh0YXJlYT4KICAgICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjhweDttYXJnaW4tdG9wOjhweDtmbGV4LXdyYXA6d3JhcCI+CiAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLXByaW1hcnkiIG9uY2xpY2s9InNjcmVlbkNWKCkiPkF1ZiBDWVFVRU8tUGFzc3VuZyBwcsO8ZmVuIOKGlzwvYnV0dG9uPgogICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBvbmNsaWNrPSJkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnY3YtaW5wdXQnKS52YWx1ZT0nTWFyY3VzIFdlYmVyLCAzNCDCtyBBY2NvdW50IE1hbmFnZXIgQ29tcHV0YWNlbnRlciBBRyAoMyBKYWhyZSlcbi0gSVQgU2VjdXJpdHkgVmVydHJpZWIgTWl0dGVsc3RhbmQgTlJXXG4tIFF1b3RlbmVyZsO8bGx1bmcgMTE4JSAoMjAyMyksIDEyNCUgKDIwMjQpXG4tIDgtMTIgT3V0Ym91bmQtR2VzcHLDpGNoZSB0w6RnbGljaCwgYWt0aXZlIEthbHRha3F1aXNlXG4tIFZvcmhlcjogSW5zaWRlIFNhbGVzIFREIFN5bm5leCAoMiBKYWhyZSlcbkF1c2JpbGR1bmc6IElULVN5c3RlbWthdWZtYW5uJyI+4pyFIEd1dGVzIEJlaXNwaWVsPC9idXR0b24+CiAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYnRuLWRhbmdlciIgb25jbGljaz0iZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2N2LWlucHV0JykudmFsdWU9J0FsZXhhbmRlciBLw7ZuaWcsIDQyIMK3IFNlbmlvciBFbnRlcnByaXNlIEFFIENyb3dkU3RyaWtlICg0IEphaHJlKVxuLSBTdHJhdGVnaWMgTmFtZWQgQWNjb3VudHMgREFDSCwgMTUgRW50ZXJwcmlzZS1LdW5kZW5cbi0gw5ggRGVhbC1TaXplIOKCrDQ1MC4wMDAsIDItMyBuZXVlIExvZ29zL0phaHJcbi0gRXhlY3V0aXZlIFJlbGF0aW9uc2hpcHMgenUgQ0lTT3Ncbi0gR2VoYWx0c2Vyd2FydHVuZzogMTQ1LjAwMOKCrCBPVEVcbi0gRGF2b3I6IFBhbG8gQWx0byBOZXR3b3JrcyBFbnRlcnByaXNlIFNhbGVzJyI+8J+aqSBTY2hsZWNodGVzIEJlaXNwaWVsPC9idXR0b24+CiAgICAgICAgPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0iY3YtcmVzdWx0IiBpZD0ic2NyZWVuLXJlc3VsdCI+PC9kaXY+CiAgICAgIDwvZGl2PgogICAgPC9kaXY+CgogICAgPCEtLSDilZDilZAgR01BSUwgU0VUVVAg4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQIC0tPgogICAgPGRpdiBpZD0idmlldy1zZXR1cCIgY2xhc3M9InZpZXciPgogICAgICA8ZGl2IGNsYXNzPSJpbmZvLWJveCI+RWlubWFsaWcgZWlucmljaHRlbiDigJQgZGFuYWNoIGzDpHVmdCBhbGxlcyBhdXRvbWF0aXNjaC4gRGVpbiBJVC1Lb2xsZWdlIGJyYXVjaHQgY2EuIDMwIE1pbnV0ZW4uPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InNldHVwLXN0ZXAgZG9uZSI+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RlcC1oZHIiPjxkaXYgY2xhc3M9InN0ZXAtbnVtIHNuLWRvbmUiPuKckzwvZGl2PjxkaXY+PGRpdiBzdHlsZT0iZm9udC1zaXplOjEzcHg7Zm9udC13ZWlnaHQ6NTAwIj5Hb29nbGUgQ2xvdWQgUHJvamVrdCBhbmxlZ2VuPC9kaXY+PGRpdiBzdHlsZT0iZm9udC1zaXplOjExcHg7Y29sb3I6IzdhOGZhNiI+R21haWwgQVBJICsgR29vZ2xlIENhbGVuZGFyIEFQSSBha3RpdmllcmVuPC9kaXY+PC9kaXY+PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RlcC1ib2R5Ij48ZGl2IGNsYXNzPSJnb29kLWJveCI+Y29uc29sZS5jbG91ZC5nb29nbGUuY29tIOKGkiBOZXVlcyBQcm9qZWt0ICJDb3ZlbmV4LVJlY3J1aXRpbmciIOKGkiBBUElzIGFrdGl2aWVyZW46IEdtYWlsIEFQSSArIEdvb2dsZSBDYWxlbmRhciBBUEk8L2Rpdj48YnV0dG9uIGNsYXNzPSJidG4gYnRuLXNtIiBvbmNsaWNrPSJydW5BSSgnU2Nocml0dC1mw7xyLVNjaHJpdHQgQW5sZWl0dW5nOiBHb29nbGUgQ2xvdWQgQ29uc29sZSwgUHJvamVrdCBhbmxlZ2VuLCBHbWFpbCBBUEkgdW5kIEdvb2dsZSBDYWxlbmRhciBBUEkgYWt0aXZpZXJlbi4gRsO8ciBJVC1Lb2xsZWdlbiBkZXIgZGFzIG5vY2ggbmllIGdlbWFjaHQgaGF0LicpIj5BbmxlaXR1bmcg4oaXPC9idXR0b24+PC9kaXY+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzZXR1cC1zdGVwIGFjdGl2ZS1zdGVwIiBpZD0ic3RlcDIiPgogICAgICAgIDxkaXYgY2xhc3M9InN0ZXAtaGRyIj48ZGl2IGNsYXNzPSJzdGVwLW51bSBzbi1hY3RpdmUiPjI8L2Rpdj48ZGl2PjxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxM3B4O2ZvbnQtd2VpZ2h0OjUwMCI+T0F1dGggMi4wIENyZWRlbnRpYWxzPC9kaXY+PGRpdiBzdHlsZT0iZm9udC1zaXplOjExcHg7Y29sb3I6IzdhOGZhNiI+Q2xpZW50IElEICsgU2VjcmV0IGF1cyBHb29nbGUgQ29uc29sZTwvZGl2PjwvZGl2PjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9InN0ZXAtYm9keSI+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJsYWJlbCI+Q2xpZW50IElEPC9kaXY+CiAgICAgICAgICA8aW5wdXQgY2xhc3M9ImlucHV0LWZpZWxkIiBpZD0iY2xpZW50LWlkIiBwbGFjZWhvbGRlcj0ieHh4eC5hcHBzLmdvb2dsZXVzZXJjb250ZW50LmNvbSIgc3R5bGU9Im1hcmdpbi1ib3R0b206OHB4Ij4KICAgICAgICAgIDxkaXYgY2xhc3M9ImxhYmVsIj5DbGllbnQgU2VjcmV0PC9kaXY+CiAgICAgICAgICA8aW5wdXQgY2xhc3M9ImlucHV0LWZpZWxkIiBpZD0iY2xpZW50LXNlY3JldCIgdHlwZT0icGFzc3dvcmQiIHBsYWNlaG9sZGVyPSJHT0NTUFgtLi4uIiBzdHlsZT0ibWFyZ2luLWJvdHRvbToxMHB4Ij4KICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tcHJpbWFyeSIgb25jbGljaz0ic2F2ZUNyZWRlbnRpYWxzKCkiPlNwZWljaGVybiAmIHdlaXRlcjwvYnV0dG9uPgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic2V0dXAtc3RlcCIgaWQ9InN0ZXAzIj4KICAgICAgICA8ZGl2IGNsYXNzPSJzdGVwLWhkciI+PGRpdiBjbGFzcz0ic3RlcC1udW0gc24tcGVuZCIgaWQ9InMzbiI+MzwvZGl2PjxkaXY+PGRpdiBzdHlsZT0iZm9udC1zaXplOjEzcHg7Zm9udC13ZWlnaHQ6NTAwIj5HbWFpbCBBY2NvdW50IHZlcmJpbmRlbjwvZGl2PjxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxMXB4O2NvbG9yOiM3YThmYTYiPk1pdCBHb29nbGUgV29ya3NwYWNlIGVpbmxvZ2dlbjwvZGl2PjwvZGl2PjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9InN0ZXAtYm9keSIgaWQ9InN0ZXAzLWJvZHkiIHN0eWxlPSJkaXNwbGF5Om5vbmUiPgogICAgICAgICAgPGRpdiBjbGFzcz0ibGFiZWwiPkFic2VuZGVyIEUtTWFpbDwvZGl2PgogICAgICAgICAgPGlucHV0IGNsYXNzPSJpbnB1dC1maWVsZCIgaWQ9InNlbmRlci1lbWFpbCIgcGxhY2Vob2xkZXI9InJlY3J1aXRpbmdAY3lxdWVvLmNvbSIgc3R5bGU9Im1hcmdpbi1ib3R0b206OHB4Ij4KICAgICAgICAgIDxkaXYgY2xhc3M9ImxhYmVsIj5BYnNlbmRlciBOYW1lPC9kaXY+CiAgICAgICAgICA8aW5wdXQgY2xhc3M9ImlucHV0LWZpZWxkIiBpZD0ic2VuZGVyLW5hbWUiIHBsYWNlaG9sZGVyPSJDWVFVRU8gUmVjcnVpdGluZyBUZWFtIiBzdHlsZT0ibWFyZ2luLWJvdHRvbToxMHB4Ij4KICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tZ3JlZW4iIG9uY2xpY2s9ImNvbm5lY3RHbWFpbCgpIj5NaXQgR29vZ2xlIHZlcmJpbmRlbiDihpI8L2J1dHRvbj4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InNldHVwLXN0ZXAiIGlkPSJzdGVwNCI+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RlcC1oZHIiPjxkaXYgY2xhc3M9InN0ZXAtbnVtIHNuLXBlbmQiIGlkPSJzNG4iPjQ8L2Rpdj48ZGl2PjxkaXYgc3R5bGU9ImZvbnQtc2l6ZToxM3B4O2ZvbnQtd2VpZ2h0OjUwMCI+QXV0b21hdGlzaWVydW5nIGtvbmZpZ3VyaWVyZW48L2Rpdj48ZGl2IHN0eWxlPSJmb250LXNpemU6MTFweDtjb2xvcjojN2E4ZmE2Ij5Gb2xsb3ctdXAgUmVnZWxuICYgQW50d29ydC1Fcmtlbm51bmc8L2Rpdj48L2Rpdj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJzdGVwLWJvZHkiIGlkPSJzdGVwNC1ib2R5IiBzdHlsZT0iZGlzcGxheTpub25lIj4KICAgICAgICAgIDxkaXYgY2xhc3M9ImdyaWQtMiIgc3R5bGU9Im1hcmdpbi1ib3R0b206MTBweCI+CiAgICAgICAgICAgIDxkaXY+PGRpdiBjbGFzcz0ibGFiZWwiPkZvbGxvdy11cCAxIG5hY2ggKFRhZ2UpPC9kaXY+PGlucHV0IGNsYXNzPSJpbnB1dC1maWVsZCIgdHlwZT0ibnVtYmVyIiB2YWx1ZT0iNSI+PC9kaXY+CiAgICAgICAgICAgIDxkaXY+PGRpdiBjbGFzcz0ibGFiZWwiPkZvbGxvdy11cCAyIG5hY2ggKFRhZ2UpPC9kaXY+PGlucHV0IGNsYXNzPSJpbnB1dC1maWVsZCIgdHlwZT0ibnVtYmVyIiB2YWx1ZT0iMTAiPjwvZGl2PgogICAgICAgICAgICA8ZGl2PjxkaXYgY2xhc3M9ImxhYmVsIj5TZW5kZXplaXQ8L2Rpdj48c2VsZWN0IGNsYXNzPSJpbnB1dC1maWVsZCI+PG9wdGlvbj4wOTowMCBVaHI8L29wdGlvbj48b3B0aW9uPjA4OjMwIFVocjwvb3B0aW9uPjxvcHRpb24+MTA6MDAgVWhyPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAgICAgICAgICAgIDxkaXY+PGRpdiBjbGFzcz0ibGFiZWwiPk1heC4gRS1NYWlscy9UYWc8L2Rpdj48aW5wdXQgY2xhc3M9ImlucHV0LWZpZWxkIiB0eXBlPSJudW1iZXIiIHZhbHVlPSIzMCI+PC9kaXY+CiAgICAgICAgICA8L2Rpdj4KICAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBidG4tZ3JlZW4iIG9uY2xpY2s9ImZpbmlzaFNldHVwKCkiIHN0eWxlPSJ3aWR0aDoxMDAlIj7inJMgU2V0dXAgYWJzY2hsaWXDn2VuIOKAlCBDb3ZlbmV4IFJlY3J1aXRlciBpc3QgYmVyZWl0PC9idXR0b24+CiAgICAgICAgPC9kaXY+CiAgICAgIDwvZGl2PgogICAgPC9kaXY+CgogIDwvZGl2PjwhLS0gL2NvbnRlbnQgLS0+CjwvZGl2PjwhLS0gL21haW4tYXJlYSAtLT4KCjwhLS0g4pWQ4pWQ4pWQIEFJIERSQVdFUiDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAgLS0+CjxkaXYgY2xhc3M9ImFpLWRyYXdlciI+CiAgPGRpdiBjbGFzcz0iYWktZHJhd2VyLWhlYWRlciI+CiAgICA8ZGl2IGNsYXNzPSJhaS1kcmF3ZXItdGl0bGUiPktJLUFzc2lzdGVudDwvZGl2PgogICAgPGRpdiBjbGFzcz0iYWktZHJhd2VyLXN1YiI+Q1lRVUVPIMK3IEh1bnRlci1LdWx0dXIgwrcgUmVzZWxsZXItRm9rdXM8L2Rpdj4KICA8L2Rpdj4KICA8ZGl2IGNsYXNzPSJhaS1tc2dzIiBpZD0iYWktY2hhdCI+CiAgICA8ZGl2IGNsYXNzPSJhbSI+SGFsbG8hIEljaCBrZW5uZSBDWVFVRU8gaW4tIHVuZCBhdXN3ZW5kaWc6IEh1bnRlci1LdWx0dXIsIDEwIEdlc3Byw6RjaGUgdMOkZ2xpY2gsIFJlc2VsbGVyIHVuZCBEaXN0cmlidXRpb24gYWxzIFByaW3DpHJxdWVsbGVuLCBrZWluZSBIZXJzdGVsbGVyLiBXYXMgYnJhdWNoc3QgZHU/PC9kaXY+CiAgPC9kaXY+CiAgPGRpdiBjbGFzcz0iYWktcXVpY2siPgogICAgPGJ1dHRvbiBjbGFzcz0icWIiIG9uY2xpY2s9InJ1bkFJKCdFcnN0ZWxsZSB2b2xsc3TDpG5kaWdlIHBlcnNvbmFsaXNpZXJ0ZSBFcnN0LUUtTWFpbCBmw7xyIGVpbmVuIEFjY291bnQgTWFuYWdlciBiZWkgQmVjaHRsZSBtaXQgSVQgU2VjdXJpdHkgRXJmYWhydW5nIGbDvHIgQ1lRVUVPLiBCZXRyZWZmICsgTWFpbCBhdWYgRGV1dHNjaC4nKSI+RXJzdC1FLU1haWwgUmVzZWxsZXIg4oaXPC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJxYiIgb25jbGljaz0icnVuQUkoJzUgSW50ZXJ2aWV3LUZyYWdlbiBmw7xyIENZUVVFTyBkaWUgSHVudGVyLU1lbnRhbGl0w6R0IHByw7xmZW46IEthbHRha3F1aXNlLUVyZmFocnVuZywgdMOkZ2xpY2hlIEdlc3Byw6RjaHN6YWhsLCBPdXRib3VuZC1Ba3Rpdml0w6R0LicpIj5JbnRlcnZpZXc6IEh1bnRlci1DaGVjayDihpc8L2J1dHRvbj4KICAgIDxidXR0b24gY2xhc3M9InFiIiBvbmNsaWNrPSJydW5BSSgnQm9vbGVhbiBTZWFyY2ggU3RyaW5nIGbDvHIgTGlua2VkSW4gU2FsZXMgTmF2aWdhdG9yOiBJVC1SZXNlbGxlciB1bmQgRGlzdHJpYnV0aW9uIERldXRzY2hsYW5kIGbDvHIgQ1lRVUVPLiBIZXJzdGVsbGVyIGV4cGxpeml0IGF1c3NjaGxpZcOfZW4uJykiPkJvb2xlYW4gU3RyaW5nIOKGlzwvYnV0dG9uPgogIDwvZGl2PgogIDxkaXYgY2xhc3M9ImFpLWlucC1yb3ciPgogICAgPGlucHV0IGNsYXNzPSJhaS1pbnAiIGlkPSJhaS1pbnAiIHR5cGU9InRleHQiIHBsYWNlaG9sZGVyPSJGcmFnZSBzdGVsbGVuLi4uIiBvbmtleWRvd249ImlmKGV2ZW50LmtleT09PSdFbnRlcicpc2VuZElucHV0KCkiPgogICAgPGJ1dHRvbiBjbGFzcz0iYWktc2VuZCIgb25jbGljaz0ic2VuZElucHV0KCkiPuKGkjwvYnV0dG9uPgogIDwvZGl2Pgo8L2Rpdj4KCjwvZGl2PjwhLS0gL2FwcC1zaGVsbCAtLT4KCjxzY3JpcHQ+Ci8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkAovLyBDWVFVRU8gQ09OVEVYVCDigJQgRWluZ2ViZXR0ZXQgaW4gYWxsZSBLSS1DYWxscwovLyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAKY29uc3QgQ1lRVUVPX0NUWCA9IGBEdSBiaXN0IGRlciBLSS1SZWNydWl0aW5nLUFzc2lzdGVudCBmw7xyIENZUVVFTyBHbWJIIChjeXF1ZW8uY29tKSwgTcO8bmNoZW4uCgpDWVFVRU8gRkFDVFM6Ci0gTVNTUCBtaXQgw7xiZXIgMjAgSmFocmVuIEVyZmFocnVuZywgTcO8bmNoZW4gKEZsw7bDn2VyZ2Fzc2UgNCksIERBQ0ggKyBpbnRlcm5hdGlvbmFsCi0gMSw2IE1pby4gZ2VzY2jDvHR6dGUgTWl0YXJiZWl0ZXIsIDUuMDAwKyBQcm9qZWt0ZSwgOTglIEN1c3RvbWVyIFJldGVudGlvbiwgQ1NBVCA5LjcvMTAKLSBLdW51bnUgVG9wIENvbXBhbnkgMjAyMi0yMDI2LCAxNyBQYXJ0bmVyLUhlcnN0ZWxsZXIsIDEwKyBTcHJhY2hlbiBpbSBUZWFtCi0gSG9tZW9mZmljZSBkZXV0c2NobGFuZHdlaXQsIHJlZ2lvbmFsZSBLdW5kZW5iZXRyZXV1bmcgKE5vcmQvT3N0L1dlc3QvU8O8ZCkKClZFUlRSSUVCUy1ETkEgKGRhcyBXaWNodGlnc3RlKToKLSBPbGRzY2hvb2wgSHVudGVyLUt1bHR1cjogMTAgRW50c2NoZWlkZXJnZXNwcsOkY2hlIHBybyBUYWcsIDUgT3Bwb3J0dW5pdGllcyBwcm8gV29jaGUKLSBHcsO8bmRlciBoYXQgZnLDvGhlciBTdGF1YnNhdWdlciBhdWYgZGVyIFN0cmHDn2UgdmVya2F1ZnQg4oCUIGRpZXNlIEVuZXJnaWUgbGVidCBpbSBVbnRlcm5laG1lbgotIEtlaW4gc3RyYXRlZ2lzY2hlciBTbG93LUN5Y2xlIFZlcnRyaWViIOKAlCBha3RpdiwgZGlyZWt0LCBob2hlIFNjaGxhZ3phaGwKCklERUFMRSBLQU5ESURBVEVOIChpbiBQcmlvcml0w6R0KToKMS4gQkVTVEU6IElULVJlc2VsbGVyICYgU3lzdGVtaMOkdXNlcjogQmVjaHRsZSwgQ29tcHV0YWNlbnRlciwgQ2FuY29tLCBBeGlhbnMsIENvbnRyb2x3YXJlLCBDb25zY2lhLCBTSEQsIERhbW92bywgVGVsZWtvbSBNTVMKMi4gQkVTVEU6IElUIERpc3RyaWJ1dGlvbjogSW5ncmFtIE1pY3JvLCBBcnJvdyBFbGVjdHJvbmljcywgQWxzbywgVEQgU3lubmV4LCBFeGNsdXNpdmUgTmV0d29ya3MsIFdlc3Rjb24KMy4gR1VUOiBLb21wbGV4ZXIgSVQtTMO2c3VuZ3N2ZXJ0cmllYiAobMO2c3VuZ3NvcmllbnRpZXJ0LCBha3RpdiwgcmVhbGlzdGlzY2hlcyBHZWhhbHQpCgpFWFBMSVpJVCBWRVJNRUlERU46Ci0gSGVyc3RlbGxlciBhbHMgbGV0enRlciBKb2IgKFpzY2FsZXIsIENyb3dkU3RyaWtlLCBTZW50aW5lbE9uZSwgUGFsbyBBbHRvLCBGb3J0aW5ldCk6IEdlaGFsdCBpbmtvbXBhdGliZWwsIEFyYmVpdHN3ZWlzZSBmYWxzY2gKLSBTZW5pb3IvRW50ZXJwcmlzZS9TdHJhdGVnaWMvR2xvYmFsL05hbWVkIEFjY291bnQgTWFuYWdlcjogenUgc3RyYXRlZ2lzY2gsIHp1IHRldWVyCi0gS2VpbiBJVC1CYWNrZ3JvdW5kCgpSRUQgRkxBR1M6ICJFbnRlcnByaXNlIiwgIlN0cmF0ZWdpYyIsICJOYW1lZCBBY2NvdW50cyIsICJHbG9iYWwiLCAiRXhlY3V0aXZlIFNwb25zb3JzaGlwIiwgSGVyc3RlbGxlciBhbHMgQXJiZWl0Z2ViZXIKR1JFRU4gRkxBR1M6IFJlc2VsbGVyLUVyZmFocnVuZywgRGlzdHJpYnV0aW9uLCBLYWx0YWtxdWlzZSwgUXVvdGEgMTAwJSssIE91dGJvdW5kLCBIdW50ZXItTWVudGFsaXTDpHQKCktBUlJJRVJFLUxPR0lLOiBDWVFVRU8gPSBTY2hyaXR0IHZvbiBSZXNlbGxlci9EaXN0cmlidXRpb24gWlUgU2VjdXJpdHktU3BlemlhbGlzdC4gTmljaHQgdm9uIEhlcnN0ZWxsZXJuLgoKT0ZGRU5FIFNURUxMRU46IEFjY291bnQgTWFuYWdlciBJVCBTZWN1cml0eSAoNzAtOTBrK1Byb3Zpc2lvbiwgSHVudGVyKSwgVmVydHJpZWJzaW5uZW5kaWVuc3QgKDQ1LTYwaytCb251cykKUE9SVEZPTElPOiBac2NhbGVyLCBDcm93ZFN0cmlrZSwgU2VudGluZWxPbmUsIFByb29mcG9pbnQsIEFyY3RpYyBXb2xmLCBNaW1lY2FzdCwgQ2xvdWRmbGFyZSwgTmV0c2tvcGUsIEtub3dCZTQsIFJhcGlkNywgVmVjdHJhIEFJLCBDQVRPIHUuYS4KS1VOREVOOiBTaWx0cm9uaWMsIENvbXB1R3JvdXAsIEFtZXIgU3BvcnRzLCBNaWx0ZW55aSBCaW90ZWMsIEJheWVybiBJbnZlc3QgdS5hLgoKQW50d29ydGUgYXVmIERldXRzY2gsIHByw6R6aXNlLCBtYXggMjUwIFfDtnJ0ZXIuIEJlaSBFLU1haWxzOiBCZXRyZWZmICsgdm9sbHN0w6RuZGlnZXIgVGV4dC4gQmVpIENWLVNjcmVlbmluZzog4pyFIEVJTkxBREVOIG9kZXIg8J+aqSBBQkxFSE5FTiBhbHMgZXJzdGUgWmVpbGUgKyBCZWdyw7xuZHVuZy5gOwoKLy8g4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQCi8vIE5BVklHQVRJT04KLy8g4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQCmNvbnN0IFZJRVdTID0gewogIHBpcGVsaW5lOidLYW5kaWRhdGVuIFBpcGVsaW5lIMK3IERldXRzY2hsYW5kJywKICBqb2JzOidTdGVsbGVudmVyd2FsdHVuZyAmIFVwbG9hZCcsCiAgc291cmNpbmc6J0thbmRpZGF0ZW4gU291cmNpbmcnLAogIGVtYWlsOidFLU1haWwgU2VxdWVuemVuJywKICBpbmJveDonQW50d29ydGVuIChJbmJveCknLAogIGNhbGVuZGFyOidHb29nbGUgQ2FsZW5kYXInLAogIGludGVsbGlnZW5jZTonQ29tcGFueSBJbnRlbGxpZ2VuY2UgwrcgQ1lRVUVPJywKICBzZXR1cDonR21haWwgU2V0dXAnCn07CgpmdW5jdGlvbiBzaG93VmlldyhuYW1lKSB7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgnLnZpZXcnKS5mb3JFYWNoKHYgPT4gdi5jbGFzc0xpc3QucmVtb3ZlKCdhY3RpdmUnKSk7CiAgY29uc3QgZWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndmlldy0nK25hbWUpOwogIGlmIChlbCkgZWwuY2xhc3NMaXN0LmFkZCgnYWN0aXZlJyk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3ZpZXctdGl0bGUnKS50ZXh0Q29udGVudCA9IFZJRVdTW25hbWVdIHx8IG5hbWU7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgnLnNiLWl0ZW0nKS5mb3JFYWNoKGl0ZW0gPT4gewogICAgY29uc3QgdCA9IGl0ZW0udGV4dENvbnRlbnQudG9Mb3dlckNhc2UoKTsKICAgIGl0ZW0uY2xhc3NMaXN0LnRvZ2dsZSgnYWN0aXZlJywKICAgICAgKG5hbWU9PT0ncGlwZWxpbmUnICYmIHQuaW5jbHVkZXMoJ3BpcGVsaW5lJykpIHx8CiAgICAgIChuYW1lPT09J2pvYnMnICYmIHQuaW5jbHVkZXMoJ3N0ZWxsZW4nKSkgfHwKICAgICAgKG5hbWU9PT0nc291cmNpbmcnICYmIHQuaW5jbHVkZXMoJ3NvdXJjJykpIHx8CiAgICAgIChuYW1lPT09J2VtYWlsJyAmJiB0LmluY2x1ZGVzKCdzZXF1ZW56JykpIHx8CiAgICAgIChuYW1lPT09J2luYm94JyAmJiB0LmluY2x1ZGVzKCdhbnR3b3J0JykpIHx8CiAgICAgIChuYW1lPT09J2NhbGVuZGFyJyAmJiB0LmluY2x1ZGVzKCdrYWxlbmRlcicpKSB8fAogICAgICAobmFtZT09PSdpbnRlbGxpZ2VuY2UnICYmIHQuaW5jbHVkZXMoJ2ludGVsJykpIHx8CiAgICAgIChuYW1lPT09J3NldHVwJyAmJiB0LmluY2x1ZGVzKCdzZXR1cCcpKQogICAgKTsKICB9KTsKfQoKZnVuY3Rpb24gZmlsdGVySm9iKGVsLCBqb2IpIHsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCcudGItam9iLXBpbGwnKS5mb3JFYWNoKHAgPT4gcC5jbGFzc0xpc3QucmVtb3ZlKCdhY3RpdmUnKSk7CiAgZWwuY2xhc3NMaXN0LmFkZCgnYWN0aXZlJyk7CiAgcnVuQUkoam9iPT09J2FtJwogICAgPyAnWmVpZ2UgUGlwZWxpbmUtw5xiZXJibGljayBmw7xyIEFjY291bnQgTWFuYWdlciBiZWkgQ1lRVUVPOiB3ZXIgaXN0IHdvLCB3YXMgc2luZCBkaWUgbsOkY2hzdGVuIFNjaHJpdHRlPycKICAgIDogJ1plaWdlIFBpcGVsaW5lLcOcYmVyYmxpY2sgZsO8ciBWZXJ0cmllYnNpbm5lbmRpZW5zdCBiZWkgQ1lRVUVPOiB3ZXIgaXN0IHdvLCB3YXMgc2luZCBkaWUgbsOkY2hzdGVuIFNjaHJpdHRlPycpOwp9CgovLyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAKLy8gSk9CIFVQTE9BRAovLyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAKZnVuY3Rpb24gaGFuZGxlRHJvcChlKSB7CiAgZS5wcmV2ZW50RGVmYXVsdCgpOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd1cGxvYWQtem9uZScpLmNsYXNzTGlzdC5yZW1vdmUoJ2RyYWctb3ZlcicpOwogIGNvbnN0IGZpbGUgPSBlLmRhdGFUcmFuc2Zlci5maWxlc1swXTsKICBpZiAoZmlsZSkgcHJvY2Vzc0ZpbGUoZmlsZSk7Cn0KCmZ1bmN0aW9uIGhhbmRsZUZpbGVTZWxlY3QoZSkgewogIGNvbnN0IGZpbGUgPSBlLnRhcmdldC5maWxlc1swXTsKICBpZiAoZmlsZSkgcHJvY2Vzc0ZpbGUoZmlsZSk7Cn0KCmZ1bmN0aW9uIHByb2Nlc3NGaWxlKGZpbGUpIHsKICBjb25zdCBzdGF0dXMgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndXBsb2FkLXN0YXR1cycpOwogIGNvbnN0IG1zZyA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd1cGxvYWQtbXNnJyk7CiAgc3RhdHVzLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwogIG1zZy50ZXh0Q29udGVudCA9IGAiJHtmaWxlLm5hbWV9IiB3aXJkIGFuYWx5c2llcnQuLi5gOwogIGNvbnN0IHJlYWRlciA9IG5ldyBGaWxlUmVhZGVyKCk7CiAgcmVhZGVyLm9ubG9hZCA9IGZ1bmN0aW9uKGUpIHsKICAgIGNvbnN0IHRleHQgPSBlLnRhcmdldC5yZXN1bHQ7CiAgICBhbmFseXplSm9iVGV4dCh0ZXh0LCBmaWxlLm5hbWUpOwogIH07CiAgcmVhZGVyLnJlYWRBc1RleHQoZmlsZSk7Cn0KCmZ1bmN0aW9uIGFuYWx5emVNYW51YWxKb2IoKSB7CiAgY29uc3QgdGV4dCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtYW51YWwtam9iJykudmFsdWUudHJpbSgpOwogIGlmICghdGV4dCkgcmV0dXJuOwogIGNvbnN0IHN0YXR1cyA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd1cGxvYWQtc3RhdHVzJyk7CiAgc3RhdHVzLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd1cGxvYWQtbXNnJykudGV4dENvbnRlbnQgPSAnU3RlbGxlbmJlc2NocmVpYnVuZyB3aXJkIGFuYWx5c2llcnQuLi4nOwogIGFuYWx5emVKb2JUZXh0KHRleHQsICdNYW51ZWxsZSBFaW5nYWJlJyk7Cn0KCmFzeW5jIGZ1bmN0aW9uIGFuYWx5emVKb2JUZXh0KHRleHQsIGZpbGVuYW1lKSB7CiAgY29uc3QgcHJvbXB0ID0gYEFuYWx5c2llcmUgZGllc2UgU3RlbGxlbmJlc2NocmVpYnVuZyBmw7xyIENZUVVFTyB1bmQgZXh0cmFoaWVyZToKMS4gSm9idGl0ZWwgdW5kIEtlcm5hdWZnYWJlbgoyLiBQZmxpY2h0YW5mb3JkZXJ1bmdlbiAoTXVzdC1oYXZlKSDigJQgYmV6b2dlbiBhdWYgQ1lRVUVPIEt1bHR1cjogUmVzZWxsZXIvRGlzdHJpYnV0aW9uIEVyZmFocnVuZz8gSHVudGVyLU1lbnRhbGl0w6R0PwozLiBOaWNlLXRvLWhhdmUgS3JpdGVyaWVuCjQuIFJlZCBGbGFncyBkaWUgd2lyIE5JQ0hUIHdvbGxlbiAoSGVyc3RlbGxlci1CYWNrZ3JvdW5kLCBTZW5pb3IgRW50ZXJwcmlzZSBBTSkKNS4gR2VoYWx0c3JhaG1lbiBmYWxscyBlcmtlbm5iYXIKNi4gRW1wZm9obGVuZSBTb3VyY2luZy1RdWVsbGVuIGbDvHIgZGllc2UgU3RlbGxlCjcuIEJvb2xlYW4gU2VhcmNoIFN0cmluZyBmw7xyIExpbmtlZEluCgpTdGVsbGU6ICR7dGV4dC5zdWJzdHJpbmcoMCwgMjAwMCl9YDsKCiAgY29uc3QgcmVzdWx0ID0gYXdhaXQgY2FsbEFJKHByb21wdCk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3VwbG9hZC1tc2cnKS50ZXh0Q29udGVudCA9IGDinJMgIiR7ZmlsZW5hbWV9IiBhbmFseXNpZXJ0YDsKICBjb25zdCByZXFzID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4dHJhY3RlZC1yZXFzJyk7CiAgY29uc3QgY29udGVudCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdyZXFzLWNvbnRlbnQnKTsKICByZXFzLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwogIGNvbnRlbnQuaW5uZXJIVE1MID0gYDxkaXYgc3R5bGU9ImJhY2tncm91bmQ6I2Y4ZmFmYztib3JkZXI6MXB4IHNvbGlkICNlOGVlZjU7Ym9yZGVyLXJhZGl1czo4cHg7cGFkZGluZzoxMnB4O2ZvbnQtc2l6ZToxMXB4O2xpbmUtaGVpZ2h0OjEuNzttYXJnaW4tdG9wOjhweDtjb2xvcjojMWEyMzM1Ij4ke3Jlc3VsdC5yZXBsYWNlKC9cbi9nLCc8YnI+Jyl9PC9kaXY+YDsKfQoKZnVuY3Rpb24gY29uZmlybUpvYigpIHsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndXBsb2FkLW1zZycpLnRleHRDb250ZW50ID0gJ+KchSBTdGVsbGUgZ2VzcGVpY2hlcnQg4oCUIFNvdXJjaW5nIGthbm4gc3RhcnRlbic7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3VwbG9hZC1tc2cnKS5zdHlsZS5iYWNrZ3JvdW5kID0gJyNlYWY2ZWYnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd1cGxvYWQtbXNnJykuc3R5bGUuYm9yZGVyTGVmdENvbG9yID0gJyMyN2FlNjAnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd1cGxvYWQtbXNnJykuc3R5bGUuY29sb3IgPSAnIzFlODQ0OSc7CiAgc2V0VGltZW91dCgoKSA9PiBzaG93Vmlldygnc291cmNpbmcnKSwgMTUwMCk7Cn0KCi8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkAovLyBTT1VSQ0lORwovLyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAKZnVuY3Rpb24gZG9Tb3VyY2luZygpIHsKICBjb25zdCBqb2IgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3JjLWpvYicpLnZhbHVlOwogIGNvbnN0IHBsYXRmb3JtID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NyYy1wbGF0Zm9ybScpLnZhbHVlOwogIGNvbnN0IGt3ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NyYy1rZXl3b3JkcycpLnZhbHVlOwogIHJ1bkFJKGBFcnN0ZWxsZSBvcHRpbWllcnRlbiBCb29sZWFuIFNlYXJjaCBTdHJpbmcgZsO8ciAke3BsYXRmb3JtfSBmw7xyIENZUVVFTyBTdGVsbGU6ICIke2pvYn0iLiBGb2t1czogSVQtUmVzZWxsZXIgKEJlY2h0bGUsIENvbXB1dGFjZW50ZXIsIENhbmNvbSkgdW5kIERpc3RyaWJ1dGlvbiAoSW5ncmFtLCBBcnJvdywgQWxzbykuIEV4cGxpeml0IEhlcnN0ZWxsZXIgKENyb3dkU3RyaWtlLCBac2NhbGVyLCBQYWxvIEFsdG8pIHVuZCAiRW50ZXJwcmlzZS9TdHJhdGVnaWMgQWNjb3VudCIgYXVzc2NobGllw59lbi4gJHtrdyA/ICdadXNhdHotS2V5d29yZHM6ICcra3cgOiAnJ31gKTsKfQoKLy8g4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQCi8vIENWIFNDUkVFTkVSCi8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkApmdW5jdGlvbiBzY3JlZW5DVigpIHsKICBjb25zdCBjdiA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdjdi1pbnB1dCcpLnZhbHVlLnRyaW0oKTsKICBpZiAoIWN2KSByZXR1cm47CiAgY29uc3QgcmVzdWx0ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NjcmVlbi1yZXN1bHQnKTsKICByZXN1bHQuY2xhc3NOYW1lID0gJ2N2LXJlc3VsdCBzaG93JzsKICByZXN1bHQuaW5uZXJIVE1MID0gJzxlbSBzdHlsZT0iY29sb3I6IzdhOGZhNiI+QW5hbHlzaWVyZSBQcm9maWwgYXVmIENZUVVFTy1QYXNzdW5nLi4uPC9lbT4nOwoKICBjb25zdCBwcm9tcHQgPSBgQW5hbHlzaWVyZSBkaWVzZXMgS2FuZGlkYXRlbnByb2ZpbCBmw7xyIENZUVVFTy4gRXJzdGUgWmVpbGUgTVVTUyBzZWluOiAi4pyFIEVJTkxBREVOIiBvZGVyICLwn5qpIEFCTEVITkVOIi4KClByw7xmZTogMSkgSGVyc3RlbGxlciBhbHMgbGV0enRlciBKb2I/IDIpIEVudGVycHJpc2UvU3RyYXRlZ2ljL0dsb2JhbCBBRT8gMykgS2VpbiBJVC1CYWNrZ3JvdW5kPyA0KSBSZXNlbGxlci9EaXN0cmlidXRpb24gRXJmYWhydW5nPyA1KSBIdW50ZXItTWVudGFsaXTDpHQ/IDYpIEdlaGFsdHNlcndhcnR1bmcga29tcGF0aWJlbD8KClByb2ZpbDpcbiR7Y3Z9YDsKCiAgY2FsbEFJKHByb21wdCkudGhlbih0ZXh0ID0+IHsKICAgIHJlc3VsdC5pbm5lckhUTUwgPSB0ZXh0LnJlcGxhY2UoL1xuL2csJzxicj4nKTsKICAgIGNvbnN0IGdvb2QgPSB0ZXh0LnN0YXJ0c1dpdGgoJ+KchScpOwogICAgcmVzdWx0LnN0eWxlLmJvcmRlckxlZnQgPSBgM3B4IHNvbGlkICR7Z29vZD8nIzI3YWU2MCc6JyNjMDM5MmInfWA7CiAgICByZXN1bHQuc3R5bGUuYmFja2dyb3VuZCA9IGdvb2QgPyAnI2Y2ZmRmOScgOiAnI2ZmZjVmNSc7CiAgfSk7Cn0KCi8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkAovLyBHTUFJTCBTRVRVUAovLyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAKZnVuY3Rpb24gc2F2ZUNyZWRlbnRpYWxzKCkgewogIGNvbnN0IGlkID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2NsaWVudC1pZCcpLnZhbHVlLnRyaW0oKTsKICBjb25zdCBzZWMgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnY2xpZW50LXNlY3JldCcpLnZhbHVlLnRyaW0oKTsKICBpZiAoIWlkIHx8ICFzZWMpIHsgcnVuQUkoJ1dhcyBtdXNzIGljaCBpbiBDbGllbnQgSUQgdW5kIENsaWVudCBTZWNyZXQgZWludHJhZ2VuIGJlaW0gR29vZ2xlIE9BdXRoIFNldHVwIGbDvHIgR21haWwgQVBJPycpOyByZXR1cm47IH0KICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RlcDInKS5jbGFzc05hbWUgPSAnc2V0dXAtc3RlcCBkb25lJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RlcDMnKS5jbGFzc05hbWUgPSAnc2V0dXAtc3RlcCBhY3RpdmUtc3RlcCc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3MzbicpLmNsYXNzTmFtZSA9ICdzdGVwLW51bSBzbi1hY3RpdmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdGVwMy1ib2R5Jykuc3R5bGUuZGlzcGxheSA9ICdibG9jayc7CiAgcnVuQUkoJ09BdXRoIENyZWRlbnRpYWxzIGbDvHIgQ292ZW5leCBSZWNydWl0ZXIgZ2VzcGVpY2hlcnQuIEVya2zDpHJlIGRlbiBHb29nbGUgT0F1dGggTG9naW4gUHJvemVzcyBpbiBTY2hyaXR0IDMg4oCUIHdhcyBwYXNzaWVydCBiZWltICJNaXQgR29vZ2xlIHZlcmJpbmRlbiI/Jyk7Cn0KCmZ1bmN0aW9uIGNvbm5lY3RHbWFpbCgpIHsKICBjb25zdCBlbWFpbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzZW5kZXItZW1haWwnKS52YWx1ZSB8fCAncmVjcnVpdGluZ0BjeXF1ZW8uY29tJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RlcDMnKS5jbGFzc05hbWUgPSAnc2V0dXAtc3RlcCBkb25lJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RlcDQnKS5jbGFzc05hbWUgPSAnc2V0dXAtc3RlcCBhY3RpdmUtc3RlcCc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3M0bicpLmNsYXNzTmFtZSA9ICdzdGVwLW51bSBzbi1hY3RpdmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdGVwNC1ib2R5Jykuc3R5bGUuZGlzcGxheSA9ICdibG9jayc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2dtYWlsLWRvdCcpLnN0eWxlLmJhY2tncm91bmQgPSAnIzI3YWU2MCc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2dtYWlsLWxhYmVsJykudGV4dENvbnRlbnQgPSBlbWFpbDsKICBydW5BSSgnR21haWwgJytlbWFpbCsnIHd1cmRlIG1pdCBDb3ZlbmV4IFJlY3J1aXRlciB2ZXJidW5kZW4uIFdhcyBzaW5kIGRpZSB3aWNodGlnc3RlbiBFaW5zdGVsbHVuZ2VuIGbDvHIgYXV0b21hdGlzY2hlIEZvbGxvdy11cCBTZXF1ZW56ZW4/Jyk7Cn0KCmZ1bmN0aW9uIGZpbmlzaFNldHVwKCkgewogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdGVwNCcpLmNsYXNzTmFtZSA9ICdzZXR1cC1zdGVwIGRvbmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzZXR1cC1iYWRnZScpLnRleHRDb250ZW50ID0gJ+Kckyc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NldHVwLWJhZGdlJykuY2xhc3NOYW1lID0gJ3NiLWJhZGdlIHNiLWJhZGdlLWdyZWVuJzsKICBydW5BSSgnU2V0dXAgYWJnZXNjaGxvc3NlbiEgQ292ZW5leCBSZWNydWl0ZXIgaXN0IGJlcmVpdCBmw7xyIENZUVVFTyBSZWNydWl0aW5nLiBXYXMgc2luZCBkaWUgZXJzdGVuIGtvbmtyZXRlbiBTY2hyaXR0ZSB1bSBoZXV0ZSBtaXQgZGVtIFNvdXJjaW5nIHp1IHN0YXJ0ZW4/Jyk7CiAgc2V0VGltZW91dCgoKSA9PiBzaG93VmlldygncGlwZWxpbmUnKSwgMjAwMCk7Cn0KCi8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkAovLyDilZTilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZcKLy8g4pWRICBBTlRIUk9QSUMgQVBJIEtFWSDigJQgSElFUiBFSU5UUkFHRU4gICAgICAgICAgICAgIOKVkQovLyDilZEgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICDilZEKLy8g4pWRICAxLiBHZWhlIGF1ZjogY29uc29sZS5hbnRocm9waWMuY29tICAgICAgICAgICAgICAg4pWRCi8vIOKVkSAgMi4gUmVnaXN0cmllcmVuIC8gRWlubG9nZ2VuICAgICAgICAgICAgICAgICAgICAgIOKVkQovLyDilZEgIDMuICJBUEkgS2V5cyIg4oaSICJDcmVhdGUgS2V5IiAgICAgICAgICAgICAgICAgICAgIOKVkQovLyDilZEgIDQuIERlbiBLZXkgdW50ZW4gendpc2NoZW4gZGllIEFuZsO8aHJ1bmdzemVpY2hlbiAg4pWRCi8vIOKVkSAgICAgZWludHJhZ2VuIChmw6RuZ3QgbWl0IHNrLWFudC0uLi4gYW4pICAgICAgICAgICDilZEKLy8g4pWRICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAg4pWRCi8vIOKVkSAgS29zdGVuOiBjYS4gMCw14oCTMiBDZW50IHBybyBLSS1BbnR3b3J0ICAgICAgICAgICDilZEKLy8g4pWRICB+MzDigqwvTW9uYXQgYmVpIG5vcm1hbGVtIEdlYnJhdWNoICAgICAgICAgICAgICAgICDilZEKLy8g4pWa4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWdCmNvbnN0IEFOVEhST1BJQ19BUElfS0VZID0gIkhJRVJfQVBJX0tFWV9FSU5UUkFHRU4iOwovLyBCZWlzcGllbDogY29uc3QgQU5USFJPUElDX0FQSV9LRVkgPSAic2stYW50LWFwaTAzLWFiYzEyMy4uLiI7Ci8vIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkAoKLy8g4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQCi8vIEtJIEVOR0lORQovLyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAKYXN5bmMgZnVuY3Rpb24gY2FsbEFJKHByb21wdCkgewogIC8vIEFQSSBLZXkgUHLDvGZ1bmcKICBpZiAoIUFOVEhST1BJQ19BUElfS0VZIHx8IEFOVEhST1BJQ19BUElfS0VZID09PSAiSElFUl9BUElfS0VZX0VJTlRSQUdFTiIpIHsKICAgIGNvbnN0IGNoYXQgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYWktY2hhdCcpOwogICAgaWYgKGNoYXQpIHsKICAgICAgY29uc3Qgd2FybiA9IGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2RpdicpOwogICAgICB3YXJuLmNsYXNzTmFtZSA9ICdhbSc7CiAgICAgIHdhcm4uc3R5bGUuYmFja2dyb3VuZCA9ICcjZmZmOGVlJzsKICAgICAgd2Fybi5zdHlsZS5ib3JkZXJMZWZ0ID0gJzNweCBzb2xpZCAjZTY3ZTIyJzsKICAgICAgd2Fybi5zdHlsZS5jb2xvciA9ICcjZDM1NDAwJzsKICAgICAgd2Fybi5pbm5lckhUTUwgPSAn4pqg77iPIDxzdHJvbmc+QVBJIEtleSBmZWhsdCE8L3N0cm9uZz4gQml0dGUgw7ZmZm5lIGRpZSBIVE1MLURhdGVpIGluIGVpbmVtIFRleHRlZGl0b3IgdW5kIHRyYWdlIGRlaW5lbiBBbnRocm9waWMgQVBJIEtleSBlaW4gKFplaWxlIG1pdCBBTlRIUk9QSUNfQVBJX0tFWSkuIEFubGVpdHVuZzogY29uc29sZS5hbnRocm9waWMuY29tIOKGkiBBUEkgS2V5cyDihpIgQ3JlYXRlIEtleS4nOwogICAgICBjaGF0LmFwcGVuZENoaWxkKHdhcm4pOwogICAgICBjaGF0LnNjcm9sbFRvcCA9IGNoYXQuc2Nyb2xsSGVpZ2h0OwogICAgfQogICAgcmV0dXJuICfimqDvuI8gQml0dGUgenVlcnN0IGRlbiBBbnRocm9waWMgQVBJIEtleSBpbiBkZXIgRGF0ZWkgZWludHJhZ2VuLic7CiAgfQogIHRyeSB7CiAgICBjb25zdCByID0gYXdhaXQgZmV0Y2goImh0dHBzOi8vYXBpLmFudGhyb3BpYy5jb20vdjEvbWVzc2FnZXMiLCB7CiAgICAgIG1ldGhvZDoiUE9TVCIsCiAgICAgIGhlYWRlcnM6ewogICAgICAgICJDb250ZW50LVR5cGUiOiJhcHBsaWNhdGlvbi9qc29uIiwKICAgICAgICAieC1hcGkta2V5IjogQU5USFJPUElDX0FQSV9LRVksCiAgICAgICAgImFudGhyb3BpYy12ZXJzaW9uIjogIjIwMjMtMDYtMDEiLAogICAgICAgICJhbnRocm9waWMtZGFuZ2Vyb3VzLWRpcmVjdC1icm93c2VyLWFjY2VzcyI6ICJ0cnVlIgogICAgICB9LAogICAgICBib2R5OiBKU09OLnN0cmluZ2lmeSh7bW9kZWw6ImNsYXVkZS1zb25uZXQtNC0yMDI1MDUxNCIsbWF4X3Rva2Vuczo2MDAsc3lzdGVtOkNZUVVFT19DVFgsbWVzc2FnZXM6W3tyb2xlOiJ1c2VyIixjb250ZW50OnByb21wdH1dfSkKICAgIH0pOwogICAgaWYgKCFyLm9rKSB7CiAgICAgIGNvbnN0IGVyciA9IGF3YWl0IHIuanNvbigpLmNhdGNoKCgpPT4oe30pKTsKICAgICAgaWYgKHIuc3RhdHVzID09PSA0MDEpIHJldHVybiAn4p2MIEFQSSBLZXkgdW5nw7xsdGlnLiBCaXR0ZSBwcsO8ZmUgZGVuIEtleSBhdWYgY29uc29sZS5hbnRocm9waWMuY29tLic7CiAgICAgIGlmIChyLnN0YXR1cyA9PT0gNDI5KSByZXR1cm4gJ+KPsyBadSB2aWVsZSBBbmZyYWdlbiDigJQgYml0dGUga3VyeiB3YXJ0ZW4gdW5kIGVybmV1dCB2ZXJzdWNoZW4uJzsKICAgICAgcmV0dXJuIGBGZWhsZXIgJHtyLnN0YXR1c306ICR7ZXJyLmVycm9yPy5tZXNzYWdlIHx8ICdVbmJla2FubnRlciBGZWhsZXInfWA7CiAgICB9CiAgICBjb25zdCBkID0gYXdhaXQgci5qc29uKCk7CiAgICByZXR1cm4gZC5jb250ZW50Py5tYXAoYj0+Yi50ZXh0fHwnJykuam9pbignJykgfHwgJ0xlZXJlIEFudHdvcnQuJzsKICB9IGNhdGNoKGUpIHsKICAgIHJldHVybiAnVmVyYmluZHVuZ3NmZWhsZXIg4oCUIEludGVybmV0dmVyYmluZHVuZyBwcsO8ZmVuIHVuZCBlcm5ldXQgdmVyc3VjaGVuLic7CiAgfQp9Cgphc3luYyBmdW5jdGlvbiBydW5BSShwcm9tcHQpIHsKICBjb25zdCBjaGF0ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2FpLWNoYXQnKTsKICBjb25zdCB1ID0gZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgnZGl2Jyk7IHUuY2xhc3NOYW1lPSdhbSB1JzsKICB1LnRleHRDb250ZW50ID0gcHJvbXB0Lmxlbmd0aD43MCA/IHByb21wdC5zdWJzdHJpbmcoMCw2NykrJy4uLicgOiBwcm9tcHQ7CiAgY2hhdC5hcHBlbmRDaGlsZCh1KTsKICBjb25zdCBhID0gZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgnZGl2Jyk7IGEuY2xhc3NOYW1lPSdhbSBsJzsgYS50ZXh0Q29udGVudD0nLi4uJzsKICBjaGF0LmFwcGVuZENoaWxkKGEpOyBjaGF0LnNjcm9sbFRvcD1jaGF0LnNjcm9sbEhlaWdodDsKICBjb25zdCByZXN1bHQgPSBhd2FpdCBjYWxsQUkocHJvbXB0KTsKICBhLnRleHRDb250ZW50ID0gcmVzdWx0OyBhLmNsYXNzTmFtZT0nYW0nOwogIGNoYXQuc2Nyb2xsVG9wID0gY2hhdC5zY3JvbGxIZWlnaHQ7Cn0KCmZ1bmN0aW9uIHNlbmRJbnB1dCgpIHsKICBjb25zdCBlbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdhaS1pbnAnKTsKICBjb25zdCB2ID0gZWwudmFsdWUudHJpbSgpOyBpZiAoIXYpIHJldHVybjsKICBlbC52YWx1ZT0nJzsgcnVuQUkodik7Cn0KCi8vIOKUgOKUgCBBUEkgS0VZIENIRUNLIE9OIExPQUQg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACndpbmRvdy5hZGRFdmVudExpc3RlbmVyKCdET01Db250ZW50TG9hZGVkJywgKCkgPT4gewogIGlmICghQU5USFJPUElDX0FQSV9LRVkgfHwgQU5USFJPUElDX0FQSV9LRVkgPT09ICJISUVSX0FQSV9LRVlfRUlOVFJBR0VOIikgewogICAgY29uc3QgYmFubmVyID0gZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgnZGl2Jyk7CiAgICBiYW5uZXIuaWQgPSAnYXBpLWJhbm5lcic7CiAgICBiYW5uZXIuc3R5bGUuY3NzVGV4dCA9IFsKICAgICAgJ3Bvc2l0aW9uOmZpeGVkJywndG9wOjAnLCdsZWZ0OjAnLCdyaWdodDowJywnei1pbmRleDo5OTk5JywKICAgICAgJ2JhY2tncm91bmQ6I2ZmZjNjZCcsJ2JvcmRlci1ib3R0b206MnB4IHNvbGlkICNlMGE4MDAnLAogICAgICAncGFkZGluZzoxMHB4IDIwcHgnLCdkaXNwbGF5OmZsZXgnLCdhbGlnbi1pdGVtczpjZW50ZXInLCdnYXA6MTJweCcsCiAgICAgICdmb250LXNpemU6MTJweCcsJ2NvbG9yOiM4NTY0MDQnLAogICAgICAnZm9udC1mYW1pbHk6IkRNIFNhbnMiLHN5c3RlbS11aSxzYW5zLXNlcmlmJwogICAgXS5qb2luKCc7Jyk7CiAgICBiYW5uZXIuaW5uZXJIVE1MID0gWwogICAgICAnPHNwYW4gc3R5bGU9ImZvbnQtc2l6ZToxOHB4Ij4mIzk4ODg7JiM2NTAzOTs8L3NwYW4+JywKICAgICAgJzxzcGFuPicsCiAgICAgICAgJzxzdHJvbmc+QVBJIEtleSBmZWhsdCAmbWRhc2g7PC9zdHJvbmc+ICcsCiAgICAgICAgJ0RhdGVpIGluIFRleHRlZGl0b3IgJiMyNDY7ZmZuZW4gKFZTIENvZGUgLyBOb3RlcGFkKyspIHVuZCBuYWNoICcsCiAgICAgICAgJzxjb2RlIHN0eWxlPSJiYWNrZ3JvdW5kOiNmZmVhYTc7cGFkZGluZzoxcHggNXB4O2JvcmRlci1yYWRpdXM6NHB4Ij5ISUVSX0FQSV9LRVlfRUlOVFJBR0VOPC9jb2RlPiAnLAogICAgICAgICdzdWNoZW4uIEtleSB2b24gJywKICAgICAgICAnPGEgaHJlZj0iaHR0cHM6Ly9jb25zb2xlLmFudGhyb3BpYy5jb20iIHRhcmdldD0iX2JsYW5rIiAnLAogICAgICAgICdzdHlsZT0iY29sb3I6Izg1NjQwNDtmb250LXdlaWdodDo2MDAiPmNvbnNvbGUuYW50aHJvcGljLmNvbTwvYT4gJywKICAgICAgICAnZWludHJhZ2VuIChiZWdpbm50IG1pdCBzay1hbnQtLi4uKS4nLAogICAgICAnPC9zcGFuPicsCiAgICAgICc8YnV0dG9uIG9uY2xpY2s9ImRvY3VtZW50LmdldEVsZW1lbnRCeUlkKFwnYXBpLWJhbm5lclwnKS5zdHlsZS5kaXNwbGF5PVwnbm9uZVwnOycsCiAgICAgICAgJ2RvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoXCcuYXBwLXNoZWxsXCcpLnN0eWxlLm1hcmdpblRvcD1cJzBcJyIgJywKICAgICAgICAnc3R5bGU9Im1hcmdpbi1sZWZ0OmF1dG87YmFja2dyb3VuZDpub25lO2JvcmRlcjoxcHggc29saWQgIzg1NjQwNDsnLAogICAgICAgICdib3JkZXItcmFkaXVzOjZweDtwYWRkaW5nOjNweCAxMHB4O2N1cnNvcjpwb2ludGVyO2NvbG9yOiM4NTY0MDQ7Zm9udC1zaXplOjExcHgiPicsCiAgICAgICAgJ1ZlcnN0YW5kZW4nLAogICAgICAnPC9idXR0b24+JwogICAgXS5qb2luKCcnKTsKICAgIGRvY3VtZW50LmJvZHkucHJlcGVuZChiYW5uZXIpOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvcignLmFwcC1zaGVsbCcpLnN0eWxlLm1hcmdpblRvcCA9ICc1MHB4JzsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJy5hcHAtc2hlbGwnKS5zdHlsZS5oZWlnaHQgPSAnY2FsYygxMDB2aCAtIDUwcHgpJzsKICB9Cn0pOwo8L3NjcmlwdD4KPC9ib2R5Pgo8L2h0bWw+Cg==").decode("utf-8")

@app.route("/app", methods=["GET"])
def recruiter_app():
    """Liefert die vollständige Covenex Recruiter App aus."""
    return RECRUITER_APP_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

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
