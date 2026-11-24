# File formats

Everything the board knows lives in plain files in your data folder. You can edit all of them by hand; the board
reads them again on the next refresh. Folder names, file names and the field labels below are the stored format.
German and English labels are both accepted.

## Application letter: `Bewerbungen/<company>/Bewerbungsschreiben_<X>.md`

One folder per company, one file per application. The file name must start with `Bewerbungsschreiben`. The file
has three parts; only the third one is the letter that goes into the Word template.

```markdown
# Infos zur Jobausschreibung          (or: # Job posting)
**Titel:** Junior Developer (80–100%)  (or: **Title:**)
**Firma:** Example Ltd                 (or: **Company:**)
**Kontakt:** Anna Example, anna@example.com   (or: **Contact:**; an e-mail here is used for follow-up mails)
**Link:** [jobs.ch](https://www.jobs.ch/...)  (matches the score of the job search)

## Aufgaben / ## Tasks
- ...

## Anforderungen / ## Requirements
- ...

# Requirements & Skills Mapping
**Requirement from the posting**
- the evidence from your profile, or the gap

# Bewerbungsschreiben                  (or: # Cover Letter)

Your Name
Street, Town
phone, e-mail

Company
Address

Bern, 14. November 2026                (Town, date: finalizing sets it to today)

**Bewerbung als Junior Developer**     (subject in bold; added from the title when missing)

Sehr geehrte Frau Example              (salutation)

Paragraph one ...

Paragraph two ...

Freundliche Grüsse
Your Name                              (must be the name from your profile)
```

The language of the letter (German or English) is detected from the heading and the salutation. It decides the date
format, the subject line and which earlier Word file is used as the template.

## Log of a sent application: `Bewerbungen/<company>/Verlauf_<X>.md`

Created by the board when you mark a letter as sent. Events are newest first.

```markdown
# Verlauf: Example Ltd, Junior Developer      (or: # Log: ...)

## Ereignisse                                 (or: ## Events)

### 2026-11-20 10:00 · Termin · Interview with Anna Example
### 2026-11-12 09:14 · Mail rein · Invitation to an interview
From: Anna Example <anna@example.com>

Thank you for your application ...

## Vorbereitung                               (or: ## Preparation)
...

## Notizen                                    (or: ## Notes)
...
```

Event types: `Mail rein` (Mail in), `Mail raus` (Mail out), `Termin` (Appointment), `Frist` (Deadline), `Notiz` (Note),
`Status`. A file keeps the language it was created in.

## Job search result: `Bewerbungen/_Stellensuche/Stellensuche_<date>.md`

```markdown
# Stellensuche 2026-11-14                     (or: # Job search ...)

**Quellen:** jobs.ch (12)                      (any **Label:** lines form the header)

## A, passt direkt                            (sections starting with A, B or C followed by , or :)

### Junior Developer bei Example Ltd, Bern `NEU`     (or: ... at ..., `NEW`)
- **Link:** https://...
- **Wertung:** 9/10                           (or: **Score:**)
- **Dafuer:** ...                             (or: **Pros:**)
- **Luecken:** ...                            (or: **Gaps:**)
- **Hinweis:** `SCHON BEWORBEN`, ...          (or: **Note:** `ALREADY APPLIED`)

## Aussortiert                                (any other section is shown as text below the cards)
```

## State files written by the board

| File | Content |
|---|---|
| `Bewerbungen/_Review/review.json` | per letter: `status` (`offen`, `final`, `abgeschickt`, `archiv`), `feedback`, `antwort`, `seit`, `phase`, `dateien`, `pruefung`, `claude_session` |
| `Bewerbungen/_Review/feedback_log.jsonl` | one line per feedback round |
| `Bewerbungen/_Review/backups/` | versions of letters and profile files, shown under "Earlier versions" |
| `Bewerbungen/_Stellensuche/suche.json` | job search settings |
| `Bewerbungen/_Stellensuche/verlauf.jsonl` | every job found so far, to mark new ones |
| `bewerbungs-board.json` | your profile, see the README |
