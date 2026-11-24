# Contributing

Thanks for helping. A few things keep the project easy to work on.

## Principles

- **No build step, no dependencies.** The server uses the Python standard library only. The page uses plain
  JavaScript; the few libraries (CodeMirror 5, marked, DOMPurify, fonts) are stored in `vendor/`. Please keep it so.
- **Your data stays local.** The server only listens on `127.0.0.1`. Nothing may load from or send to the internet
  except the AI provider the user chose and the job portals of the job search.
- **Stored formats do not change.** Folder names, file names and JSON keys in the data folder are German and stay
  so (see [docs/format.md](docs/format.md)). Code, comments, prompts and the API are English. Every interface text
  goes into `i18n.js` in German and English.
- **Untrusted text is never trusted HTML.** Postings, mails and AI answers can contain anything. Render markdown only
  through `md()` in `app.js` (DOMPurify), and give agentic AI runs only the tools and paths they need.

## Running the tests

```sh
python3 tests/run.py         # self-tests of all modules plus an API test with a demo folder and a mock AI
python3 tests/run.py --ui    # plus a browser test (needs node and chromium)
```

GitHub Actions runs the tests on every push, on Linux and Windows (`.github/workflows/test.yml`). The tests never touch your own data or settings. Every Python module also has its own self-test:
`python3 <module>.py` prints `ok`.

## Screenshots and the demo GIF

They come from made-up demo data, never from real applications:

```sh
python3 tests/demo.py /tmp/demo
BEWERBUNGS_BOARD_CONFIG=$(mktemp -d) BEWERBUNGS_ROOT=/tmp/demo HOME=$(mktemp -d) python3 server.py 8899 &
node tests/screenshots.mjs 8899 chromium /tmp/shots
magick -delay 260 -loop 0 /tmp/shots/{letters,new,jobsearch,next,pipeline,statistics}.png -resize 960x -colors 96 -layers Optimize docs/demo.gif
```

## Where things are

See the table "Structure" in the README. The server routes are the `GET` and `POST` tables at the end of
`server.py`; background jobs go through `start_job()` and the page waits for them with `waitJob()` in `app.js`.

## Pull requests

- One topic per pull request, with a short description of what changes for the user.
- Run `python3 tests/run.py --ui` before you open it; add a check for new behavior.
- Keep the job portals polite: no login, no captcha workarounds, pauses between requests.
