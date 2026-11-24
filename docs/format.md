# File formats

Everything the board knows lives in plain files in your data folder. You can edit all of them by hand; the board
reads them again on the next refresh. Inside letters, logs and job search files German and English labels are both
accepted. Data folders of the first versions use German folder, file and key names (see "Old data folders" at the end).

## Application letter: `applications/<company>/letter_<X>.md`

One folder per company, one file per application. The file name must start with `letter`. The file
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

## Log of a sent application: `applications/<company>/log_<X>.md`

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

Event types: `Mail in` (Mail rein), `Mail out` (Mail raus), `Appointment` (Termin), `Deadline` (Frist), `Note` (Notiz),
`Status`. A file keeps the language it was created in; the API uses the ids `mail_in`, `mail_out`, `appointment`,
`deadline`, `note`, `status`.

## Job search result: `applications/_jobsearch/jobsearch_<date>.md`

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
| `applications/_review/state.json` | per letter: `status` (`open`, `final`, `sent`, `archived`), `phase` (`sent`, `no_reply`, `replied`, `interview`, `offer`, `rejected`), `feedback`, `answer`, `since`, `files`, `hints`, `claude_session` |
| `applications/_review/feedback_log.jsonl` | one line per feedback round |
| `applications/_review/learnings_state.json` | up to which round the learnings were evaluated |
| `applications/_review/backups/` | versions of letters and profile files, shown under "Earlier versions" |
| `applications/_jobsearch/search.json` | job search settings |
| `applications/_jobsearch/history.jsonl` | every job found so far, to mark new ones |
| `applications/_jobsearch/postings.json` | result of "Still open?" per posting link |
| `application-board.json` | your profile, see the README |

## Old data folders

Data folders created by the first versions (up to 0.5) use German names. The board recognises them by `Bewerbungen/` or
`bewerbungs-board.json` and keeps them in that format; `layout.py` has the complete tables.

| English layout | German layout |
|---|---|
| `application-board.json` | `bewerbungs-board.json` (keys such as `ort`, `regeln`, `dateiname` with `{firma}`) |
| `profile/`, `profile/documents/cvs/` ... | `Profil/`, `Profil/Dokumente/Lebenslaeufe/` ... |
| `applications/<company>/letter_<X>.md`, `log_<X>.md` | `Bewerbungen/<company>/Bewerbungsschreiben_<X>.md`, `Verlauf_<X>.md` |
| `applications/_review/state.json` | `Bewerbungen/_Review/review.json` (values `offen`, `abgeschickt`, `gespraech` ...) |
| `applications/_jobsearch/search.json`, `history.jsonl`, `postings.json` | `Bewerbungen/_Stellensuche/suche.json`, `verlauf.jsonl`, `status.json` |
