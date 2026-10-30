import re
import threading
from collections import Counter
from urllib.parse import quote_plus

from flask import Flask, jsonify, request
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

REGISTRATION = "SP23-BAI-044"
NEWS_SOURCE = "ProPakistani"
SEARCH_URL = "https://propakistani.pk/?s={}"

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


def find_first_result(driver, keyword):
    driver.get(SEARCH_URL.format(quote_plus(keyword)))
    selectors = [
        "article h2 a",
        "h2.entry-title a",
        "h3.entry-title a",
        "h2.post-title a",
        "h3.post-title a",
        "main h2 a",
        "main h3 a",
        "h2 a",
        "h3 a",
    ]

    def good(href):
        return (
            href
            and "propakistani.pk" in href
            and "?s=" not in href
            and "/category/" not in href
            and "/tag/" not in href
            and "/author/" not in href
            and "/page/" not in href
            and href.rstrip("/") != "https://propakistani.pk"
        )

    def locate(d):
        for sel in selectors:
            for el in d.find_elements(By.CSS_SELECTOR, sel):
                href = el.get_attribute("href")
                if good(href):
                    return href
        return False

    try:
        return WebDriverWait(driver, 20).until(locate)
    except Exception:
        return None


def get_article_text(driver, url):
    driver.get(url)
    WebDriverWait(driver, 20).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, "article p, .entry-content p, p")
    )
    for sel in [".entry-content p", ".post-content p", "article p", "main p", "p"]:
        paras = [p.text.strip() for p in driver.find_elements(By.CSS_SELECTOR, sel)]
        paras = [p for p in paras if len(p.split()) >= 8]
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
