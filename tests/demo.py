#!/usr/bin/env python3
"""Fictional demo data for screenshots and the README GIF: python3 tests/demo.py <empty folder>

Everything here is made up (Lena Beispiel, the companies, the mails). Then start a board on it with its own config folder:
  BEWERBUNGS_BOARD_CONFIG=$(mktemp -d) BEWERBUNGS_ROOT=<folder> HOME=$(mktemp -d) python3 server.py 8899
and run  node tests/screenshots.mjs 8899 chromium docs/screenshots
"""
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
TODAY = date.today()
d = lambda n: (TODAY + timedelta(days=n)).isoformat()


def letter(company, title, contact, reqs, body, link):
    mapping = "\n\n".join(f"**{r}**\n- {e}" for r, e in reqs)
    return f"""# Infos zur Jobausschreibung
**Titel:** {title}
**Firma:** {company}
**Kontakt:** {contact}
**Link:** [jobs.ch]({link})

## Aufgaben
- Neue Funktionen entwickeln und pflegen
- Mit Design und QA zusammenarbeiten

## Anforderungen
""" + "\n".join(f"- {r}" for r, _ in reqs) + f"""

# Requirements & Skills Mapping

{mapping}

# Bewerbungsschreiben

Lena Beispiel
Musterweg 1, 8000 Zürich
079 000 00 00, lena@example.com

{company}
Bahnhofstrasse 10
8000 Zürich

Zürich, {TODAY.day}. {["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"][TODAY.month - 1]} {TODAY.year}

**Bewerbung als {title}**

Sehr geehrte Damen und Herren

{body}

Freundliche Grüsse
Lena Beispiel
"""


APPS = [
    ("Pixelwerk Studios", "Junior Unity Developer", "Mara Muster, jobs@pixelwerk.example",
     [("Unity und C#", "Zwei Spielprojekte im Studium in Unity mit C#."), ("Gameplay-Programmierung", "Kampfsystem und KI-Gegner im Semesterprojekt."),
      ("Erfahrung mit Konsolen", "Lücke: bisher nur PC, im Brief nicht behaupten.")],
     "Mit Ihrem Studio verbinde ich Spiele, die liebevoll gebaut sind. In meinem letzten Semesterprojekt habe ich in Unity mit C# das Kampfsystem und die KI der Gegner entwickelt und dabei gelernt, Gameplay im Team schnell zu iterieren.\n\nIch freue mich darauf, diese Erfahrung bei Pixelwerk einzubringen und von Ihrem Team zu lernen.",
     "https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a001/", "offen", None),
    ("Alpenblick Software AG", "Software Engineer C#", "Tim Beispiel, tim@alpenblick.example",
     [("C# und .NET", "Desktop-Tool in C# und WPF für ein Kundenprojekt."), ("Saubere Architektur", "Schichtenaufbau und Tests im Bachelorprojekt.")],
     "Ihre Software für die Bauplanung zeigt, wie viel gute Werkzeuge im Alltag bewirken. In meinem Bachelorprojekt habe ich ein Desktop-Tool in C# und WPF gebaut, mit klarer Schichtung und automatischen Tests.\n\nGerne zeige ich Ihnen in einem Gespräch, wie ich arbeite.",
     "https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a002/", "final", None),
    ("Nordlicht Games GmbH", "Gameplay Programmer", "Sara Muster, sara@nordlicht.example",
     [("C++ oder C#", "C# täglich, C++ aus zwei Modulen."), ("Teamarbeit", "Lead Programmer in einem Team von sechs.")],
     "Als Lead Programmer eines sechsköpfigen Studienteams habe ich gelernt, Gameplay und Technik zusammenzubringen.",
     "https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a003/", "abgeschickt", "gespraech"),
    ("Seeblick IT AG", "IT Support Engineer", "HR Team, hr@seeblick.example",
     [("Windows-Clients", "Eigene Rechner und die der Familie betreut, Skripte in PowerShell.")],
     "Technik zu erklären macht mir Freude, seit ich Kindern im Code Camp das Programmieren beibringe.",
     "https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a004/", "abgeschickt", "abgeschickt"),
    ("Bergbach Interactive", "Tools Programmer", "Jonas Beispiel, jonas@bergbach.example",
     [("Editor-Tools in Unity", "Leveleditor als Unity-Erweiterung im Studium.")],
     "Gute Werkzeuge machen ein Team schneller, das habe ich beim Bau eines Leveleditors für unser Studienprojekt erlebt.",
     "https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a005/", "abgeschickt", "antwort"),
    ("Talwind AG", "Junior Developer", "Mia Muster, mia@talwind.example",
     [("Web-Grundlagen", "Eigene Website und kleine Webprojekte.")],
     "Ich bewerbe mich, weil mich Ihre Arbeit an offenen Daten überzeugt.",
     "https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a006/", "abgeschickt", "absage"),
    ("Felsenweg AG", "Junior Software Engineer", "Lea Muster, lea@felsenweg.example",
     [("C# oder Java", "C# in mehreren Studienprojekten.")],
     "Ihre Lernplattform für Lehrbetriebe hat mich neugierig gemacht.",
     "https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a008/", "abgeschickt", "abgeschickt"),
]
SENT_DAYS_AGO = {"Felsenweg AG": 45}  # long silence: the board moves it to "No reply"

RUN = f"""# Stellensuche {TODAY.isoformat()}

**Suchfelder:** Games und Unity, Softwareentwicklung
**Quellen:** jobs.ch (14); greenhouse:pixelwerk (2)
**Ergebnis:** 4 Stellen, davon 3 neu

## A, passt direkt

### Junior Unity Developer bei Pixelwerk Studios, Zürich `NEU`
- **Link:** https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a001/
- **Wertung:** 9/10
- **Dafuer:** Unity und C# verlangt, zwei Spielprojekte in Unity belegen das.
- **Luecken:** keine Konsolenerfahrung

### Gameplay Programmer bei Nordlicht Games GmbH, Winterthur
- **Link:** https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a003/
- **Wertung:** 8/10
- **Dafuer:** Gameplay-Programmierung, Lead Programmer im Studienteam.
- **Luecken:** C++ nur aus dem Studium
- **Hinweis:** `SCHON BEWORBEN`, Ordner Bewerbungen/Nordlicht Games GmbH

## B, lohnt einen Blick

### Software Engineer C# bei Alpenblick Software AG, Zürich `NEU`
- **Link:** https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a002/
- **Wertung:** 7/10
- **Dafuer:** C# und WPF aus dem Bachelorprojekt.
- **Luecken:** Branche Bauplanung neu

## C, Grenzfaelle

### Backend Developer bei Flusslauf AG, Baden `NEU`
- **Link:** https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-00000000a007/
- **Wertung:** 4/10
- **Dafuer:** Grundlagen in Datenbanken.
- **Luecken:** Java und Kubernetes verlangt
"""


def main(target):
    root = Path(target)
    if root.exists() and any(root.iterdir()):
        sys.exit("The folder must be empty")
    shutil.copytree(APP / "example" / "de", root, dirs_exist_ok=True)
    prof = json.loads((root / "bewerbungs-board.json").read_text("utf-8"))
    prof.update(name="Lena Beispiel", email="lena@example.com", ort="Zürich", regelcheck={"woerter_min": 60, "woerter_max": 350, "zeichen": ["ß", " - "]})
    (root / "bewerbungs-board.json").write_text(json.dumps(prof, ensure_ascii=False, indent=2), "utf-8")
    state = {}
    for i, (company, title, contact, reqs, body, link, status, phase) in enumerate(APPS):
        folder = root / "Bewerbungen" / company
        folder.mkdir(parents=True)
        short = company.split()[0]
        rel = f"Bewerbungen/{company}/Bewerbungsschreiben_{short}.md"
        (root / rel).write_text(letter(company, title, contact, reqs, body, link), "utf-8")
        sent = -SENT_DAYS_AGO.get(company, 20 - i)
        state[rel] = {"status": status, "feedback": "Den zweiten Absatz konkreter machen." if i == 0 else "", "seit": d(sent)}
        if phase:
            state[rel]["phase"] = phase
            events = [f"### {d(sent)} · Status · Abgeschickt\n"]
            if phase in ("antwort", "gespraech", "absage"):
                events.insert(0, f"### {d(-6)} 10:12 · Mail rein · Ihre Bewerbung\nFrom: {contact}\n\nVielen Dank für Ihre Bewerbung, wir melden uns.\n")
            if phase == "gespraech":
                events.insert(0, f"### {d(2)} 14:00 · Termin · Erstes Gespräch\nVideo-Call mit dem Team Lead\n")
                events.insert(1, f"### {d(-2)} 09:30 · Mail rein · Einladung zum Gespräch\nFrom: {contact}\n\nWir laden Sie gerne zu einem Gespräch ein.\n")
            if phase == "abgeschickt" and company not in SENT_DAYS_AGO:
                events.insert(0, f"### {d(5)} · Frist · Unterlagen nachreichen\n")
            (folder / f"Verlauf_{short}.md").write_text(f"# Verlauf: {company}, {title}\n\n## Ereignisse\n\n" + "\n".join(events) +
                                                       "\n## Vorbereitung\n\n## Notizen\n", "utf-8")
    (root / "Bewerbungen" / "_Review").mkdir()
    (root / "Bewerbungen" / "_Review" / "review.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    (root / "Bewerbungen" / "_Stellensuche").mkdir()
    (root / "Bewerbungen" / "_Stellensuche" / f"Stellensuche_{TODAY.isoformat()}.md").write_text(RUN, "utf-8")
    print(root)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else sys.exit(__doc__))
