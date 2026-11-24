# Changelog

## 0.5.1

- New name: Application Board (was Bewerbungs-Board). Start scripts `application-board` and `application-board.cmd`, config folder `application-board` (the old folder moves over on the first start), variables `APPLICATION_BOARD_ROOT`, `APPLICATION_BOARD_CONFIG`, `APPLICATION_BOARD_SOFFICE` and so on (the old `BEWERBUNGS_*` names still work), MCP server name `application-board`. The data folder and its file names stay as they are.

## 0.5.0

- Pipeline column "No reply": applications without any activity for 30 days (sent or answered, no date ahead) move there by themselves, with a status entry in the log. Dragging one back counts as activity.
- The dossier shows how long ago the last mail was: green under a week, orange under two weeks, red after.
- Dossier sections fold with a click on their heading; the suggestion chips above the feedback fold too.
- "Posting still open?" for one application, in the dossier, the letters menu and the command palette.
- Dropdowns are drawn in the page (customizable select in current Chromium): they stay on screen with a high page zoom and follow the theme.
- Themes are sorted alphabetically; the theme "Minecraft" is now "Pixel" (a saved choice is taken over).
- License texts of the bundled libraries and fonts in `vendor/licenses/`.
- Mails open in Thunderbird with the PDFs already attached ("Open in Thunderbird" in the application and follow-up mail). Nothing is sent; Send stays in Thunderbird.
- The Thunderbird index covers 12 months instead of 120 days and 48 MB per folder. It is kept on disk (`mailindex.json.gz` in the config folder), and a folder that only grew is read from its old end.
- "Still open?" in the job search checks every posting of a run; postings that are gone are crossed out (`_Stellensuche/status.json`).
- The same job on several portals becomes one card with an "Also on" line; all links count as known in later runs.
- Tests run on every push, on Linux and Windows (GitHub Actions). Commands of the "cli" provider keep Windows paths intact.

## 0.4.0

- Fact check: after a feedback round or a draft, numbers and product or tech names that appear neither in the profile, the CV content, the posting nor the feedback are named in the answer ("Please check ..."). Only facts new in this round count.
- Pipeline "Next" view (the new start view): new mails to file, appointments and deadlines of the next 7 days, applications to follow up, final letters not sent yet, feedback not applied yet.
- Mails from Thunderbird are matched to sent applications by contact domain or company name and offered in the dossier. Adding one suggests the next phase (interview, offer, rejection), without AI.
- The job rating gets your earlier choices as examples: the letters you wrote and the postings you marked as not interesting.
- Optional AI fact check (task "AI fact check"): reads the new sentences of a round against profile and posting. The check without AI now also catches names in English letters, and earlier warnings no longer count as a source.
- Watched companies: job feeds of Personio, Greenhouse, Lever and SmartRecruiters in the job search.
- Daily automatic job search while the board runs; the result shows in "Next".
- Application mail for final letters (to the contact, with the PDFs to attach); the task is now called "Draft mails".
- Requirements from the mapping as chips above the feedback; gaps marked, one click adds feedback.
- Tone for new drafts: formal, warm or concise.
- Calendar address to subscribe to (`/calendar.ics`); the .ics download comes from the server.
- LinkedIn data export (zip) and JSON Resume as profile documents.
- Usage per month and task (tokens, Claude Code cost) under Settings → AI providers.
- Data folder as one ZIP under Settings → Folder.
- Optional check for a new version; release zips built by GitHub Actions.
- MCP server (`mcp_server.py`) for Claude Desktop, Claude Code and other MCP clients.
- The pipeline no longer waits for the first Thunderbird index; "Next" loads letters and the newest job search freshly.
- README: what leaves your computer, MCP setup.

## 0.3.0

Security
- Markdown from letters, mails, postings and AI answers is sanitized (DOMPurify); the page sends a Content-Security-Policy.
- Uploaded or linked documents other than PDF and images are downloaded, never shown as a page; `nosniff` on every answer.
- Agentic AI runs may only edit the one letter they work on and have no shell and no web access; a warning names any
  other file that changed. Plain completions never get web tools.
- CodeMirror, marked, DOMPurify and the fonts are stored in `vendor/`: no CDN, no Google Fonts, works offline.

Fixes
- Claude memory is found for paths with spaces, dots, umlauts and on Windows.
- The job search no longer rates a job again that an earlier run already rated.
- A mail added from Thunderbird is the right one even after Thunderbird compacted the folder.
- Unexpected server errors return a message instead of dropping the connection.
- Letters, logs, profile and settings are written atomically; renamed company folders keep status and history.
- Long texts sent to the AI are cut with a visible note instead of silently.
- Running AI jobs can be cancelled; a job lost by a server restart is reported.

Personal settings
- The rule check in the editor (word range, forbidden characters) comes from the profile (`regelcheck`).
- LinkedIn search places are a setting; texts no longer assume Claude as the provider.

Open source
- Settings and keys live in the user config folder (`~/.config/bewerbungs-board`, `%APPDATA%`, ...), moved there automatically.
- Start scripts `bewerbungs-board` and `bewerbungs-board.cmd`, `--open` opens the browser; LibreOffice and Poppler
  are also found in their usual Windows and macOS places.
- `tests/run.py` with demo data and a mock AI, browser test, GitHub Actions; screenshots, file format docs, CONTRIBUTING.

New
- New application from a link or a pasted posting, right in the Applications tab (`n`).
- Earlier versions of a letter with diff and restore (`v`).
- Archive for finished applications.
- Follow-up mail drafted by the AI, with mailto link.
- Statistics: reply and interview rates, funnel, results by grade.
- Browser reminders for today's and tomorrow's appointments and deadlines.
- Spell check in the editor in the language of the letter.

## 0.2.0

- Code, API and README in English; interface in German and English; data keeps its language.

## 0.1.0

- First version as its own repository.
