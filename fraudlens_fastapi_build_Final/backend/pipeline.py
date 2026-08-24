# ==================== pipeline.py ====================
# Framework-agnostic core logic extracted from the original Streamlit app.py.
# No Streamlit imports here — this module is called by FastAPI (main.py).

import os
import re
import ast
import json
import numpy as np
import pandas as pd
import joblib
import shap
import requests
import trafilatura
import nltk
from nltk.corpus import stopwords
from scipy.sparse import hstack
from typing import Dict, Tuple, Optional, List
from datetime import datetime
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from langchain_groq import ChatGroq

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

nltk.download("stopwords", quiet=True)
STOP_WORDS = set(stopwords.words("english"))

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENCORPORATES_API_TOKEN = os.getenv("OPENCORPORATES_API_TOKEN")

# ==================== Scam Keyword Dictionary ====================

SCAM_KEYWORDS = {
    "payment_requests": ["registration fee", "processing fee", "training fee", "deposit required",
        "pay to apply", "send your bank details", "western union", "money gram",
        "activation fee", "equipment fee", "upi payment", "google pay", "phonepe"],

    "urgency_pressure": ["urgent hiring", "limited seats", "act now", "apply immediately",
        "hurry up", "only few slots left", "apply within 24 hours"],

    "vague_or_suspicious_contact": ["whatsapp only", "click here to apply", "contact us on telegram",
        "no interview required", "no experience needed high salary", "telegram", "no phone calls"],

    "unrealistic_offers": ["work from home earn", "earn from home no investment", "guaranteed income",
        "easy money", "get rich quick", "make money fast"]
}

# ==================== Model Loading (call once at app startup) ====================

_best_model = None
_tfidf = None
_shap_explainer = None
_feature_names = None


def load_models():
    """Loads model + vectorizer + SHAP explainer once. Call from FastAPI startup event."""
    global _best_model, _tfidf, _shap_explainer, _feature_names

    _best_model = joblib.load(os.path.join(BASE_DIR, "best_model.pkl"))
    _tfidf = joblib.load(os.path.join(BASE_DIR, "tfidf_vectorizer.pkl"))

    n_features = _tfidf.get_feature_names_out().shape[0] + 6
    _shap_explainer = shap.LinearExplainer(_best_model, np.zeros((1, n_features)))

    _feature_names = _tfidf.get_feature_names_out().tolist() + [
        "telecommuting", "has_company_logo", "has_questions",
        "salary_range_missing", "company_profile_missing", "scam_keyword_flag"
    ]
    return _best_model, _tfidf, _shap_explainer, _feature_names


def get_models():
    """Returns the loaded model objects, loading them first if needed (e.g. in tests)."""
    if _best_model is None:
        return load_models()
    return _best_model, _tfidf, _shap_explainer, _feature_names


# ==================== Text Processing ====================

def clean_text(text: str) -> str:
    text = re.sub(r"<.*?>", " ", text).lower()
    text = re.sub(r"[^a-z\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(w for w in text.split() if w not in STOP_WORDS)


def extract_text_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 403:
            return None, "This site blocks automated access (403 Forbidden) — likely bot protection."
        if response.status_code != 200:
            return None, f"Could not reach that URL (status {response.status_code})."
        text = trafilatura.extract(response.text)
        if not text or len(text.strip()) < 30:
            return None, "Couldn't extract readable text from that page."
        return text, None
    except Exception as e:
        return None, f"Error extracting from URL: {str(e)}"


def extract_domain_from_text(text: str) -> Optional[str]:
    matches = re.findall(r'(https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9-]+\.(?:com|net|org|io|co|in)[^\s,.]*)', text)
    if not matches:
        return None
    url = re.sub(r'^https?://|^www\.', '', matches[0])
    return url.split('/')[0].rstrip('.,')


def scan_for_scam_keywords(text: str, keyword_dict: Dict = SCAM_KEYWORDS) -> Dict:
    text = text.lower()
    return {cat: found for cat, kws in keyword_dict.items() if (found := [k for k in kws if k in text])}


def get_top_shap_features(shap_values_row, names: List[str], top_n: int = 8) -> List[Dict]:
    top_idx = np.argsort(np.abs(shap_values_row))[::-1][:top_n]
    return [{"feature": names[i], "impact": round(float(shap_values_row[i]), 4),
              "direction": "toward FAKE" if shap_values_row[i] > 0 else "toward REAL"} for i in top_idx]


# ==================== Confidence Score ====================

def calculate_confidence(content_risk, keyword_matches, verification_label, top_shap_features) -> float:
    confidence = 50
    risk_distance = abs(content_risk - 50)
    confidence += risk_distance * 0.3
    keyword_scam = len(keyword_matches) > 0
    model_scam = content_risk > 50
    if keyword_scam == model_scam:
        confidence += 20
    else:
        confidence -= 15
    if model_scam and verification_label == "Unverified":
        confidence += 10
    elif not model_scam and verification_label == "Verified":
        confidence += 10
    else:
        confidence -= 5
    strong_shap = sum(1 for f in top_shap_features if abs(f["impact"]) > 0.3)
    confidence += strong_shap * 3
    keyword_count = sum(len(matches) for matches in keyword_matches.values())
    if keyword_count >= 3:
        confidence += 5
    elif keyword_count >= 5:
        confidence += 10
    return max(0, min(100, round(confidence, 1)))


def get_confidence_label(score: float) -> str:
    if score >= 70:
        return "High Confidence"
    elif score >= 50:
        return "Moderate Confidence"
    return "Low Confidence"


# ==================== Similar Scam Detection ====================

def check_against_past_reports(job_description: str, threshold: float = 0.7) -> Optional[Dict]:
    master_path = os.path.join(BASE_DIR, "reported_postings_master.csv")
    if not os.path.exists(master_path):
        return None
    df = pd.read_csv(master_path)
    if len(df) == 0:
        return None
    past_texts = df["job_description"].fillna("").tolist()
    past_texts.append(job_description)
    vectorizer = TfidfVectorizer(max_features=1000, stop_words="english")
    vectors = vectorizer.fit_transform(past_texts)
    current_vec = vectors[-1]
    past_vecs = vectors[:-1]
    similarities = cosine_similarity(current_vec, past_vecs)[0]
    best_match_idx = similarities.argmax()
    best_score = similarities[best_match_idx]
    if best_score >= threshold:
        return {
            "similar_count": int((similarities >= threshold).sum()),
            "best_match": df.iloc[best_match_idx].to_dict(),
            "similarity": float(best_score),
            "total_reports": len(df)
        }
    return None


# ==================== LLM Explanation ====================

def get_llm_explanation(top_features, keyword_matches=None, content_risk=None, risk_label=None,
                         verification_label=None, job_title=None, company_name=None) -> str:
    feature_lines = []
    for f in top_features[:6]:
        marker = "FLAG" if f["direction"] == "toward FAKE" else "OK"
        feature_lines.append(f"[{marker}] {f['feature']}: {f['direction']}")
    feature_text = "\n".join(feature_lines)

    if keyword_matches:
        keyword_lines = [f"- {cat.replace('_', ' ').title()}: {', '.join(matches)}"
                          for cat, matches in keyword_matches.items()]
        keyword_text = "\n".join(keyword_lines)
    else:
        keyword_text = "No scam keywords detected"

    scam_signals = []
    if keyword_matches:
        scam_signals.append("scam keywords found")
    if "company_profile_missing" in [f["feature"] for f in top_features if f["direction"] == "toward FAKE"]:
        scam_signals.append("missing company profile")
    if verification_label in ["Unverified", "Partially Verified"]:
        scam_signals.append("company could not be verified")
    if risk_label in ["High", "Medium"]:
        scam_signals.append("elevated risk score")

    scam_confidence = "HIGH" if len(scam_signals) >= 3 else "MODERATE" if len(scam_signals) >= 2 else "LOW"

    prompt = f"""You are a job scam detection expert. Your job is to help a job seeker understand if a job posting is a SCAM or LEGITIMATE.

**Job Title:** {job_title or "Not provided"}
**Company:** {company_name or "Not provided"}

**Analysis Results:**
- Risk Level: {risk_label} ({content_risk}%)
- Verification Status: {verification_label}
- Scam Confidence: {scam_confidence}

**RED FLAGS DETECTED:**
{keyword_text}

**MODEL SIGNALS:**
{feature_text}

**SCAM DETECTION RULES:**

A job posting is likely a SCAM if it has ANY of these:
1. Asks for money — registration fee, processing fee, training fee, deposit, UPI payment, Google Pay, PhonePe
2. Suspicious contact — WhatsApp only, Telegram, no phone calls, no official email
3. Urgency pressure — urgent hiring, only few seats left, act now, apply within 24 hours
4. Missing company info — no company profile, no logo, no official website
5. Unrealistic offers — guaranteed income, earn from home no investment, get rich quick

A job posting is likely LEGITIMATE if it has:
1. Official company website and email domain
2. Clear job responsibilities and requirements
3. No mention of any fees
4. Standard application process (not WhatsApp/Telegram)
5. Realistic salary expectations

**YOUR TASK:**
Write a clear, specific explanation (3-4 sentences) that tells the job seeker:
1. IS THIS A SCAM? Yes/No/Maybe
2. WHY? List the SPECIFIC red flags found
3. WHAT TO DO NEXT? Give actionable advice

**RULES:**
- Be SPECIFIC - mention actual red flags found (e.g., "asks for a registration fee")
- Be HONEST - if the posting looks legitimate, say so clearly
- Be HELPFUL - tell the job seeker what to do next
- Never mention "SHAP", "model", "features", or technical terms
- Use simple, clear language a job seeker can understand

**YOUR EXPLANATION (3-4 sentences):**"""

    if not GROQ_API_KEY:
        return "(Explanation unavailable: GROQ_API_KEY not configured)"

    try:
        llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0.3, groq_api_key=GROQ_API_KEY)
        response = llm.invoke(prompt)
        return response.content
    except Exception as e:
        return f"(Explanation unavailable: {str(e)})"


# ==================== Company Verification ====================

def check_company_registry(company_name: str) -> Dict:
    if not OPENCORPORATES_API_TOKEN:
        return {"found": None, "error": "OPENCORPORATES_API_TOKEN not configured"}
    try:
        r = requests.get("https://api.opencorporates.com/v0.4/companies/search",
                          params={"q": company_name, "api_token": OPENCORPORATES_API_TOKEN}, timeout=10)
        if r.status_code == 200:
            companies = r.json().get("results", {}).get("companies", [])
            return {"found": len(companies) > 0,
                    "top_match": companies[0]["company"]["name"] if companies else None,
                    "total_results": len(companies)}
        return {"found": None, "error": f"Status {r.status_code}"}
    except Exception as e:
        return {"found": None, "error": str(e)}


def get_verification_status(has_logo: bool, has_profile: bool, domain: Optional[str],
                             company_name: Optional[str]) -> Tuple[float, str, List[str]]:
    score, max_score, reasons = 0, 0, []
    max_score += 40
    if company_name and company_name.strip():
        reg = check_company_registry(company_name.strip())
        if reg.get("found") is True:
            score += 40
            reasons.append(f"Found in company registry (matched: {reg.get('top_match')})")
        elif reg.get("found") is False:
            reasons.append("Not found in company registry")
        else:
            reasons.append(f"Registry check unavailable ({reg.get('error', 'unknown error')})")
    else:
        reasons.append("No company name provided — registry check skipped")

    max_score += 20
    score += 20 if has_logo else 0
    reasons.append("Company logo provided" if has_logo else "No company logo provided")

    max_score += 20
    score += 20 if has_profile else 0
    reasons.append("Company profile provided" if has_profile else "No company profile provided")

    reasons.append(f"Link detected: {domain}" if domain else "No link/website detected in posting")

    final_score = round((score / max_score) * 100, 1) if max_score else 0
    label = "Verified" if final_score >= 70 else "Partially Verified" if final_score >= 40 else "Unverified"
    return final_score, label, reasons


# ==================== Risk Breakdown (rule-based detail cards) ====================

def get_risk_breakdown(keyword_matches, top_shap_features, verification_label, has_logo, has_profile) -> List[Dict]:
    risk_items = []

    if "payment_requests" in keyword_matches:
        for match in keyword_matches["payment_requests"]:
            risk_items.append({"category": "Payment Request", "severity": "HIGH",
                "detail": f"Asks for money: '{match}'",
                "advice": "Legitimate companies never ask for money during recruitment"})

    if "urgency_pressure" in keyword_matches:
        for match in keyword_matches["urgency_pressure"]:
            risk_items.append({"category": "Urgency Pressure", "severity": "MEDIUM",
                "detail": f"Creates false urgency: '{match}'",
                "advice": "Scammers rush you to stop you from thinking clearly"})

    if "vague_or_suspicious_contact" in keyword_matches:
        for match in keyword_matches["vague_or_suspicious_contact"]:
            risk_items.append({"category": "Suspicious Contact", "severity": "HIGH",
                "detail": f"Suspicious contact method: '{match}'",
                "advice": "Legitimate companies use official email, not WhatsApp/Telegram"})

    if "unrealistic_offers" in keyword_matches:
        for match in keyword_matches["unrealistic_offers"]:
            risk_items.append({"category": "Unrealistic Offer", "severity": "MEDIUM",
                "detail": f"Unrealistic promise: '{match}'",
                "advice": "If it sounds too good to be true, it probably is"})

    if not has_profile:
        risk_items.append({"category": "Missing Company Profile", "severity": "HIGH",
            "detail": "Company profile/description is missing",
            "advice": "Real companies usually provide a detailed company profile"})

    if not has_logo:
        risk_items.append({"category": "Missing Company Logo", "severity": "MEDIUM",
            "detail": "Company logo is not provided",
            "advice": "Legitimate companies typically include their logo in job postings"})

    if verification_label == "Unverified":
        risk_items.append({"category": "Company Unverified", "severity": "HIGH",
            "detail": "Company could not be verified in registry",
            "advice": "Check if the company is legitimate before applying"})
    elif verification_label == "Partially Verified":
        risk_items.append({"category": "Company Partially Verified", "severity": "MEDIUM",
            "detail": "Company could not be fully verified",
            "advice": "Check the company's official website before applying"})

    return risk_items


# ==================== Reporting ====================

def save_reported_posting(job_title, company_name, job_description, content_risk,
                           verification_label, keyword_matches) -> int:
    entry = {
        "timestamp": datetime.now().isoformat(),
        "job_title": job_title,
        "company_name": company_name,
        "job_description": (job_description or "")[:500],
        "content_risk_score": content_risk,
        "verification_label": verification_label,
        "keyword_matches": str(keyword_matches)
    }

    json_path = os.path.join(BASE_DIR, "reported_postings.json")
    reports = json.load(open(json_path)) if os.path.exists(json_path) else []
    reports.append(entry)
    json.dump(reports, open(json_path, "w"), indent=2)

    df = pd.DataFrame(reports)
    df.to_csv(os.path.join(BASE_DIR, "reported_postings.csv"), index=False)
    # Overwrite master with full current set — avoids duplicate accumulation
    df.to_csv(os.path.join(BASE_DIR, "reported_postings_master.csv"), index=False)

    return len(reports)


def get_report_stats() -> Dict:
    """Aggregate stats over reported_postings.json for the public Reports/Transparency page.
    No individual posting text or company names are exposed — counts and category
    breakdowns only."""
    json_path = os.path.join(BASE_DIR, "reported_postings.json")
    reports = json.load(open(json_path)) if os.path.exists(json_path) else []

    total = len(reports)
    verification_breakdown: Dict[str, int] = {}
    keyword_category_counts: Dict[str, int] = {}
    risk_bucket_counts = {"High": 0, "Medium": 0, "Low": 0}

    for r in reports:
        label = r.get("verification_label", "Unknown")
        verification_breakdown[label] = verification_breakdown.get(label, 0) + 1

        score = r.get("content_risk_score", 0) or 0
        if score >= 66:
            risk_bucket_counts["High"] += 1
        elif score >= 33:
            risk_bucket_counts["Medium"] += 1
        else:
            risk_bucket_counts["Low"] += 1

        raw_km = r.get("keyword_matches", "{}")
        try:
            km = ast.literal_eval(raw_km) if isinstance(raw_km, str) else raw_km
        except Exception:
            km = {}
        if isinstance(km, dict):
            for cat in km.keys():
                keyword_category_counts[cat] = keyword_category_counts.get(cat, 0) + 1

    latest_timestamp = reports[-1]["timestamp"] if reports else None

    return {
        "total_reports": total,
        "verification_breakdown": verification_breakdown,
        "risk_bucket_counts": risk_bucket_counts,
        "keyword_category_counts": keyword_category_counts,
        "latest_timestamp": latest_timestamp,
    }


# ==================== Main Analysis Function ====================

def analyze_posting(job_title: str, company_name: str, job_description: str,
                     url: Optional[str], has_logo: bool, has_profile: bool, has_questions: bool) -> Dict:
    """Runs the full FraudLens pipeline on a single job posting and returns the result dict."""
    best_model, tfidf, shap_explainer, feature_names = get_models()

    url_error = None
    if url and url.strip():
        extracted_text, url_error = extract_text_from_url(url.strip())
        if not url_error and extracted_text:
            job_description = f"{job_description}\n\n{extracted_text}"

    full_text = f"{job_title or ''} {job_description or ''}"
    X_text = tfidf.transform([clean_text(full_text)])
    keyword_matches = scan_for_scam_keywords(full_text)
    metadata = np.array([[0, int(has_logo), int(has_questions), 1, int(not has_profile)]])
    keyword_feature = np.array([[1 if keyword_matches else 0]])
    X_final = hstack([X_text, metadata, keyword_feature])

    shap_values_live = shap_explainer.shap_values(X_final.toarray())[0]
    top_shap_features = get_top_shap_features(shap_values_live, feature_names)

    raw_score = best_model.decision_function(X_final)[0]
    content_risk = round((1 / (1 + np.exp(-raw_score))) * 100, 1)
    risk_label = "High" if content_risk > 60 else "Medium" if content_risk > 30 else "Low"

    domain = extract_domain_from_text(job_description if not url else url)
    verification_score, verification_label, verification_reasons = get_verification_status(
        has_logo, has_profile, domain, company_name)

    confidence_score = calculate_confidence(content_risk, keyword_matches, verification_label, top_shap_features)
    confidence_label = get_confidence_label(confidence_score)

    similar_scam = check_against_past_reports(job_description)

    llm_explanation = get_llm_explanation(
        top_features=top_shap_features, keyword_matches=keyword_matches,
        content_risk=content_risk, risk_label=risk_label,
        verification_label=verification_label, job_title=job_title, company_name=company_name
    )

    risk_breakdown = get_risk_breakdown(keyword_matches, top_shap_features, verification_label, has_logo, has_profile)

    return {
        "content_risk": content_risk, "risk_label": risk_label,
        "confidence_score": confidence_score, "confidence_label": confidence_label,
        "verification_score": verification_score, "verification_label": verification_label,
        "verification_reasons": verification_reasons, "keyword_matches": keyword_matches,
        "top_shap_features": top_shap_features, "llm_explanation": llm_explanation,
        "job_title": job_title, "company_name": company_name, "job_description": job_description,
        "has_logo": has_logo, "has_profile": has_profile,
        "similar_scam": similar_scam, "risk_breakdown": risk_breakdown,
        "url_error": url_error,
    }

