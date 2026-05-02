# COVENEX RECRUITER AGENT
## Setup-Anleitung für den IT-Kollegen

---

## Was dieser Agent tut

Jeden Morgen um 07:30 Uhr startet der Agent automatisch und:
- ✉ Prüft Gmail auf neue Kandidaten-Antworten
- 🤖 Beantwortet Antworten automatisch mit KI
- 📅 Bucht Termine in Google Calendar wenn Kandidat zusagt
- 🔄 Sendet fällige Follow-ups (Tag 5, 10, 15)
- 📊 Schickt dir einen Tages-Report per E-Mail

---

## Schritt 1: Repository auf GitHub hochladen (5 Min)

1. GitHub Account anlegen auf github.com (falls nicht vorhanden)
2. Neues Repository erstellen: "covenex-agent"
3. Diese Dateien hochladen:
   - `agent.py`
   - `requirements.txt`
   - `railway.toml`

---

## Schritt 2: Railway Account anlegen (2 Min)

1. Gehe auf railway.app
2. "Login with GitHub" klicken
3. Kostenloses Konto erstellen

---

## Schritt 3: Projekt deployen (5 Min)

1. Railway Dashboard → "New Project"
2. "Deploy from GitHub repo" → covenex-agent auswählen
3. Railway erkennt automatisch Python und installiert alles
4. Warten bis "✓ Deployed" erscheint

---

## Schritt 4: Umgebungsvariablen eintragen (10 Min)

In Railway → dein Projekt → "Variables" → diese Werte eintragen:

```
ANTHROPIC_API_KEY    = sk-ant-api03-...          (von console.anthropic.com)
GMAIL_CLIENT_ID      = xxxx.apps.googleusercontent.com
GMAIL_CLIENT_SECRET  = GOCSPX-...
GMAIL_SENDER_EMAIL   = recruiting@cyqueo.com
GMAIL_SENDER_NAME    = CYQUEO Recruiting
REPORT_EMAIL         = deine-email@cyqueo.com    (wohin der Tages-Report geht)
DAILY_RUN_TIME       = 07:30                     (Uhrzeit für täglichen Lauf)
MAX_EMAILS_PER_DAY   = 30                        (Sicherheitslimit)
FOLLOWUP_1_DAYS      = 5
FOLLOWUP_2_DAYS      = 10
FOLLOWUP_3_DAYS      = 15
PORT                 = 8080
```

---

## Schritt 5: Google OAuth einrichten (15 Min)

### 5a. Google Cloud Console
1. console.cloud.google.com → Projekt "Covenex-Recruiting" (bereits aus Gmail-Setup)
2. APIs & Services → Credentials
3. "Create Credentials" → OAuth 2.0 Client ID
4. Application Type: "Web Application"
5. Authorized redirect URIs: `https://deine-railway-url.railway.app/oauth/callback`
6. Client ID + Secret notieren → in Railway als GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET eintragen

### 5b. Erste Authentifizierung
1. Railway URL aufrufen: `https://deine-railway-url.railway.app/health`
2. Wenn "running" erscheint → Google OAuth abschließen via `/auth/start`

---

## Schritt 6: Ersten Test starten (2 Min)

1. Railway URL aufrufen: `https://deine-railway-url.railway.app/run-now`
   (POST Request via curl oder Postman)
2. Oder: einfach warten bis 07:30 Uhr — dann läuft er automatisch
3. Du bekommst eine Test-E-Mail an deine REPORT_EMAIL

---

## API Endpunkte (für das Frontend)

| Endpunkt | Methode | Was es tut |
|----------|---------|------------|
| `/health` | GET | Prüft ob Agent läuft |
| `/status` | GET | Zeigt Statistiken |
| `/run-now` | POST | Startet Agent sofort |
| `/candidates` | GET | Alle Kandidaten |
| `/candidates` | POST | Kandidat hinzufügen |
| `/ask` | POST | Direkte KI-Anfrage |

---

## Kosten

| Service | Kosten |
|---------|--------|
| Railway (Hobby Plan) | ~5€/Monat |
| Anthropic API | ~0,01–0,05€ pro Agent-Lauf |
| Google APIs | Kostenlos |
| **Gesamt** | **~6–10€/Monat** |

---

## Problembehebung

**Agent läuft nicht:**
→ Railway Dashboard → Logs prüfen
→ Alle Environment Variables gesetzt?

**Gmail-Fehler:**
→ Google Cloud Console → OAuth Consent Screen → App verifiziert?
→ Token abgelaufen → `/auth/refresh` aufrufen

**KI-Fehler:**
→ Anthropic API Key korrekt?
→ Guthaben auf console.anthropic.com prüfen

---

## Sicherheitshinweise

- API Keys NIEMALS in Code eintragen — immer als Railway Environment Variable
- `token.pickle` enthält Google-Zugangsdaten — nicht in GitHub hochladen
- `.gitignore` Datei erstellen mit: `token.pickle`, `*.log`, `candidates.json`

---

Fragen? support@cyqueo.com
