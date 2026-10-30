# CSC418 Lab Assignment 4 - Selenium News Summarizer API

- Registration: SP23-BAI-044
- News source: ProPakistani (44 % 20 + 1 = 5)
- Language: Python (Selenium + Flask)

Dockerized Selenium (Chrome + ChromeDriver) app. It searches ProPakistani for a keyword,
opens the first result, summarizes the article and returns JSON on port 7000.

## Run
```
docker run -d -p 7000:7000 ramishaiftikhar/propakistani-api:v1
curl "http://localhost:7000/get?keyword=google"
```
