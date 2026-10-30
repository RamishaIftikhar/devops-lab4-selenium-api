import json
import re
import threading
import time
from collections import Counter
from urllib.parse import quote_plus

from flask import Flask, jsonify, request
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

REGISTRATION = "SP23-BAI-044"
NEWS_SOURCE = "ProPakistani"
HOME = "https://propakistani.pk/"
REST_SEARCH = HOME + "wp-json/wp/v2/posts?search={}&per_page=5&_fields=link"
ARTICLE_RE = re.compile(r"^https?://(www\.)?propakistani\.pk/\d{4}/\d{2}/\d{2}/[^/?#]+/?$")

app = Flask(__name__)
driver_lock = threading.Lock()  # one Chrome at a time (small server)

STOPWORDS = set(
    "a an the and or but if then else of to in on at by for with from as is are was were be been being "
    "it its this that these those he she they we you i his her their our your not no yes do does did "
    "have has had will would can could should may might also than so such into over after before about "
    "up out more most said says say".split()
)


def make_driver():
    opts = Options()
    opts.binary_location = "/usr/bin/google-chrome"
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-software-rasterizer")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--no-zygote")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--remote-debugging-port=9222")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    opts.page_load_strategy = "eager"
    service = Service("/usr/local/bin/chromedriver")
    d = webdriver.Chrome(service=service, options=opts)
    d.set_page_load_timeout(40)
    return d


def article_links(driver, scope="a[href]"):
    out = []
    for el in driver.find_elements(By.CSS_SELECTOR, scope):
        try:
            href = el.get_attribute("href")
        except Exception:
            continue
        if href and ARTICLE_RE.match(href) and href not in out:
            out.append(href)
    return out


def search_via_rest(driver, keyword):
    """Strategy 1: the site's WordPress search endpoint, opened in Chrome."""
    driver.get(REST_SEARCH.format(quote_plus(keyword)))
    body = driver.find_element(By.TAG_NAME, "body").text.strip()
    data = json.loads(body)
    for post in data:
        link = post.get("link", "")
        if ARTICLE_RE.match(link):
            return link
    return None


def search_via_ui(driver, keyword):
    """Strategy 2: type the keyword into the site's own search box."""
    driver.get(HOME)
    time.sleep(2)
    baseline = set(article_links(driver))

    input_css = "input[type='search'], input[name='s'], input[placeholder*='earch' i]"

    def visible_input():
        for el in driver.find_elements(By.CSS_SELECTOR, input_css):
            try:
                if el.is_displayed() and el.is_enabled():
                    return el
            except Exception:
                pass
        return None

    box = visible_input()
    if not box:
        togglers = driver.find_elements(
            By.CSS_SELECTOR,
            "[class*='search' i], [id*='search' i], button[aria-label*='earch' i], a[href='#search']",
        )
        for t in togglers[:12]:
            try:
                driver.execute_script("arguments[0].click();", t)
                time.sleep(0.7)
                box = visible_input()
                if box:
                    break
            except Exception:
                continue
    if not box:
        return None

    box.clear()
    box.send_keys(keyword)
    box.send_keys(Keys.ENTER)

    end = time.time() + 12
    while time.time() < end:
        time.sleep(1)
        if "not found" in (driver.title or "").lower():
            return None
        fresh = [l for l in article_links(driver) if l not in baseline]
        if fresh:
            return fresh[0]
    return None


def search_via_tag(driver, keyword):
    """Strategy 3: the tag archive page for the keyword."""
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    if not slug:
        return None
    driver.get(f"{HOME}tag/{slug}/")
    if "not found" in (driver.title or "").lower():
        return None
    links = article_links(driver, "main a[href], #content a[href], article a[href]")
    return links[0] if links else None


def find_first_result(driver, keyword):
    for strategy in (search_via_rest, search_via_ui, search_via_tag):
        try:
            url = strategy(driver, keyword)
            if url:
                return url
        except Exception:
            continue
    return None


def get_article_text(driver, url):
    driver.get(url)
    WebDriverWait(driver, 20).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, "article p, .entry-content p, p")
    )
    for sel in [".entry-content p", ".post-content p", "article p", "main p", "p"]:
        paras = [p.text.strip() for p in driver.find_elements(By.CSS_SELECTOR, sel)]
        paras = [
            p for p in paras
            if len(p.split()) >= 8 and "stay connected with propakistani" not in p.lower()
        ]
        if len(paras) >= 2:
            return " ".join(paras)
    return ""


def summarize(text, max_sentences=4):
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if len(s.split()) >= 6]
    if len(sentences) <= max_sentences:
        return " ".join(sentences)
    words = re.findall(r"[a-zA-Z']+", text.lower())
    freq = Counter(w for w in words if w not in STOPWORDS and len(w) > 2)
    scored = []
    for i, s in enumerate(sentences):
        toks = [w for w in re.findall(r"[a-zA-Z']+", s.lower()) if w not in STOPWORDS]
        if not toks:
            continue
        score = sum(freq[w] for w in toks) / (len(toks) ** 0.8)
        if i < 3:
            score *= 1.2  # lead sentences matter in news
        scored.append((score, i, s))
    top = sorted(scored, reverse=True)[:max_sentences]
    top.sort(key=lambda x: x[1])
    return " ".join(s for _, _, s in top)


@app.route("/")
def home():
    return (
        f"<h1>Registration: {REGISTRATION}</h1>"
        f"<p>News source: {NEWS_SOURCE}</p>"
        "<p>Use <code>/get?keyword=your_keyword</code></p>"
    )


@app.route("/get")
def get_news():
    keyword = request.args.get("keyword", "").strip()
    result = {
        "registration": REGISTRATION,
        "newssource": NEWS_SOURCE,
        "keyword": keyword,
        "url": "",
        "summary": "",
    }
    if not keyword:
        result["summary"] = "Error: keyword query parameter is required"
        return jsonify(result), 400

    with driver_lock:
        driver = None
        try:
            driver = make_driver()
            url = find_first_result(driver, keyword)
            if not url:
                result["summary"] = "No search results found"
                return jsonify(result), 404
            result["url"] = url
            text = get_article_text(driver, url)
            result["summary"] = summarize(text) or "Could not extract article text"
        except Exception as e:
            result["summary"] = f"Error: {type(e).__name__}: {str(e)[:200]}"
            return jsonify(result), 500
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000, threaded=True)
