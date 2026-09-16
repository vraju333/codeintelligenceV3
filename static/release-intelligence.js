async function generateReleaseIntelligence() {
    return openJiraRiskReport();
}

async function openJiraRiskReport() {
    const result = document.getElementById("jiraImpactResult");
    const jiraText = document.getElementById("jiraImpactRequirement")?.value || "";
    const includeAi = false;

    if (!result) return;
    const report = openRiskReportShell();
    if (!report) {
        result.innerHTML = '<div class="jira-impact-error">Browser blocked the report window. Please allow popups for this app and click Open Risk Report again.</div>';
        return;
    }
    result.innerHTML = '<div class="jira-impact-loading">Generating risk report...</div>';

    try {
        const response = await fetch("/api/release-intelligence/report", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                jira_text: jiraText,
                include_ai: includeAi
            })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(JSON.stringify(data));
        }
        writeReleaseReportWindow(report, data);
        result.innerHTML = '<div class="jira-impact-block"><h3>Risk report opened</h3><div class="jira-impact-note">The release risk report opened in a new window. If it did not open, allow popups for this app and click Open Risk Report again.</div></div>';
    } catch (error) {
        writeReleaseReportError(report, error.message || "Release intelligence failed.");
        result.innerHTML = `<div class="jira-impact-error">${escapeHtml(error.message || "Release intelligence failed.")}</div>`;
    }
}

function openRiskReportShell() {
    const report = window.open("", "_blank", "noopener,noreferrer,width=1200,height=800");
    if (!report) {
        return null;
    }
    report.document.open();
    report.document.write(`
        <!doctype html>
        <html><head><title>CodeIntelligence Risk Report</title></head>
        <body style="font-family:Arial,sans-serif;margin:24px;color:#172033;background:#f6f8fb">
            <div style="max-width:1180px;margin:0 auto;background:white;border:1px solid #d7e2f2;border-radius:10px;padding:20px">
                <h1 style="margin-top:0">Generating Risk Report...</h1>
                <p>Reading local Git changes and scenario baselines.</p>
            </div>
        </body></html>
    `);
    report.document.close();
    return report;
}

function writeReleaseReportError(report, message) {
    if (!report || report.closed) return;
    report.document.open();
    report.document.write(`
        <!doctype html>
        <html><head><title>CodeIntelligence Risk Report</title></head>
        <body style="font-family:Arial,sans-serif;margin:24px;color:#172033;background:#f6f8fb">
            <div style="max-width:1180px;margin:0 auto;background:white;border:1px solid #fecaca;border-radius:10px;padding:20px">
                <h1 style="margin-top:0;color:#991b1b">Risk Report Failed</h1>
                <pre style="white-space:pre-wrap">${escapeHtml(message)}</pre>
            </div>
        </body></html>
    `);
    report.document.close();
}

function writeReleaseReportWindow(report, data) {
    if (!report || report.closed) return;
    report.document.open();
    report.document.write(`
        <!doctype html>
        <html>
        <head>
            <title>CodeIntelligence Risk Report</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 24px; color: #172033; background: #f6f8fb; }
                .report-wrap { max-width: 1180px; margin: 0 auto; }
                .report-header { background: #2563a6; color: white; padding: 20px; border-radius: 10px; margin-bottom: 16px; }
                .report-header h1 { margin: 0 0 6px; font-size: 1.6rem; }
                .panel { background: white; border: 1px solid #d7e2f2; border-radius: 10px; padding: 16px; margin-bottom: 14px; }
                .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
                .metric { background: #f8fbff; border: 1px solid #d7e2f2; border-radius: 8px; padding: 12px; }
                .metric span { display: block; color: #5b6b82; font-size: .78rem; font-weight: 700; text-transform: uppercase; }
                .metric strong { display: block; margin-top: 5px; font-size: 1.35rem; }
                .row { border-top: 1px solid #e2e8f0; padding: 10px 0; }
                .badge { display: inline-block; border-radius: 999px; padding: 4px 9px; background: #e8f1ff; margin-right: 6px; font-size: .78rem; font-weight: 700; }
                .muted { color: #5b6b82; }
                pre { white-space: pre-wrap; background: #f8fbff; border: 1px solid #d7e2f2; border-radius: 8px; padding: 12px; }
            </style>
        </head>
        <body>
            <div class="report-wrap">
                <div class="report-header">
                    <h1>CodeIntelligence Risk Report</h1>
                    <div>Local release risk, changed files, scenario impact and QA recommendations.</div>
                </div>
                ${renderReleaseIntelligence(data)}
            </div>
        </body>
        </html>
    `);
    report.document.close();
}

function renderReleaseIntelligence(data) {
    const risk = data.risk || {};
    const changedFiles = data.changed_files || [];
    const impacted = data.impacted_scenarios || [];
    const tests = data.test_recommendations || [];
    const ai = data.ai_summary || null;

    return `
        <div class="risk-dashboard">
            <div class="risk-summary-card">
                <div class="risk-summary-head">
                    <div>
                        <div class="muted-text">Release Risk Dashboard</div>
                        <div class="risk-score">${Number(risk.score || 0)}/100</div>
                    </div>
                    <span class="risk-level ${escapeHtml(risk.level || "LOW")}">${escapeHtml(risk.level || "LOW")}</span>
                </div>
                <p><strong>Recommendation:</strong> ${escapeHtml(risk.release_recommendation || "")}</p>
                <ul class="risk-list">
                    ${(risk.reasons || []).map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}
                </ul>
            </div>

            <div class="risk-grid">
                <div class="risk-metric"><span>Changed Files</span><strong>${changedFiles.length}</strong></div>
                <div class="risk-metric"><span>Impacted Scenarios</span><strong>${impacted.length}</strong></div>
                <div class="risk-metric"><span>Test Recommendations</span><strong>${tests.length}</strong></div>
            </div>

            <div class="risk-section">
                <h3>Changed Files</h3>
                ${changedFiles.length ? changedFiles.map(file => `
                    <div class="risk-file-row">
                        <span class="risk-badge">${escapeHtml(file.status || "MODIFIED")}</span>
                        <span class="risk-file-path">${escapeHtml(file.file_path || "")}</span>
                        <span class="risk-badge">${escapeHtml(file.risk_hint || "LOW")}</span>
                    </div>
                `).join("") : `<div class="muted-box">No local Git changes detected.</div>`}
            </div>

            <div class="risk-section">
                <h3>Impacted Scenarios</h3>
                ${impacted.length ? impacted.map(item => `
                    <div class="risk-scenario-row">
                        <span class="risk-badge">${escapeHtml(item.scenario_code || "")}</span>
                        <div>
                            <strong>${escapeHtml(item.operation || "")}</strong>
                            <div class="muted-text">${(item.reasons || []).map(escapeHtml).join(" · ")}</div>
                            ${(item.matched_files || []).length ? `<div class="muted-text">Files: ${(item.matched_files || []).map(escapeHtml).join(", ")}</div>` : ""}
                        </div>
                        <span class="risk-badge">${item.missing_test_baseline ? "TEST NEEDED" : "TEST EXISTS"}</span>
                    </div>
                `).join("") : `<div class="muted-box">No registered scenario matched these changes yet.</div>`}
            </div>

            <div class="risk-section">
                <h3>Recommended QA</h3>
                ${tests.length ? tests.map(item => `
                    <div class="risk-scenario-row">
                        <span class="risk-badge">${escapeHtml(item.scenario_code || "")}</span>
                        <div>
                            <strong>${escapeHtml(item.operation || "")}</strong>
                            <div class="muted-text">${escapeHtml(item.recommendation || "")}</div>
                        </div>
                    </div>
                `).join("") : `<div class="muted-box">No test recommendations generated.</div>`}
            </div>

            ${ai ? `
                <div class="risk-section">
                    <h3>OpenAI / LLM Summary</h3>
                    <div class="muted-text">Status: ${escapeHtml(ai.status || "")} ${ai.provider ? `· Provider: ${escapeHtml(ai.provider)}` : ""}</div>
                    <div class="risk-ai-summary">${escapeHtml(ai.text || "")}</div>
                </div>
            ` : ""}
        </div>
    `;
}
