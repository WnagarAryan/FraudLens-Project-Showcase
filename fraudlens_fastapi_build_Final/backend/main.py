# ==================== main.py ====================
# FastAPI wrapper around pipeline.py. Serves the JSON API the HTML/CSS/JS
# frontend calls, plus the static frontend files themselves.

from dotenv import load_dotenv
load_dotenv()  # must run before pipeline.py reads os.getenv() for API keys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional

import pipeline

app = FastAPI(title="FraudLens API")

# Dev-friendly CORS. Tighten allow_origins to your real domain before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    pipeline.load_models()


class AnalyzeRequest(BaseModel):
    job_title: Optional[str] = ""
    company_name: Optional[str] = ""
    job_description: str = Field(..., min_length=1)
    url: Optional[str] = None
    has_logo: bool = False
    has_profile: bool = False
    has_questions: bool = False


class ReportRequest(BaseModel):
    job_title: Optional[str] = ""
    company_name: Optional[str] = ""
    job_description: Optional[str] = ""
    content_risk: float
    verification_label: str
    keyword_matches: dict = {}


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if not req.job_description.strip():
        raise HTTPException(status_code=400, detail="job_description is required")
    try:
        result = pipeline.analyze_posting(
            job_title=req.job_title,
            company_name=req.company_name,
            job_description=req.job_description,
            url=req.url,
            has_logo=req.has_logo,
            has_profile=req.has_profile,
            has_questions=req.has_questions,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@app.post("/report")
def report(req: ReportRequest):
    try:
        total = pipeline.save_reported_posting(
            req.job_title, req.company_name, req.job_description,
            req.content_risk, req.verification_label, req.keyword_matches
        )
        return {"status": "ok", "total_reports": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Report failed: {str(e)}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/reports/stats")
def report_stats():
    try:
        return pipeline.get_report_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not load report stats: {str(e)}")


# Serve the frontend (../static) at the site root. Mounted last so /analyze,
# /report, /health above take priority over static file matching.
app.mount("/", StaticFiles(directory="../static", html=True), name="static")
