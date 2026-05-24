# Prompt Hacker

Interaktives Lernspiel und Arbeitsblatt zu **Prompts, LLM-Sicherheit und Prompt-Injection**
für Schülerinnen und Schüler der Klassen 8-10 an der Gesamtschule Meiderich.

## Inhalt

Die App verbindet die übliche GSM-Vollversion mit zwei Lernphasen:

1. **Spielphase:** Drei KI-Wächter schützen je ein harmloses Geheimnis. Die Lernenden
   probieren aus, welche Prompt-Injection-Techniken funktionieren. Pro Schüler:in und
   Level gibt es serverseitig 5 Versuche; Zusatzversuche kann die Lehrkraft freigeben.
2. **Arbeitsblattphase:** Ein Infotext erklärt Prompts, Systemprompts, LLMs,
   Prompt-Injection, indirekte Injection, Jailbreaks, Encoding-Angriffe und wichtige
   Schutzmaßnahmen. Danach folgen fünf Aufgabenreiter mit Niveau A/B/C.
3. **KI-Tutor:** Der Chat erscheint nur im Arbeitsblattbereich. Jede Frage geht zuerst
   ins Lehrer-Dashboard und wird dort freigegeben oder abgelehnt.

## Die drei Level

| Level | Name | Schwierigkeit | Lernziel |
| --- | --- | --- | --- |
| 1 | Bot Bert | Einfach | Systemprompt ist keine echte Sicherheitsbarriere |
| 2 | Wächterin Wiebke | Mittel | Rollenspiel-Jailbreak und Kontext-Umlenkung |
| 3 | Tresor-Theo | Schwer | Verschleierung, Encoding und Ausgabeprüfung |

## Arbeitsblatt-Reiter

- **Infotext:** Grundlagen zu Prompts, LLM-Sicherheit und Injection-Techniken
- **Aufgabe 1:** Grundbegriffe sichern
- **Aufgabe 2:** Injection-Techniken analysieren
- **Aufgabe 3:** Sicherheitsregeln entwickeln
- **Aufgabe 4:** Prompt reparieren
- **Reflexion:** Transfer nach dem Spiel

Jede Aufgabe hat die Niveaus **A**, **B** und **C**. Antworten werden serverseitig in
SQLite gespeichert, damit sie im Dashboard sichtbar bleiben.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Die App läuft danach auf <http://127.0.0.1:5000>.

Ohne `GEMINI_API_KEY` startet die App im Offline-Modus. Oberfläche, Dashboard,
Arbeitsblatt und Freigaben funktionieren trotzdem; KI-Antworten sind dann Platzhalter.

Standard-Lehrerpasswort aus `.env.example`: `gsm` im Entwicklungsmodus beziehungsweise
`LEHRER_PASSWORD` in `.env`.

## Projektstruktur

```text
prompt_hacker/
  app.py                  Flask-Backend, Gemini-Integration, Level-API
  data/                   SQLite-Datenbank zur Laufzeit
  levels.json             Level-Definitionen
  templates/login.html    Schülerlogin mit Datenschutz-/KI-Hinweis
  templates/dashboard.html Lehrer-Dashboard
  templates/index.html    Single-Page-App mit Spiel und Arbeitsblatt
  static/css/style.css    GSM-Design, iPad-first Layout
  static/js/main.js       Spiel-, Arbeitsblatt- und KI-Freigabelogik
  static/js/dashboard.js  Dashboard-Logik
  static/img/             Logos, Titelbild und Bot-Bilder
  requirements.txt
  .env.example
```

## Datenschutz und Sicherheit

Die App verwendet nur harmlose Beispiel-Geheimnisse. Lernende sollen keine echten
Namen, Passwörter, privaten Daten oder Informationen über andere Personen eingeben.
Wenn ein Gemini-Key gesetzt ist, werden Spielnachrichten an das KI-Modell gesendet.

Für echte KI-Anwendungen gilt: Geheimnisse gehören nicht in Prompts. Sichere Systeme
brauchen serverseitige Rechteprüfung, Tool-Freigaben, Ausgabefilter, Protokollierung
und Tests mit Angriffsprompts.
