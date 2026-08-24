# FraudLens — FastAPI + HTML/CSS/JS build

## Structure
```
fraudlens_build/
├── backend/
│   ├── main.py                    FastAPI app — /analyze, /report, /health, serves ../static
│   ├── pipeline.py                All ML/verification/LLM logic (Streamlit-free)
│   ├── requirements.txt
│   ├── .env                       Real API keys — DO NOT COMMIT
│   ├── .env.example               Safe template to commit instead
│   ├── best_model.pkl
│   ├── tfidf_vectorizer.pkl
│   ├── feature_names.pkl
│   ├── reported_postings.json
│   └── reported_postings_master.csv
└── static/
    ├── index.html
    ├── style.css
    ├── script.js
    └── assets/
        ├── hero-bg.mp4             ← you provide this (slow-motion loop)
        └── hero-fallback.jpg       ← poster frame shown before video loads / on reduced-motion
```

## Run locally
```bash
cd backend
pip install -r requirements.txt --break-system-packages   # or use a venv, preferred
uvicorn main:app --reload --port 8000
```
Then open `http://localhost:8000` — FastAPI serves the frontend directly, no separate dev server needed.

## Before you deploy anywhere public
1. **Rotate all four API keys** in `.env` (Groq, OpenCorporates, both OpenRegistry tokens, Tavily) if this project has ever been shared, screenshotted, or pushed to a repo with the old hardcoded keys still in `app.py`.
2. Add `.env`, `*.pkl`, and `reported_postings*.{json,csv}` to `.gitignore` before your first commit.
3. In `main.py`, change `allow_origins=["*"]` to your actual deployed frontend domain.

## Still needed from you
- `assets/hero-bg.mp4` — the slow-motion background video for the hero. Keep it small
  (aim under ~4–5MB, H.264) so the hero doesn't feel slow to load.
- `assets/hero-fallback.jpg` — a static poster frame (first frame of the video works fine)
  shown while the video loads and for anyone with `prefers-reduced-motion` set.

Until those exist, the hero falls back to a flat background color (`--bg`) — the page still
works, it just won't have the video effect yet.

## What changed from the original Streamlit app
- All emoji-as-status-indicator UI (🔴🟡🟢 etc.) replaced with actual color-coded badges,
  since emoji rendering is inconsistent across OS/browsers and isn't accessible to screen readers.
- `save_reported_posting` keeps the Set 16 fix (overwrite master CSV from the full JSON history
  each time) rather than the older `pd.concat`-based version in `app.py`, which could duplicate
  rows across runs.
- Layout direction locked to flat, high-contrast, sharp-corner blocks for the actual results
  screen (no glassmorphism) — the glass/floating-label treatment is hero-only, matching the
  trust-sensitive nature of what this tool tells someone.
