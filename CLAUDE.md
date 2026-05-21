# Scholarship Scraper — Claude Code Instructions

## Project Overview
Weekly scholarship email digest scraper for a specific user profile.
Scrapes AI/graduate scholarship aggregator sites, filters by profile, and
emails an HTML digest every Monday morning via Gmail SMTP.

## Owner Profile (never change these without asking)
- Program: MS in AI Strategy at Wake Forest University (currently enrolled)
- Age: 57+, non-traditional adult learner
- Background: Jewish
- Residence: Michigan
- Undergrad: Ohio State University (alumnus)
- Focus: Artificial intelligence, technology strategy, business

## Stack
- Language: Python 3
- Hosting: Render (cron job, runs every Monday 8am UTC)
- Repo: GitHub (auto-deploys to Render on push)
- Output: HTML email digest via Gmail SMTP
- Key files:
  - `scraper.py` — main script (scraping, filtering, email)
  - `requirements.txt` — pip dependencies
  - `render.yaml` — Render cron job config
  - `seen_scholarships.json` — dedup cache (auto-generated, do not delete)

## Environment Variables (set in Render dashboard, never hardcode)

### Email (required)
- `EMAIL_FROM` — sender Gmail address
- `EMAIL_PASSWORD` — Gmail App Password (16 chars)
- `EMAIL_TO` — recipient email address

### CareerOneStop API (optional — free, US Dept of Labor)
- `CAREERONESTOP_API_KEY` — API key from registration
- `CAREERONESTOP_USER_ID` — user ID assigned at registration
- Register: https://www.careeronestop.org/Developers/WebAPI/registration.aspx
- If either var is missing the integration is silently skipped

### ScholarshipAPI.com (optional — free tier)
- `SCHOLARSHIPAPI_KEY` — API key from registration
- Register: https://scholarshipapi.com/
- If var is missing the integration is silently skipped

## How to Make Changes
1. Edit the relevant file(s)
2. Test locally if possible: `python scraper.py` (requires env vars set)
3. Always run: `git add . && git commit -m "description of change" && git push`
4. Render auto-deploys on push — no manual deploy needed

## Common Tasks

### Add a new scholarship source
Add an entry to the `SOURCES` list in `scraper.py`:
```python
{
    "name": "Source Name",
    "url": "https://...",
    "item_selector": "article, .listing",
    "title_selector": "h2, h3, a",
    "link_selector": "a",
    "amount_selector": ".amount",   # or None
    "deadline_selector": ".deadline", # or None
}
```
Then: `git add . && git commit -m "add new source: Source Name" && git push`

### Add a curated scholarship
Add an entry to the `CURATED` list in `scraper.py`:
```python
{
    "title": "Scholarship Name",
    "provider": "Organization",
    "amount": "$X,000",
    "deadline": "Month DD annually",
    "url": "https://...",
    "why": "One sentence on why this matches the owner profile",
}
```
Then: `git add . && git commit -m "add curated: Scholarship Name" && git push`

### Update profile keywords
Edit `PROFILE["keywords"]` in `scraper.py`.
Then: `git add . && git commit -m "update profile keywords" && git push`

### Change email schedule
Edit the `schedule` field in `render.yaml`.
Cron format: `"0 8 * * 1"` = Monday 8am UTC
Then: `git add . && git commit -m "update schedule" && git push`

### Add a new pip dependency
Add to `requirements.txt`, then:
`git add . && git commit -m "add dependency: package-name" && git push`

## Git Commit Convention
Use clear, short messages:
- `add source: Fastweb graduate`
- `add curated: NSF GRFP fellowship`
- `fix: email formatting on mobile`
- `update: expand Jewish foundation scholarships`
- `update: add Michigan-specific sources`

## Do Not
- Never hardcode email credentials or passwords
- Never delete `seen_scholarships.json` (it prevents duplicate emails)
- Never change the owner profile section without confirming with the user
- Never change `EMAIL_FROM`, `EMAIL_PASSWORD`, `EMAIL_TO` — these live in Render only

## Local Development
```bash
cd ~/Desktop/Scholarship
pip install -r requirements.txt
export EMAIL_FROM="you@gmail.com"
export EMAIL_PASSWORD="your-app-password"
export EMAIL_TO="you@gmail.com"
python scraper.py
```

## Render Dashboard
- URL: render.com
- Service: scholarship-scraper (Cron Job)
- Manual test: click "Manual Run" in the dashboard
- Logs: click "Logs" tab after triggering a run
