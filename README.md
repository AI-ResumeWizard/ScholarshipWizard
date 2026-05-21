# Scholarship Scraper
Weekly email digest of AI/graduate scholarships matched to your profile.

## Profile (edit scraper.py to update)
- Wake Forest MS AI Strategy (enrolled)
- Age 57+ / adult learner
- Jewish identity
- Michigan resident
- Ohio State undergrad alum

## Setup

### 1. Gmail App Password
You need a Gmail App Password (NOT your real Gmail password):
1. Go to myaccount.google.com → Security → 2-Step Verification → App passwords
2. Create one named "scholarship-scraper"
3. Copy the 16-character password

### 2. Push to GitHub
```bash
git init
git add .
git commit -m "initial scholarship scraper"
git remote add origin https://github.com/YOUR_USERNAME/scholarship-scraper.git
git push -u origin main
```

### 3. Deploy on Render
1. Go to render.com → New → Cron Job
2. Connect your GitHub repo
3. Render will detect render.yaml automatically
4. Go to Environment → Add these 3 variables:
   - EMAIL_FROM: yourgmail@gmail.com
   - EMAIL_PASSWORD: (the 16-char app password from step 1)
   - EMAIL_TO: where you want the digest sent

### 4. Test it
In Render dashboard → Manual Run to trigger immediately and check your inbox.

## Customizing
- **Add sources**: Add entries to the `SOURCES` list in scraper.py
- **Add curated scholarships**: Add entries to the `CURATED` list
- **Change keywords**: Edit `PROFILE["keywords"]`
- **Change schedule**: Edit the cron expression in render.yaml
  - `0 8 * * 1` = Every Monday 8am UTC
  - `0 8 * * 1,4` = Monday + Thursday

## How it works
1. Scrapes 4 scholarship aggregator sites weekly
2. Filters listings by your profile keywords
3. Deduplicates against previously seen scholarships (seen_scholarships.json)
4. Always includes your 10 curated high-fit scholarships
5. Emails you an HTML digest with apply links
