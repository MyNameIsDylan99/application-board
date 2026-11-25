"""Sources of the built-in job search: portals with a public API, job boards with a feed, the job feeds of companies
(ATS) and the person's own sources (RSS/Atom, JSON or a web page with job links).

Every source is a dict. `collect` asks it for all search terms, `preview` for one (the Test button), `detail` gives the
text of one posting. A job is a dict with source, kind, id, title, company, place, age (days), lat, lon, url, text.
Nothing here needs a login. The base URLs can be overridden for tests only (RV_JOBS_BASE, RV_LINKEDIN_BASE).
"""
import functools
import html
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
JOBS_BASE = os.environ.get("RV_JOBS_BASE", "https://www.jobs.ch")
LI_BASE = os.environ.get("RV_LINKEDIN_BASE", "https://www.linkedin.com")
JOBUP_BASE, JR_BASE = "https://www.jobup.ch", "https://www.job-room.ch"
MAX_BYTES = 15_000_000
MAX_PAGES = 5

# ---------- Catalogue ----------
# kind "feed": read by the same generic reader as the person's own sources, so it can be copied and changed.
# url: {term} = one request per search term, {page} = page number, {days} = freshness. match: which hits stay, "title" or
# "text" (a search term is found there) or "all". remote: every job there is remote.
FEED_KEYS = ("items", "title", "company", "place", "date", "link", "text", "title_format", "link_contains")  # optional mapping
BUILTIN = [
    {"id": "jobs_ch", "name": "jobs.ch", "kind": "jobs", "area": "ch"},
    {"id": "jobup_ch", "name": "jobup.ch", "kind": "jobs", "area": "ch"},
    {"id": "job_room", "name": "job-room.ch", "kind": "jobroom", "area": "ch"},
    {"id": "swissdevjobs", "name": "SwissDevJobs", "kind": "feed", "area": "ch", "match": "title", "url": "https://swissdevjobs.ch/rss",
     "title_format": "{title} @ {company} [{salary}] | {title} @ {company}"},
    {"id": "linkedin", "name": "LinkedIn", "kind": "linkedin", "area": "all"},
    {"id": "arbeitnow", "name": "Arbeitnow", "kind": "feed", "area": "de", "match": "title", "url": "https://www.arbeitnow.com/api/job-board-api?page={page}"},
    {"id": "himalayas", "name": "Himalayas", "kind": "feed", "area": "remote", "remote": True, "match": "title",
     "url": "https://himalayas.app/jobs/api/search?q={term}&page={page}", "place": "locationRestrictions", "link": "guid"},
    {"id": "jobicy", "name": "Jobicy", "kind": "feed", "area": "remote", "remote": True, "match": "title",
     "url": "https://jobicy.com/api/v2/remote-jobs?count=50&tag={term}"},
    {"id": "remoteok", "name": "Remote OK", "kind": "feed", "area": "remote", "remote": True, "match": "title", "url": "https://remoteok.com/api"},
    {"id": "weworkremotely", "name": "We Work Remotely", "kind": "feed", "area": "remote", "remote": True, "match": "title",
     "url": "https://weworkremotely.com/remote-jobs.rss", "title_format": "{company}: {title}"},
    {"id": "workwithindies", "name": "Work With Indies", "kind": "feed", "area": "games", "match": "text",
     "url": "https://www.workwithindies.com/careers/rss.xml",
     "title_format": "{company} is hiring a {title} to work from {place} | {company} is hiring an {title} to work from {place}"},
    {"id": "remotegamejobs", "name": "Remote Game Jobs", "kind": "feed", "area": "games", "remote": True, "match": "text",
     "url": "https://remotegamejobs.com/feed.rss", "title_format": "{company} is hiring {title} (Remote Job) | {company} is hiring {title}"},
]
BY_ID = {s["id"]: s for s in BUILTIN}

# A watched company is a line "<ats>:<slug> [name]" in search.json under "companies", e.g. "greenhouse:acme Acme Ltd".
# The address of the careers page works too (company_line turns it into such a line).
ATS = {"personio": "https://{slug}.jobs.personio.de/xml", "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
       "lever": "https://api.lever.co/v0/postings/{slug}?mode=json", "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
       "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}", "recruitee": "https://{slug}.recruitee.com/api/offers/",
       "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", "teamtailor": "https://{slug}.teamtailor.com/jobs.rss"}
ATS_FEED = {"ashby": {"link": "jobUrl", "text": "descriptionPlain"}, "recruitee": {"link": "careers_url", "place": "location", "date": "published_at"},
            "workable": {"place": "city", "date": "published_on"}, "teamtailor": {}}  # read by the generic reader
ATS_LINE = re.compile(rf"\s*({'|'.join(ATS)}):([A-Za-z0-9._-]+)\s*(.*)$")
ATS_URL = [(re.compile(p, re.I), k) for p, k in (
    (r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w.-]+)", "greenhouse"), (r"https?://jobs\.lever\.co/([\w.-]+)", "lever"),
    (r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)", "personio"), (r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w.-]+)", "smartrecruiters"),
    (r"https?://jobs\.ashbyhq\.com/([\w.-]+)", "ashby"), (r"https?://([\w-]+)\.recruitee\.com", "recruitee"),
    (r"https?://apply\.workable\.com/([\w.-]+)", "workable"), (r"https?://([\w-]+)\.teamtailor\.com", "teamtailor"))]


def company_line(line):
    """A line of the watched companies in its normal form; ValueError when it is none."""
    line = line.strip()
    if ATS_LINE.match(line):
        return line
    for rx, kind in ATS_URL:
        m = rx.match(line)
        if m:
            name = line.split(None, 1)[1].strip() if " " in line else ""
            return f"{kind}:{m.group(1)} {name}".strip()
    raise ValueError(line[:80])


def active(cfg):
    """The sources of a search: built-in and own ones that are switched on, then the watched companies."""
    out = [s for s in BUILTIN if cfg.get("sources", {}).get(s["id"])]
    out += [{**s, "kind": "feed"} for s in cfg.get("custom_sources") or [] if s.get("on")]
    for line in cfg.get("companies") or []:
        m = ATS_LINE.match(line)
        if m:
            kind, slug, name = m.groups()
            out.append({"id": f"{kind}:{slug}", "name": f"{kind}:{slug}", "kind": "ats", "ats": kind, "slug": slug, "company": name.strip() or slug, "match": "title"})
    return out


def clean_custom(s):
    """One own source, checked and reduced to the known keys; ValueError(reason) when it cannot work."""
    s = {k: s[k] for k in ("id", "name", "url", "on", "remote", "match", *FEED_KEYS) if k in s}
    for k in ("id", "name", "url", "match", *FEED_KEYS):
        s[k] = " ".join(str(s.get(k) or "").split())[:500]
    s["on"], s["remote"] = bool(s.get("on")), bool(s.get("remote"))
    s["match"] = s["match"] if s["match"] in ("title", "text", "all") else "title"
    if not s["name"]:
        raise ValueError("name missing")
    p = urllib.parse.urlsplit(s["url"])
    if p.scheme not in ("http", "https") or not p.hostname or re.search(r"[{}]", p.netloc):
        raise ValueError(f"{s['name']}: not a web address (https://...)")
    if not re.fullmatch(r"own_[a-z0-9_]{1,40}", s["id"]):
        s["id"] = "own_" + (re.sub(r"[^a-z0-9]+", "_", norm(s["name"])).strip("_")[:30] or "source")
    return {k: v for k, v in s.items() if v != "" or k in ("id", "name", "url")}


# ---------- Reading pages ----------

class Closed(ValueError):
    """The posting itself says that it is over."""


@functools.lru_cache(maxsize=2000)
def _public_host(host):
    try:
        return all(ipaddress.ip_address(a[4][0].split("%")[0]).is_global for a in socket.getaddrinfo(host, None))
    except OSError:
        return True  # unknown name: nothing to reach


def link_ok(url, base=""):
    """May a link from a feed be opened? Only http(s), and no address inside the own network unless the source is there."""
    try:
        u, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
        return u.scheme in ("http", "https") and bool(u.hostname) and (u.hostname == b.hostname or _public_host(u.hostname))
    except ValueError:
        return False


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not link_ok(newurl, req.full_url):
            raise ValueError("redirect to an address that is not public")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_Redirect)


def get(url, timeout=25, data=None):
    if not re.match(r"https?://", url, re.I):
        raise ValueError("only http and https addresses")
    headers = {"User-Agent": UA, "Accept": "application/json, application/rss+xml, text/html;q=0.9, */*;q=0.8", "Accept-Language": "de-CH,de;q=0.9,en;q=0.8"}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    with _OPENER.open(urllib.request.Request(url, headers=headers, data=data), timeout=timeout) as r:
        raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("answer too large")
        return raw.decode("utf-8", "replace")


def strip_html(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", s)).strip()


def rich(s):
    """Text of a description that is HTML, or HTML escaped once more (RSS)."""
    s = str(s or "")
    return strip_html(html.unescape(s) if "&lt;" in s and "<" not in s else s)


def one(s, n=200):
    return " ".join(strip_html(str(s or "")).split())[:n]


def norm(s):
    return s.lower().replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("é", "e")


def age_of(v):
    """Days since a date in any usual form (ISO, RFC 822, seconds or milliseconds, 31.12.2026); 0 when unknown."""
    try:
        if isinstance(v, (int, float)) or re.fullmatch(r"\d{10}(\d{3})?", str(v or "")):
            n = float(v)
            d = datetime.fromtimestamp(n / 1000 if n > 1e11 else n, timezone.utc).date()
        elif re.match(r"\d{4}-\d{2}-\d{2}", str(v)):
            d = date.fromisoformat(str(v)[:10])
        elif re.match(r"\d{1,2}\.\d{1,2}\.\d{4}", str(v)):
            d = datetime.strptime(re.match(r"[\d.]+", str(v)).group(0)[:10], "%d.%m.%Y").date()
        else:
            d = parsedate_to_datetime(str(v)).date()
        return max(0, (date.today() - d).days)
    except (ValueError, TypeError, OverflowError, OSError):
        return 0


def term_match(text, terms, words=False):
    """Does a text contain one of the search terms (all its words, accents and case ignored; words: as whole words)?
    A short word must begin a word ("Unity" is not in "Community"), a long one may be part of a compound."""
    t = norm(text)
    hit = lambda w: re.search(rf"{'(?<![a-z0-9])' if words or len(w) < 7 else ''}{re.escape(w)}{'(?![a-z0-9])' if words else ''}", t)
    return any(all(hit(w) for w in norm(term).split()) for term in terms if term.strip())


def matches(src, c, terms):
    how = src.get("match", "all")
    return how == "all" or term_match(c["title"], terms) or (how == "text" and term_match(f"{c['title']} {c.get('text', '')}", terms, words=True))


# ---------- Portals ----------

def jobs_search(term, days, page=1, base=JOBS_BASE, name="jobs.ch"):
    q = urllib.parse.urlencode({"query": term, "rows": 20, "page": page, "publication_date": days})
    d = json.loads(get(f"{base}/api/v1/public/search?{q}"))
    out = []
    for j in d.get("documents", []):
        coords, links = j.get("coordinates") or {}, j.get("_links") or {}
        url = next((links[k]["href"] for k in ("detail_de", "detail_fr", "detail_en") if (links.get(k) or {}).get("href")), "")
        out.append({"source": name, "kind": "jobs", "id": j.get("job_id", ""), "title": j.get("title", ""), "company": j.get("company_name", ""),
                    "place": j.get("place", ""), "age": int(j.get("age") or 0), "lat": coords.get("lat"), "lon": coords.get("lon"),
                    "url": url or f"https://www.{name}/de/stellenangebote/detail/{j.get('job_id', '')}/"})
    return out


def jobs_detail(id_, base=JOBS_BASE):
    d = json.loads(get(f"{base}/api/v1/public/search/job/{id_}"))
    if d.get("is_active") is False:
        raise Closed("posting no longer active")
    end = d.get("publication_end_date") or ""
    if end and end < date.today().isoformat():
        raise Closed(f"deadline passed ({end})")
    text = strip_html(d.get("template_text") or d.get("template") or "")
    meta = (f"Title: {d.get('title', '')}\nCompany: {d.get('company_name', '')}\nPlace: {d.get('place', '')}\n"
            f"Published: {str(d.get('publication_date', ''))[:10]}, deadline: {end}\nWorkload: {d.get('employment_grades', '')}\n\n")
    return meta + text, {"title": d.get("title"), "company": d.get("company_name"), "place": d.get("place"), "deadline": end}


def _jr(ad):
    """Title, company, place, text of an advertisement of job-room.ch (the portal of the Swiss public employment service)."""
    c = ad.get("jobContent") or {}
    desc = (c.get("jobDescriptions") or [{}])[0]
    return one(desc.get("title")), one((c.get("company") or {}).get("name")), one((c.get("location") or {}).get("city")), rich(desc.get("description")), c


def jobroom_search(term, days, page=1):
    body = {"workloadPercentageMin": 10, "workloadPercentageMax": 100, "onlineSince": min(days, 60), "displayRestricted": False, "keywords": [term]}
    out = []
    for x in json.loads(get(f"{JR_BASE}/jobadservice/api/jobAdvertisements/_search?page={page - 1}&size=20", data=body)):
        ad = x.get("jobAdvertisement") or {}
        title, company, place, _, c = _jr(ad)
        coords = (c.get("location") or {}).get("coordinates") or {}
        num = lambda v: float(v) if v else None
        out.append({"source": "job-room.ch", "kind": "jobroom", "id": ad.get("id", ""), "title": title, "company": company, "place": place,
                    "age": age_of((ad.get("publication") or {}).get("startDate")), "lat": num(coords.get("lat")), "lon": num(coords.get("lon")),
                    "url": f"{JR_BASE}/job-search/{ad.get('id', '')}"})
    return out


def jobroom_detail(id_):
    ad = json.loads(get(f"{JR_BASE}/jobadservice/api/jobAdvertisements/{id_}"))
    if not str(ad.get("status", "")).startswith("PUBLISHED"):
        raise Closed("posting no longer active")
    end = (ad.get("publication") or {}).get("endDate") or ""
    if end and end < date.today().isoformat():
        raise Closed(f"deadline passed ({end})")
    title, company, place, text, c = _jr(ad)
    e = c.get("employment") or {}
    return (f"Title: {title}\nCompany: {company}\nPlace: {place}\nWorkload: {e.get('workloadPercentageMin', '')} to {e.get('workloadPercentageMax', '')} %\n"
            f"Original: {c.get('externalUrl') or ''}\n\n{text}"), {"title": title, "company": company, "place": place, "deadline": end}


def li_search(term, place, days, start=0):
    q = urllib.parse.urlencode({"keywords": term, "location": place, "f_TPR": f"r{days * 86400}", "start": start})
    page = get(f"{LI_BASE}/jobs-guest/jobs/api/seeMoreJobPostings/search?{q}")
    out = []
    for card in re.split(r"<li\b", page)[1:]:
        m = re.search(r"urn:li:jobPosting:(\d+)", card) or re.search(r"/jobs/view/[^\"'?]*?(\d{6,})", card)
        if not m:
            continue
        jid = m.group(1)
        t = re.search(r'class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3>', card, re.S)
        c = re.search(r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>(.*?)</h4>', card, re.S)
        o = re.search(r'class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)</span>', card, re.S)
        dt = re.search(r'datetime="(\d{4}-\d{2}-\d{2})"', card)
        out.append({"source": "LinkedIn", "kind": "linkedin", "id": jid, "title": strip_html(t.group(1)) if t else "", "company": strip_html(c.group(1)) if c else "",
                    "place": strip_html(o.group(1)) if o else "", "age": age_of(dt.group(1)) if dt else 0, "lat": None, "lon": None,
                    "url": f"https://www.linkedin.com/jobs/view/{jid}"})
    return out


def li_detail(id_):
    page = get(f"{LI_BASE}/jobs-guest/jobs/api/jobPosting/{id_}")
    if re.search(r"no longer accepting applications|nimmt keine Bewerbungen mehr", page, re.I):
        raise Closed("posting no longer accepts applications")
    t = re.search(r'class="[^"]*top-card-layout__title[^"]*"[^>]*>(.*?)</h\d>', page, re.S)
    c = re.search(r'class="[^"]*topcard__org-name-link[^"]*"[^>]*>(.*?)</a>', page, re.S)
    o = re.search(r'class="[^"]*topcard__flavor--bullet[^"]*"[^>]*>(.*?)</span>', page, re.S)
    d = re.search(r'class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', page, re.S)
    meta = {"title": strip_html(t.group(1)) if t else None, "company": strip_html(c.group(1)) if c else None, "place": strip_html(o.group(1)) if o else None}
    body = strip_html(d.group(1) if d else page)
    return f"Title: {meta['title'] or ''}\nCompany: {meta['company'] or ''}\nPlace: {meta['place'] or ''}\n\n" + body, meta


def portal_detail(url):
    """(text, meta) of a posting on one of the portals above, None for any other address."""
    m = re.search(r"(jobs|jobup)\.ch/.*?/detail/([0-9a-f-]{36})", url) or re.search(r"(jobs|jobup)\.ch/.*?[?&]jobid=([0-9a-f-]{36})", url, re.I)
    if m:
        return jobs_detail(m.group(2), JOBUP_BASE if m.group(1).lower() == "jobup" else JOBS_BASE)
    m = re.search(r"job-room\.ch/job-search/([0-9a-f-]{36})", url)
    if m:
        return jobroom_detail(m.group(1))
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?]*?-)?(\d{6,})", url)
    return li_detail(m.group(1)) if m else None


# ---------- Generic reader: RSS/Atom, JSON, web page ----------

ALIASES = {
    "title": ("title", "jobTitle", "job_title", "position", "headline", "text", "name"),
    "company": ("company_name", "companyName", "company", "employer", "organization", "hiringOrganization", "subcompany"),
    "place": ("location", "jobGeo", "candidate_required_location", "jobLocation", "place", "city", "region", "office", "locations"),
    "date": ("pubDate", "published", "publication_date", "published_at", "publishedAt", "published_on", "datePosted", "date", "created_at", "createdAt", "epoch", "updated"),
    "link": ("url", "link", "jobUrl", "job_url", "absolute_url", "hostedUrl", "applicationLink", "apply_url", "guid"),
    "text": ("description", "jobDescription", "descriptionPlain", "descriptionHtml", "content", "encoded", "body", "summary", "jobExcerpt", "excerpt"),
}


def flat(v):
    """Readable text of a JSON value: a list joined, of an object its name."""
    if isinstance(v, list):
        return ", ".join(x for x in map(flat, v) if x)
    if isinstance(v, dict):
        for k in ("name", "title", "city", "addressLocality", "label", "value", "address"):
            if v.get(k):
                return flat(v[k])
        return ""
    return "" if v is None or isinstance(v, bool) else str(v)


def pick(o, spec, key):
    """Field of an item: "=fixed text", a path like "company.name", a pattern like "https://x/jobs/{id}", or guessed."""
    if spec.startswith("="):
        return spec[1:].strip()
    if "{" in spec:
        return re.sub(r"\{([\w.]+)\}", lambda m: urllib.parse.quote(pick(o, m.group(1), key)), spec)
    if spec:
        for part in spec.split("."):
            o = o.get(part) if isinstance(o, dict) else (o[int(part)] if isinstance(o, list) and part.isdigit() and int(part) < len(o) else None)
        return flat(o)
    return next((v for v in (flat(o.get(k)) for k in ALIASES[key]) if v), "")


def find_items(d, path=""):
    """The list of jobs in a JSON answer: at `path`, or the longest list of objects that have something like a title."""
    if path:
        for part in path.split("."):
            d = d.get(part) if isinstance(d, dict) else None
        return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []
    best, todo = [], [(d, 0)]
    while todo:
        x, depth = todo.pop()
        if isinstance(x, list):
            items = [i for i in x if isinstance(i, dict) and pick(i, "", "title") and pick(i, "", "link")]
            if len(items) > len(best):
                best = items
        if depth < 4:
            todo += [(v, depth + 1) for v in (x.values() if isinstance(x, dict) else x[:3] if isinstance(x, list) else []) if isinstance(v, (dict, list))]
    return best


def xml_items(raw):
    """Items of RSS or Atom as dicts (local tag names; a tag that only has children keeps their texts below its name)."""
    raw = re.sub(r"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", "", raw, flags=re.S).lstrip("﻿ \r\n\t")  # no entities from a foreign feed
    local = lambda e: e.tag.rsplit("}", 1)[-1] if isinstance(e.tag, str) else ""
    out = []
    for el in ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", raw)).iter():
        if local(el) not in ("item", "entry", "position", "job"):
            continue
        item = {}
        for ch in el.iter():
            k = local(ch)
            if ch is el or not k:
                continue
            v = (ch.text or "").strip()
            if k == "link" and not v:
                v = ch.get("href", "") if ch.get("rel", "alternate") == "alternate" else ""
            if v and k not in item:
                item[k] = v
        out.append(item)
    return out


class _Links(HTMLParser):
    """Links of a page with their text, and the heading inside the link when there is one."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.cur, self.head, self.skip = [], None, 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "a":
            self.cur = {"href": dict(attrs).get("href") or "", "text": [], "head": []}
        elif self.cur is not None and re.fullmatch(r"h[1-6]", tag):
            self.head += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag == "a" and self.cur is not None:
            self.out.append((self.cur["href"], " ".join(self.cur["head"]) or next(iter(self.cur["text"]), "")))
            self.cur, self.head = None, 0
        elif re.fullmatch(r"h[1-6]", tag):
            self.head = max(0, self.head - 1)

    def handle_data(self, data):
        data = " ".join(data.split())
        if data and self.cur is not None and not self.skip:
            self.cur["head" if self.head else "text"].append(data)


JOB_LINK = re.compile(r"/(?:jobs?|careers?|karriere|stellen\w*|vacanc\w+|positions?|openings?|offres?\w*|emplois?|inserate?|o|j)/(?:[^?#]*/)?[^/?#]{4,}", re.I)


def json_ld(page):
    """schema.org JobPosting objects of a page."""
    out = []

    def walk(x):
        if isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            kinds = x.get("@type")
            if "JobPosting" in (kinds if isinstance(kinds, list) else [kinds]):
                out.append(x)
            else:
                for y in x.values():
                    walk(y)
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            walk(json.loads(m.group(1).strip(), strict=False))
        except (ValueError, RecursionError):
            continue
    return out


def ld_place(x):
    locs = x.get("jobLocation") or []
    names = []
    for l in locs if isinstance(locs, list) else [locs]:
        a = l.get("address") if isinstance(l, dict) else l
        names.append(flat(a.get("addressLocality") or a.get("addressRegion") or a.get("addressCountry")) if isinstance(a, dict) else flat(a))
    return ", ".join(([] if x.get("jobLocationType") != "TELECOMMUTE" else ["Remote"]) + [n for n in names if n])


def html_items(page, base, contains=""):
    items = [{"title": flat(x.get("title")), "company": flat(x.get("hiringOrganization")), "place": ld_place(x), "date": flat(x.get("datePosted")),
              "url": flat(x.get("url")) or base, "description": flat(x.get("description"))} for x in json_ld(page)]
    if items and not contains:
        return items
    p = _Links()
    p.feed(page)
    seen, out = set(), []
    for href, text in p.out:
        try:
            url = urllib.parse.urljoin(base, href.strip()).split("#")[0]
        except ValueError:
            continue
        fits = contains in url if contains else (JOB_LINK.search(urllib.parse.urlsplit(url).path) and 4 <= len(text) <= 150)
        if fits and text and url not in seen and url != base:
            seen.add(url)
            out.append({"title": text, "url": url})
    return out


def title_parts(title, fmt):
    """Splits a title by a format such as "{company}: {title}" (other names, {salary}, are dropped); alternatives separated by " | "."""
    for alt in (fmt or "").split(" | "):
        parts = re.split(r"\{(\w+)\}", alt.strip())
        names = parts[1::2]
        if "title" not in names or len(set(names)) != len(names) or not all(n.isidentifier() for n in names):
            continue
        rx = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>.+?)" for i, p in enumerate(parts))
        m = re.fullmatch(rx, title, re.I)
        if m:
            return {k: v.strip() for k, v in m.groupdict().items() if k in ("title", "company", "place")}
    return {}


def feed_parse(src, raw, base):
    """Answer of a feed source -> jobs. The format (JSON, RSS/Atom, web page) is recognised from the answer."""
    head = raw.lstrip("﻿ \r\n\t")[:600]
    web = False
    if head[:1] in "{[" and head:
        items = find_items(json.loads(raw), src.get("items", ""))
    elif re.match(r"<\?xml|<rss|<feed|<rdf|<!--", head, re.I) and not re.search(r"<html", head, re.I):
        items = xml_items(raw)
    else:
        items, web = html_items(raw, base, src.get("link_contains", "")), True
    out = []
    for o in items:
        f = {k: pick(o, "" if web and not src.get(k, "").startswith("=") else src.get(k, ""), k) for k in ALIASES}
        title = one(f["title"])
        parts = title_parts(title, src.get("title_format"))
        title, company, place = parts.get("title", title), one(f["company"]) or parts.get("company", ""), one(f["place"], 2000) or parts.get("place", "")
        try:
            url = urllib.parse.urljoin(base, html.unescape(f["link"]).strip())
        except ValueError:
            continue
        if not title or not link_ok(url, base):
            continue
        if not src.get("remote") and not re.search("remote", place, re.I) and any(o.get(k) is True for k in ("remote", "isRemote", "telecommuting")):
            place = ", ".join(x for x in (place, "Remote") if x)
        region = place
        if src.get("remote"):
            place = f"Remote ({one(place, 60)})" if place and not re.search(r"remote|worldwide|anywhere", place, re.I) else "Remote"
            region = f"Remote, {region}" if region else "Remote"
        out.append({"source": src["name"], "kind": "feed", "id": "", "title": title, "company": company or src["name"], "place": one(place, 90), "region": region,
                    "age": age_of(f["date"]), "lat": None, "lon": None, "url": url, "text": rich(f["text"])})
    return out


def ats_parse(kind, slug, raw, company=""):
    """Raw answer of a company feed -> jobs (with the posting text where the feed has it)."""
    company = company or slug
    out = []
    if kind in ATS_FEED:
        out = feed_parse({"name": company, "company": f"={company}", **ATS_FEED[kind]}, raw, ATS[kind].format(slug=slug))
        if kind == "ashby":
            hidden = {j.get("jobUrl") for j in json.loads(raw).get("jobs", []) if j.get("isListed") is False}
            out = [c for c in out if c["url"] not in hidden]
    elif kind == "personio":
        root = ET.fromstring(re.sub(r"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", "", raw, flags=re.S))  # no entities from a foreign feed
        for pos in root.iter("position"):
            g = lambda k: (pos.findtext(k) or "").strip()
            desc = "\n\n".join(f"{d.findtext('name') or ''}\n{strip_html(d.findtext('value') or '')}" for d in pos.iter("jobDescription"))
            out.append({"id": g("id"), "title": g("name"), "place": g("office"), "age": age_of(g("createdAt")),
                        "url": f"https://{slug}.jobs.personio.de/job/{g('id')}", "text": desc, "company": g("subcompany") or company})
    elif kind == "greenhouse":
        for j in json.loads(raw).get("jobs", []):
            out.append({"id": str(j.get("id", "")), "title": j.get("title", ""), "place": (j.get("location") or {}).get("name", ""),
                        "age": age_of(j.get("first_published") or j.get("updated_at")), "url": j.get("absolute_url", ""),
                        "text": strip_html(html.unescape(j.get("content") or "")), "company": company})
    elif kind == "lever":
        for j in json.loads(raw):
            lists = "\n\n".join(f"{x.get('text', '')}\n{strip_html(x.get('content', ''))}" for x in j.get("lists") or [])
            out.append({"id": j.get("id", ""), "title": j.get("text", ""), "place": (j.get("categories") or {}).get("location", ""),
                        "age": age_of(j.get("createdAt")), "url": j.get("hostedUrl", ""),
                        "text": (j.get("descriptionPlain") or "") + "\n\n" + lists, "company": company})
    elif kind == "smartrecruiters":
        for j in json.loads(raw).get("content", []):
            loc = j.get("location") or {}
            out.append({"id": j.get("id", ""), "title": j.get("name", ""), "age": age_of(j.get("releasedDate")),
                        "place": ", ".join(x for x in (loc.get("city"), loc.get("country"), "Remote" if loc.get("remote") else "") if x),
                        "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id', '')}", "text": "", "slug": slug,
                        "company": (j.get("company") or {}).get("name") or company})
    out = [c for c in out if link_ok(c["url"])]
    for c in out:
        c.update(source=kind, kind="ats", lat=None, lon=None)
    return out


def sr_detail(c):
    d = json.loads(get(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings/{c['id']}"))
    sections = (d.get("jobAd") or {}).get("sections") or {}
    return "\n\n".join(f"{v.get('title', '')}\n{strip_html(v.get('text', ''))}" for v in sections.values() if isinstance(v, dict))


def page_detail(url):
    """(text, meta) of any posting page: its schema.org data when it has some, otherwise the visible text."""
    page = get(url)
    ld = json_ld(page)
    if len(ld) == 1:
        x = ld[0]
        end = str(x.get("validThrough") or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) and end < date.today().isoformat():
            raise Closed(f"deadline passed ({end})")
        text = rich(flat(x.get("description")))
        if len(text) > 200:
            return text, {"company": one(flat(x.get("hiringOrganization"))), "place": one(ld_place(x), 90), "deadline": end}
    m = re.search(r'"(?:addressLocality|actualCity|city)"\s*:\s*"([^"\\]{2,60})"', page)  # data of the page's own script
    return strip_html(page)[:12000], {"place": one(m.group(1), 90)} if m else {}


# ---------- Search ----------

def ask(src, term, place, page, cfg):
    """One request to a source -> jobs, before matching."""
    days, kind = int(cfg.get("max_age_days") or 30), src["kind"]
    if kind == "jobs":
        return jobs_search(term, days, page, JOBS_BASE if src["id"] == "jobs_ch" else JOBUP_BASE, src["name"])
    if kind == "jobroom":
        return jobroom_search(term, days, page)
    if kind == "linkedin":
        return li_search(term, place, days, (page - 1) * 25)
    if kind == "ats":
        return ats_parse(src["ats"], src["slug"], get(ATS[src["ats"]].format(slug=src["slug"])), src["company"])
    url = src["url"]
    for k, v in (("{term}", urllib.parse.quote_plus(term)), ("{page}", str(page)), ("{days}", str(days))):
        url = url.replace(k, v)
    return feed_parse(src, get(url), url)


def plan(src, terms, cfg):
    """The requests of a source: [(field, term, place)], each read for up to `pages(src, cfg)` pages."""
    if src["kind"] in ("jobs", "jobroom") or "{term}" in src.get("url", ""):
        return [(f, t, "") for f, t in terms]
    if src["kind"] == "linkedin":
        places = [p for p in cfg.get("linkedin_places") or ["Schweiz"] if p] + (["Remote"] if cfg.get("remote") else [])
        return [(f, t, p) for f, t in terms for p in places]
    return [("", "", "")]


def pages(src, cfg):
    paged = src["kind"] in ("jobs", "jobroom", "linkedin") or "{page}" in src.get("url", "")
    return min(MAX_PAGES, max(1, int(cfg.get("pages") or 1))) if paged else 1


def collect(src, terms, cfg, add, log, stop, pause):
    """All jobs of a source through add(job). Returns the line for the result file; errors never end the search."""
    name, wait = src["name"], pause if src["kind"] == "linkedin" else pause / 5
    words, errors, throttled, found = [t for _, t in terms], 0, 0, 0
    for field, term, place in plan(src, terms, cfg):
        label = " ".join(x for x in (name, f"'{term}'" if term else "", place) if x)
        for page in range(1, pages(src, cfg) + 1):
            if stop() or throttled >= 3 or errors >= 3:
                break
            try:
                res = ask(src, term, place, page, cfg)
                hits = [c for c in res if matches(src, c, words)]
                for c in hits:
                    c["field"] = field
                    add(c)
                found += len(hits)
                log(f"{label}{f' page {page}' if page > 1 else ''}: {len(hits)} hits" + (f" of {len(res)}" if len(res) != len(hits) else ""))
                throttled = 0
                if not res:
                    break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    throttled += 1
                    log(f"{name} throttles (429), waiting {10 * throttled} s")
                    time.sleep(0 if pause == 0 else 10 * throttled)
                else:
                    errors += 1
                    log(f"{label}: HTTP {e.code}")
                break
            except Exception as e:
                errors += 1
                log(f"{label}: error {str(e)[:200]}")
                break
            finally:
                time.sleep(wait)
        if stop() or throttled >= 3 or errors >= 3:
            break
    if errors >= 3:
        log(f"{name}: too many errors, source skipped")
    return f"{name} ({found})" + ("; stopped after repeated HTTP 429" if throttled >= 3 else " (partly failed)" if errors else "")


def preview(src, terms, cfg, n=8):
    """One request of a source for the Test button."""
    field, term, place = plan(src, terms or [("", "")], cfg)[0]
    res = ask(src, term, place, 1, cfg)
    words = [t for _, t in terms]
    jobs = [{"title": c["title"], "company": c["company"], "place": c["place"], "age": c["age"], "url": c["url"], "match": matches(src, c, words),
             "text": len(c.get("text") or "")} for c in res]
    return {"term": term, "count": len(jobs), "matching": sum(j["match"] for j in jobs), "jobs": sorted(jobs, key=lambda j: not j["match"])[:n]}


def detail(c):
    """(text for the rating, meta) of a job; Closed when the posting is over."""
    if c["kind"] == "jobs":
        return jobs_detail(c["id"], JOBUP_BASE if c["source"] == "jobup.ch" else JOBS_BASE)
    if c["kind"] == "jobroom":
        return jobroom_detail(c["id"])
    if c["kind"] == "linkedin":
        return li_detail(c["id"])
    text, meta = c.get("text") or "", {}
    if c["source"] == "smartrecruiters":
        text = text or sr_detail(c)
    elif len(text) < 600 or not c.get("place"):  # only a teaser in the feed, or no place: read the posting itself
        try:
            full, meta = page_detail(c["url"])
            text = text if len(text) >= 600 else full
        except (urllib.error.URLError, TimeoutError) as e:
            if not text or getattr(e, "code", 0) in (404, 410):
                raise
    if c["company"] != c["source"]:
        meta.pop("company", None)  # the feed knows it already
    if c.get("place"):
        meta.pop("place", None)
    return f"Title: {c['title']}\nCompany: {meta.get('company') or c['company']}\nPlace: {meta.get('place') or c['place']}\n\n{text}", meta


if __name__ == "__main__":  # self-test without network
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    gh = ats_parse("greenhouse", "acme", json.dumps({"jobs": [{"id": 1, "title": "Unity Developer", "location": {"name": "Zurich"},
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "content": "&lt;p&gt;C# and Unity&lt;/p&gt;", "first_published": "2026-11-01"}]}), "Acme")
    assert gh[0]["title"] == "Unity Developer" and gh[0]["text"] == "C# and Unity" and gh[0]["company"] == "Acme" and gh[0]["source"] == "greenhouse", gh
    pe = ats_parse("personio", "acme", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaa">]><workzag-jobs><position><id>7</id><office>Bern</office>'
                   '<name>Software Engineer</name><createdAt>2026-11-02T10:00:00+01:00</createdAt><jobDescriptions><jobDescription><name>Tasks</name>'
                   '<value><![CDATA[<p>Build &amp; ship</p>]]></value></jobDescription></jobDescriptions></position></workzag-jobs>')
    assert pe[0]["url"] == "https://acme.jobs.personio.de/job/7" and "Build & ship" in pe[0]["text"] and pe[0]["place"] == "Bern", pe
    lv = ats_parse("lever", "acme", json.dumps([{"id": "x", "text": "Game Programmer", "categories": {"location": "Remote"}, "hostedUrl": "https://jobs.lever.co/acme/x",
                                                 "descriptionPlain": "Unity", "lists": [{"text": "You", "content": "<li>C#</li>"}], "createdAt": 1767225600000}]))
    sr = ats_parse("smartrecruiters", "acme", json.dumps({"content": [{"id": "9", "name": "IT Support", "location": {"city": "Basel", "remote": True}, "releasedDate": "2026-11-03"}]}))
    assert lv[0]["place"] == "Remote" and "C#" in lv[0]["text"] and sr[0]["place"] == "Basel, Remote" and sr[0]["url"].endswith("/acme/9"), (lv, sr)
    ab = ats_parse("ashby", "acme", json.dumps({"jobs": [
        {"title": "Dev", "location": "Zurich", "jobUrl": "https://jobs.ashbyhq.com/acme/1", "publishedAt": "2026-11-01T00:00:00Z", "descriptionPlain": "C#", "isListed": True},
        {"title": "Secret", "location": "Zurich", "jobUrl": "https://jobs.ashbyhq.com/acme/2", "isListed": False}]}), "Acme")
    assert [(c["title"], c["company"], c["place"], c["text"], c["source"]) for c in ab] == [("Dev", "Acme", "Zurich", "C#", "ashby")], ab
    tt = ats_parse("teamtailor", "acme", '<?xml version="1.0"?><rss xmlns:tt="https://teamtailor.com/locations"><channel><item><title>Dev</title><description>&lt;p&gt;Hi&lt;/p&gt;</description>'
                   '<pubDate>Tue, 24 Nov 2026 10:33:49 +0100</pubDate><link>https://acme.teamtailor.com/jobs/1-dev</link><tt:locations><tt:location><tt:city>Bern</tt:city></tt:location></tt:locations></item></channel></rss>')
    assert (tt[0]["title"], tt[0]["place"], tt[0]["text"], tt[0]["url"]) == ("Dev", "Bern", "Hi", "https://acme.teamtailor.com/jobs/1-dev"), tt
    assert ATS_LINE.match("greenhouse:acme Acme Ltd").groups() == ("greenhouse", "acme", "Acme Ltd")
    for url, line in (("https://boards.greenhouse.io/acme/jobs/1 Acme Ltd", "greenhouse:acme Acme Ltd"), ("https://acme.jobs.personio.com/", "personio:acme"),
                      ("https://jobs.ashbyhq.com/acme", "ashby:acme"), ("https://apply.workable.com/acme/", "workable:acme"), ("lever:acme", "lever:acme")):
        assert company_line(url) == line, (url, company_line(url))
    try:
        company_line("Acme Ltd")
        raise SystemExit("invalid company line accepted")
    except ValueError:
        pass
    assert term_match("Junior Unity-Entwickler (m/w/d)", ["Unity Entwickler"]) and not term_match("Sales Manager", ["Unity", "C#"])
    assert not term_match("Community Manager", ["Unity"]) and term_match("Softwareentwickler", ["Entwickler"])
    assert title_parts("Dev 80-100% @ Acme AG [CHF 1]", BY_ID["swissdevjobs"]["title_format"]) == {"title": "Dev 80-100%", "company": "Acme AG"}
    assert term_match("we use Unity and C#.", ["c#"], words=True) and not term_match("community", ["unity"], words=True)
    assert title_parts("Acme: Senior Dev: Tools", "{company}: {title}") == {"company": "Acme", "title": "Senior Dev: Tools"}
    assert title_parts("X is hiring an Artist to work from Anywhere", BY_ID["workwithindies"]["title_format"]) == {"company": "X", "title": "Artist", "place": "Anywhere"}
    assert title_parts("Y is hiring Dev (Remote Job)", BY_ID["remotegamejobs"]["title_format"]) == {"company": "Y", "title": "Dev"} and title_parts("Dev", "{title}{title}") == {}
    assert age_of(date.today().isoformat() + "T10:00:00") == 0 and age_of("31.12.2000") > 3000 and age_of("Tue, 24 Nov 2020 10:33:49 +0100") > 2000 and age_of(1600000000000) == age_of(1600000000) > 1000
    assert age_of(None) == age_of("soon") == age_of([]) == 0
    # JSON: list and fields are guessed; own mapping wins
    src = {"name": "Own", "kind": "feed"}
    js = json.dumps({"meta": [{"a": 1}], "data": {"list": [{"jobTitle": "Dev", "employer": {"name": "A AG"}, "location": [{"city": "Bern"}, {"city": "Zug"}], "slug": "dev 1", "url": "/jobs/1", "created_at": 1600000000}]}})
    j = feed_parse(src, js, "https://example.com/api")[0]
    assert (j["title"], j["company"], j["place"], j["url"]) == ("Dev", "A AG", "Bern, Zug", "https://example.com/jobs/1") and j["age"] > 1000, j
    j = feed_parse({**src, "items": "data.list", "title": "slug", "company": "=Fix AG", "place": "location.1.city", "link": "https://example.com/j/{slug}"}, js, "https://example.com/api")[0]
    assert (j["title"], j["company"], j["place"], j["url"]) == ("dev 1", "Fix AG", "Zug", "https://example.com/j/dev%201"), j
    assert feed_parse({**src, "items": "nothing.here"}, js, "https://example.com") == [] == feed_parse(src, "[]", "https://example.com")
    r = feed_parse({**src, "remote": True}, json.dumps([{"legal": "x"}, {"position": "Dev", "company": "B", "location": "USA", "url": "https://example.com/2"}]), "https://example.com")[0]
    assert feed_parse(src, json.dumps([{"title": "Dev", "location": "Bonn", "remote": True, "url": "https://example.com/3"}]), "https://example.com")[0]["place"] == "Bonn, Remote"
    assert r["place"] == "Remote (USA)" and r["region"] == "Remote, USA", r
    # Atom, and a feed with links that must not be opened
    at = feed_parse(src, '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Dev</title><link rel="self" href="https://example.com/x"/><link href="https://example.com/1"/><summary>S</summary>'
                    '<updated>2026-11-01T00:00:00Z</updated></entry><entry><title>Bad</title><link href="file:///etc/passwd"/></entry><entry><title>Local</title><link href="http://127.0.0.1:8765/api/settings"/></entry></feed>', "https://example.com/feed")
    assert [(c["title"], c["url"], c["text"]) for c in at] == [("Dev", "https://example.com/1", "S")], at
    # web page: schema.org first, then links
    ld = '<html><script type="application/ld+json">{"@graph":[{"@type":"JobPosting","title":"Dev &amp; Ops","hiringOrganization":{"name":"C"},"jobLocation":{"address":{"addressLocality":"Basel"}},"url":"/jobs/5","datePosted":"2026-11-01","description":"<p>T</p>"}]}</script></html>'
    c = feed_parse(src, ld, "https://example.com/jobs")[0]
    assert (c["title"], c["company"], c["place"], c["url"], c["text"]) == ("Dev & Ops", "C", "Basel", "https://example.com/jobs/5", "T"), c
    pg = '<!doctype html><html><body><a href="/about">About us</a><a href="/jobs/unity-dev"><img><h3>Unity Dev</h3><span>Bern</span></a><a href="/jobs/unity-dev#x">again</a><a href="/open/7">Artist</a></body></html>'
    assert [(x["title"], x["company"]) for x in feed_parse({**src, "name": "Acme"}, pg, "https://example.com/careers")] == [("Unity Dev", "Acme")]
    assert [x["title"] for x in feed_parse({**src, "link_contains": "/open/"}, pg, "https://example.com/careers")] == ["Artist"]
    # checking an own source
    ok = clean_custom({"name": " My  board ", "url": "https://example.com/?q={term}", "on": 1, "match": "x", "evil": 1})
    assert ok == {"id": "own_my_board", "name": "My board", "url": "https://example.com/?q={term}", "on": True, "remote": False, "match": "title"}, ok
    for bad in ({"name": "", "url": "https://x.ch"}, {"name": "a", "url": "file:///etc/passwd"}, {"name": "a", "url": "x"}, {"name": "a", "url": "https://{term}/x"}):
        try:
            clean_custom(bad)
            raise SystemExit(f"accepted {bad}")
        except ValueError:
            pass
    assert not link_ok("http://localhost/x", "https://example.com") and not link_ok("http://[bad", "") and link_ok("http://127.0.0.1:1/x", "http://127.0.0.1:2/feed")
    try:
        get("file:///etc/hostname")
        raise SystemExit("file address read")
    except ValueError:
        pass
    # collect against a small local server: pages, matching, an error that ends nothing

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            body = {"jobs": []}
            if self.path.startswith("/bad"):
                self.send_error(500)
                return
            if self.path.startswith("/jobs/"):
                data = b"<html><body>" + b"The whole posting. " * 60 + b'<script>{"actualCity":"Bern"}</script></body></html>'
            else:
                if q.get("p", ["1"])[0] in ("1", "2"):
                    body["jobs"] = [{"title": f"{q['q'][0]} {q['p'][0]}", "url": f"/jobs/{q['p'][0]}"}, {"title": "Cook", "url": "/jobs/cook"}]
                data = json.dumps(body).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, got, lines = f"http://127.0.0.1:{srv.server_port}", [], []
    s = {"id": "own_x", "name": "X", "kind": "feed", "match": "title", "url": base + "/api?q={term}&p={page}"}
    note = collect(s, [("F", "Dev C#")], {"pages": 5}, got.append, lines.append, lambda: False, 0)
    assert [c["title"] for c in got] == ["Dev C# 1", "Dev C# 2"] and note == "X (2)" and len(lines) == 3, (got, note, lines)
    d = detail(got[0])
    assert "The whole posting" in d[0] and d[1]["place"] == "Bern", d
    pv = preview(s, [("F", "Dev")], {})
    assert (pv["count"], pv["matching"], pv["jobs"][0]["match"]) == (2, 1, True), pv
    assert "partly failed" in collect({**s, "url": base + "/bad"}, [("F", "Dev")], {}, got.append, lines.append, lambda: False, 0)
    assert [x["id"] for x in active({"sources": {"jobs_ch": True, "linkedin": False}, "custom_sources": [{"id": "own_a", "name": "A", "url": "https://a.ch", "on": True}, {"id": "own_b", "on": False}],
                                     "companies": ["lever:acme Acme", "nonsense"]})] == ["jobs_ch", "own_a", "lever:acme"]
    srv.shutdown()
    print("ok")
