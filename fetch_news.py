"""沖縄県・県内41市町村のお知らせを集めて index.html を作るスクリプト。

使い方:  python fetch_news.py
標準ライブラリだけで動きます（追加インストール不要）。
"""
import datetime as dt
import email.utils
import html
import json
import posixpath
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
ITEMS_FILE = DATA / "items.json"
STATUS_FILE = DATA / "status.json"
TEMPLATE = BASE / "template.html"
OUTPUT = BASE / "index.html"

JST = dt.timezone(dt.timedelta(hours=9))
KEEP_DAYS = 180          # 保存しておく期間
TIMEOUT = 30
UA = "Mozilla/5.0 (compatible; OkinawaGovNewsDigest/1.0)"
CTX = ssl.create_default_context()


# ---------- 取得 ----------
def http_get(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


# ---------- 文字列処理 ----------
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
TAGS = re.compile(r"<[^>]+>")


def clean(s):
    if not s:
        return ""
    s = html.unescape(TAGS.sub("", s))
    return re.sub(r"\s+", " ", s).strip()


def norm_link(link, base):
    if not link:
        return ""
    u = urllib.parse.urljoin(base, link.strip())
    p = urllib.parse.urlsplit(u)
    path = posixpath.normpath(p.path) if p.path else "/"
    if p.path.endswith("/") and not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, p.query, ""))


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    if m:
        return dt.datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=JST)
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
            if not m:
                return None
            d = dt.datetime(int(m[1]), int(m[2]), int(m[3]))
    if d.tzinfo is None:
        d = d.replace(tzinfo=JST)
    return d.astimezone(JST)


def decode_xml(raw):
    head = raw[:300].decode("ascii", "ignore")
    m = re.search(r'encoding=["\']([\w\-]+)', head)
    enc = (m.group(1) if m else "utf-8").lower()
    if enc in ("shift_jis", "shift-jis", "sjis", "x-sjis", "windows-31j"):
        enc = "cp932"
    if raw.startswith(b"\xef\xbb\xbf"):
        raw, enc = raw[3:], "utf-8"
    try:
        text = raw.decode(enc, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    return CTRL.sub("", text)


def local(tag):
    return tag.rsplit("}", 1)[-1].lower()


# ---------- 形式ごとのパーサ ----------
def parse_feed(raw, base):
    text = decode_xml(raw)
    items = []
    try:
        root = ET.fromstring(text)
        for el in root.iter():
            if local(el.tag) not in ("item", "entry"):
                continue
            f = {"title": "", "link": "", "date": "", "cat": ""}
            for c in el:
                n = local(c.tag)
                if n == "title":
                    f["title"] = "".join(c.itertext())
                elif n == "link":
                    href = c.get("href")
                    if href and c.get("rel", "alternate") == "alternate":
                        f["link"] = href
                    elif not href and (c.text or "").strip():
                        f["link"] = c.text
                elif n in ("pubdate", "date", "published", "issued") and not f["date"]:
                    f["date"] = c.text or ""
                elif n in ("updated", "modified") and not f["date"]:
                    f["date"] = c.text or ""
                elif n in ("category", "subject") and not f["cat"]:
                    f["cat"] = c.text or c.get("term") or ""
            if not f["link"]:
                f["link"] = el.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
            items.append(f)
    except ET.ParseError:
        # 壊れたXML向けの予備処理
        for block in re.findall(r"<(?:item|entry)\b.*?</(?:item|entry)>", text, re.S | re.I):
            def g(pat):
                m = re.search(pat, block, re.S | re.I)
                return m.group(1) if m else ""
            items.append({
                "title": re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", g(r"<title[^>]*>(.*?)</title>"), flags=re.S),
                "link": g(r"<link[^>]*>(.*?)</link>") or g(r"<link[^>]*href=[\"']([^\"']+)"),
                "date": g(r"<(?:pubDate|dc:date|published|updated)[^>]*>(.*?)</"),
                "cat": g(r"<(?:category|dc:subject)[^>]*>(.*?)</"),
            })
    return [{"title": clean(i["title"]), "link": norm_link(clean(i["link"]), base),
             "date": i["date"], "cat": clean(i["cat"])} for i in items]


def parse_update_json(raw, base):
    out = []
    for p in json.loads(raw.decode("utf-8-sig")):
        if p.get("is_category_index"):
            continue
        tags = [t.get("tag_name", "") for t in (p.get("tag") or [])]
        out.append({"title": clean(p.get("page_name")), "link": norm_link(p.get("url"), base),
                    "date": p.get("publish_datetime", ""), "cat": " ".join(t for t in tags if t)})
    return out[:60]


def parse_tarama_json(raw, base):
    return [{"title": clean(p.get("title")), "link": norm_link(p.get("permalink"), base),
             "date": p.get("date", ""), "cat": clean(p.get("category_name"))}
            for p in json.loads(raw.decode("utf-8-sig"))]


PARSERS = {"rss": parse_feed, "update_json": parse_update_json, "tarama_json": parse_tarama_json}


def fetch_source(src):
    year = dt.datetime.now(JST).year
    got, errors = [], []
    for url in src["urls"]:
        url = url.replace("{year}", str(year))
        try:
            raw = http_get(url)
            got += PARSERS[src["type"]](raw, url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {type(e).__name__}: {e}"[:300])
    got = [g for g in got if g["title"] and g["link"]]
    return src, got, errors


# ---------- メイン ----------
def main():
    DATA.mkdir(exist_ok=True)
    sources = json.loads((BASE / "sources.json").read_text(encoding="utf-8"))["sources"]
    try:
        store = {i["link"]: i for i in json.loads(ITEMS_FILE.read_text(encoding="utf-8"))}
    except (FileNotFoundError, json.JSONDecodeError):
        store = {}

    now = dt.datetime.now(JST)
    now_s = now.isoformat(timespec="seconds")
    status = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fetch_source, sources))

    for src, got, errors in results:
        new = 0
        for g in got:
            d = parse_date(g["date"])
            if d and d > now + dt.timedelta(days=2):   # 未来日付（イベント日など）は掲載日扱いしない
                d = None
            if d and d < now - dt.timedelta(days=KEEP_DAYS):   # 保存期間より古いものは扱わない
                continue
            old = store.get(g["link"])
            if old is None:
                new += 1
                store[g["link"]] = {
                    "muni": src["name"], "region": src["region"], "title": g["title"], "link": g["link"],
                    "cat": g["cat"], "date": (d or now).isoformat(timespec="seconds"),
                    "dated": bool(d), "first_seen": now_s,
                }
            else:
                old["title"] = g["title"]
                if d and not old.get("dated"):
                    old["date"], old["dated"] = d.isoformat(timespec="seconds"), True
        status.append({"name": src["name"], "region": src["region"], "home": src["home"],
                       "ok": not errors or bool(got), "count": len(got), "new": new, "errors": errors})
        mark = "OK " if not errors else ("一部" if got else "NG ")
        log(f"[{mark}] {src['name']}: {len(got)}件（新規 {new}）" + (f"  {errors[0]}" if errors else ""))

    cutoff = (now - dt.timedelta(days=KEEP_DAYS)).isoformat()
    items = sorted((i for i in store.values() if i["date"] >= cutoff),
                   key=lambda i: (i["date"], i["first_seen"]), reverse=True)

    ITEMS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=0), encoding="utf-8")
    STATUS_FILE.write_text(json.dumps({"updated": now_s, "sources": status}, ensure_ascii=False, indent=1),
                           encoding="utf-8")

    payload = json.dumps({"updated": now_s, "items": items, "sources": status}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    OUTPUT.write_text(page, encoding="utf-8")

    ng = [s["name"] for s in status if not s["ok"]]
    log(f"完了: {len(items)}件を {OUTPUT} に出力。" + (f" 取得失敗: {', '.join(ng)}" if ng else " 全団体取得成功。"))
    return 0


_LOG = []


def log(msg):
    """画面に出しつつ data/last_run.log にも残す（タスクスケジューラ実行時の確認用）。"""
    print(msg)
    _LOG.append(msg)
    try:
        (DATA / "last_run.log").write_text(
            f"実行: {dt.datetime.now(JST):%Y-%m-%d %H:%M}\n" + "\n".join(_LOG) + "\n", encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
