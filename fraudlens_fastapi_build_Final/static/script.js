// Analyze form
const form = document.getElementById("analyze-form");
const analyzeBtn = document.getElementById("analyze-btn");
const errorBanner = document.getElementById("analyze-error");
const resultsSection = document.getElementById("results");

let lastResult = null; // cached for the report button

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBanner.hidden = true;

  const payload = {
    job_title: document.getElementById("job_title").value,
    company_name: document.getElementById("company_name").value,
    job_description: document.getElementById("job_description").value,
    url: document.getElementById("url").value || null,
    has_logo: document.getElementById("has_logo").checked,
    has_profile: document.getElementById("has_profile").checked,
    has_questions: document.getElementById("has_questions").checked,
  };

  if (!payload.job_description.trim()) {
    errorBanner.textContent = "Please paste a job description first.";
    errorBanner.hidden = false;
    return;
  }

  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analyzing...";

  try {
    const res = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || `Request failed (${res.status})`);
    }

    const result = await res.json();
    lastResult = result;
    renderResults(result);
  } catch (err) {
    errorBanner.textContent = `Something went wrong: ${err.message}`;
    errorBanner.hidden = false;
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analyze posting";
  }
});

// ==================== Clear button ====================

document.getElementById("clear-btn").addEventListener("click", () => {
  form.reset();
  resultsSection.hidden = true;
  errorBanner.hidden = true;
  lastResult = null;
});

// Render results

function riskBadgeClass(label) {
  if (label === "High") return "high";
  if (label === "Medium") return "medium";
  return "low";
}

function verificationBadgeClass(label) {
  if (label === "Verified") return "low"; // green
  if (label === "Partially Verified") return "medium";
  return "high";
}

function renderResults(r) {
  resultsSection.hidden = false;
  resultsSection.scrollIntoView({ behavior: "smooth" });

  // Similar scam warning
  const similarBanner = document.getElementById("similar-scam-banner");
  if (r.similar_scam) {
    const s = r.similar_scam;
    similarBanner.hidden = false;
    similarBanner.innerHTML = `
      <strong>Warning: similar scam reported before.</strong>
      ${(s.similarity * 100).toFixed(1)}% match against a previously reported posting
      ("${escapeHtml(s.best_match.job_title || "unknown")}"),
      out of ${s.total_reports} total reports on file. Be extra cautious.
    `;
  } else {
    similarBanner.hidden = true;
  }

  // Content risk
  document.getElementById("content-risk-value").textContent = `${r.content_risk}%`;
  const riskBadge = document.getElementById("risk-badge");
  riskBadge.textContent = `${r.risk_label} risk`;
  riskBadge.className = `badge ${riskBadgeClass(r.risk_label)}`;

  // Verification
  document.getElementById("verification-value").textContent = `${r.verification_score}%`;
  const verBadge = document.getElementById("verification-badge");
  verBadge.textContent = r.verification_label;
  verBadge.className = `badge ${verificationBadgeClass(r.verification_label)}`;

  const reasonsList = document.getElementById("verification-reasons");
  reasonsList.innerHTML = r.verification_reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("");

  // Keyword matches
  const keywordDiv = document.getElementById("keyword-matches");
  const categories = Object.keys(r.keyword_matches || {});
  if (categories.length === 0) {
    keywordDiv.innerHTML = `<p class="fineprint">No known scam keywords detected.</p>`;
  } else {
    keywordDiv.innerHTML = categories.map(cat => `
      <div class="keyword-category">
        <strong>${escapeHtml(cat.replace(/_/g, " "))}:</strong>
        ${r.keyword_matches[cat].map(escapeHtml).join(", ")}
      </div>
    `).join("");
  }

  // LLM explanation
  document.getElementById("llm-explanation").textContent = r.llm_explanation;

  // Red-flag checklist
  const checklist = [];
  r.top_shap_features
    .filter(f => f.direction === "toward FAKE")
    .slice(0, 3)
    .forEach(f => checklist.push(`Model flagged: ${f.feature.replace(/_/g, " ")}`));
  Object.entries(r.keyword_matches || {}).forEach(([cat, matches]) => {
    checklist.push(`${cat.replace(/_/g, " ")}: ${matches.join(", ")}`);
  });
  if (r.verification_label === "Unverified") checklist.push("Company could not be verified");
  if (!r.has_logo) checklist.push("No company logo");
  if (!r.has_profile) checklist.push("No company profile provided");

  const checklistEl = document.getElementById("red-flag-checklist");
  checklistEl.innerHTML = checklist.length
    ? checklist.slice(0, 8).map(item => `<li>${escapeHtml(item)}</li>`).join("")
    : `<li>No significant red flags detected.</li>`;

  // Detailed risk breakdown
  const breakdownDiv = document.getElementById("risk-breakdown");
  if (!r.risk_breakdown || r.risk_breakdown.length === 0) {
    breakdownDiv.innerHTML = `<p class="fineprint">No significant risk factors found.</p>`;
  } else {
    breakdownDiv.innerHTML = r.risk_breakdown.map(item => `
      <div class="risk-item">
        <div class="risk-item-head">
          <span>${escapeHtml(item.category)}</span>
          <span class="badge ${item.severity === "HIGH" ? "high" : "medium"}">${item.severity}</span>
        </div>
        <div class="risk-item-detail">${escapeHtml(item.detail)}</div>
        <div class="risk-item-advice">${escapeHtml(item.advice)}</div>
      </div>
    `).join("");
  }

  document.getElementById("report-confirmation").hidden = true;
}

// Report button

document.getElementById("report-btn").addEventListener("click", async () => {
  if (!lastResult) return;
  const btn = document.getElementById("report-btn");
  btn.disabled = true;

  try {
    const res = await fetch("/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_title: lastResult.job_title,
        company_name: lastResult.company_name,
        job_description: lastResult.job_description,
        content_risk: lastResult.content_risk,
        verification_label: lastResult.verification_label,
        keyword_matches: lastResult.keyword_matches,
      }),
    });
    const data = await res.json();
    const confirmation = document.getElementById("report-confirmation");
    confirmation.textContent = `Thank you — this posting has been reported. (${data.total_reports} total reports logged)`;
    confirmation.hidden = false;
  } catch (err) {
    alert("Could not submit the report. Please try again.");
  } finally {
    btn.disabled = false;
  }
});

// Utility

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
