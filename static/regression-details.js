
/* Regression source-change details.
   Loaded after app.js; intentionally overrides renderRegression. */

function renderRegressionSourceChanges(data) {
    const files = data.git_changes?.changed_files || [];

    if (!files.length) {
        return "";
    }

    let totalClassified = 0;
    files.forEach(file => {
        totalClassified += (file.source_changes || []).length;
    });

    let html = `
        <div class="regression-change-section">
            <div class="regression-change-heading">
                <div>
                    <h3>What changed?</h3>
                    <p class="muted-text">
                        Source-level Git changes that caused this impact analysis.
                    </p>
                </div>
                <span class="tag">${files.length} file${files.length === 1 ? "" : "s"} · ${totalClassified} detected change${totalClassified === 1 ? "" : "s"}</span>
            </div>
            <div class="regression-file-list">
    `;

    for (const file of files) {
        const changes = file.source_changes || [];

        html += `
            <div class="regression-file-card">
                <div class="regression-file-header">
                    <div>
                        <strong>${escapeHtml(file.file_name || file.class_name || "")}</strong>
                        <span class="muted-text">${escapeHtml(file.file_path || "")}</span>
                    </div>
                    <span class="tag">${changes.length} change${changes.length === 1 ? "" : "s"}</span>
                </div>
        `;

        if (!changes.length) {
            html += `<div class="muted-box">Changed file detected; no field/method classification was available.</div>`;
        }

        for (const change of changes) {
            const type = change.change_type || "SOURCE_MODIFIED";
            const cssType =
                type.includes("ADDED") ? "added" :
                type.includes("REMOVED") ? "removed" :
                "modified";

            let detail = "";
            if (change.data_type && change.symbol) {
                detail = `${escapeHtml(change.symbol)} : ${escapeHtml(change.data_type)}`;
            } else if (change.symbol) {
                detail = escapeHtml(change.symbol);
            }

            const lineText = change.line_number ? `Line ${change.line_number}` : "";

            html += `
                <div class="source-change-row ${cssType}">
                    <div class="source-change-icon">${
                        cssType === "added" ? "+" :
                        cssType === "removed" ? "−" : "~"
                    }</div>
                    <div class="source-change-main">
                        <div class="source-change-title">
                            <strong>${escapeHtml(change.label || type)}</strong>
                            ${lineText ? `<span>${escapeHtml(lineText)}</span>` : ""}
                        </div>
                        ${detail ? `<div class="source-change-symbol">${detail}</div>` : ""}
                        ${change.added_text ? `<code class="diff-line diff-add">+ ${escapeHtml(change.added_text)}</code>` : ""}
                        ${change.removed_text ? `<code class="diff-line diff-remove">− ${escapeHtml(change.removed_text)}</code>` : ""}
                        ${(change.added_lines || []).length ? `
                            <div class="compact-code-lines">
                                ${(change.added_lines || []).map(line =>
                                    `<code class="diff-line diff-add">+ ${escapeHtml(line)}</code>`
                                ).join("")}
                            </div>
                        ` : ""}
                        ${(change.removed_lines || []).length ? `
                            <div class="compact-code-lines">
                                ${(change.removed_lines || []).map(line =>
                                    `<code class="diff-line diff-remove">− ${escapeHtml(line)}</code>`
                                ).join("")}
                            </div>
                        ` : ""}
                    </div>
                </div>
            `;
        }

        if (file.raw_diff) {
            html += `
                <details class="technical-details regression-source-diff">
                    <summary>View Git diff</summary>
                    <pre>${escapeHtml(file.raw_diff)}</pre>
                </details>
            `;
        }

        html += `</div>`;
    }

    html += `</div></div>`;
    return html;
}

function renderRegression(data) {
    if (data.status === "NO_CHANGES") {
        return `<div class="success">No Python source changes detected.</div>`;
    }

    const direct = data.directly_affected || [];
    const possible = data.possibly_affected || [];
    const unaffected = data.unaffected_scenarios || [];
    const operations = data.affected_operations || [];

    let html = `
        <div class="regression-hero">
            <div>
                <div class="section-eyebrow">CHANGE IMPACT ANALYSIS</div>
                <h2>Python Change Blast Radius</h2>
                <p>Operations are discovered directly from the current FastAPI code. Scenario impact is shown separately when baselines exist.</p>
            </div>
        </div>
        <div class="regression-summary-grid">
            <div class="metric-card"><span class="metric-label">Changed files</span><strong>${data.total_changed_java_files || 0}</strong></div>
            <div class="metric-card operation-metric"><span class="metric-label">Affected operations</span><strong>${operations.length}</strong></div>
            <div class="metric-card danger-metric"><span class="metric-label">Direct scenarios</span><strong>${direct.length}</strong></div>
            <div class="metric-card warning-metric"><span class="metric-label">Possible scenarios</span><strong>${possible.length}</strong></div>
        </div>
    `;

    html += renderRegressionSourceChanges(data);
    html += renderAffectedOperations(operations);
    html += `<div class="regression-section-title"><div><div class="section-eyebrow">SCENARIO REGISTRY</div><h3>Scenario Impact</h3><p class="muted-text">These results depend on captured scenario/operation baselines.</p></div></div>`;

    html += renderImpactGroup(
        "Directly affected",
        direct,
        "danger",
        "Changed method is in the stored scenario flow."
    );

    html += renderImpactGroup(
        "Possibly affected",
        possible,
        "warning",
        "Changed class is used by the scenario, but exact method evidence is not stored."
    );

    html += renderImpactGroup(
        "Unaffected / no baseline",
        unaffected,
        "neutral-impact",
        "No overlap with current changes, or baseline has not been captured."
    );

    return html;
}


function renderAffectedOperations(operations) {
    let html = `
        <div class="operation-impact-section">
            <div class="regression-section-title">
                <div>
                    <div class="section-eyebrow">LIVE CODE GRAPH</div>
                    <h3>Affected Operations</h3>
                    <p class="muted-text">Discovered from FastAPI routes, execution flow and request/response schemas. No Scenario Registry entry is required.</p>
                </div>
                <span class="operation-count">${operations.length}</span>
            </div>
    `;
    if (!operations.length) {
        return html + `<div class="muted-box">No FastAPI operation could be linked to the current Python source changes.</div></div>`;
    }
    html += `<div class="operation-impact-grid">`;
    for (const op of operations) {
        const method = escapeHtml(op.http_method || "");
        html += `
            <div class="operation-impact-card">
                <div class="operation-card-top">
                    <span class="http-badge http-${method.toLowerCase()}">${method}</span>
                    <strong>${escapeHtml(op.endpoint || "")}</strong>
                </div>
                <div class="operation-handler">${escapeHtml(op.handler || "")}</div>
                <div class="operation-reason">${escapeHtml(op.reason || "")}</div>
                ${(op.evidence || []).length ? `<div class="operation-evidence">${op.evidence.map(x => `<span>${escapeHtml(x)}</span>`).join("")}</div>` : ""}
            </div>
        `;
    }
    return html + `</div></div>`;
}

function renderImpactGroup(title, scenarios, cssClass, emptyText) {
    let html = `<div class="impact-group"><div class="impact-group-header"><h3>${escapeHtml(title)}</h3><span class="tag">${scenarios.length}</span></div>`;
    if (scenarios.length === 0) {
        html += `<div class="muted-box">${escapeHtml(emptyText)}</div></div>`;
        return html;
    }

    html += `<div class="impact-card-grid">`;
    for (const scenario of scenarios) {
        const version = scenario.baseline_version ? `V${scenario.baseline_version}` : "No baseline";
        const paths = scenario.dependency_paths || [];
        html += `
            <div class="impact-card ${cssClass}">
                <div class="impact-card-title">${escapeHtml(scenario.scenario_code || "")}</div>
                <div class="muted-text">${escapeHtml(scenario.http_method || "")} ${escapeHtml(scenario.endpoint || "")}</div>
                <div class="impact-card-footer">
                    <span class="tag">${escapeHtml(version)}</span>
                    ${scenario.matched_classes?.length ? `<span class="muted-text">${escapeHtml(scenario.matched_classes.join(", "))}</span>` : ""}
                </div>
                ${scenario.reason ? `<div class="regression-impact-reason">${escapeHtml(scenario.reason)}</div>` : ""}
                ${paths.length ? `
                    <div class="regression-why-block">
                        <strong>Why affected?</strong>
                        ${paths.map(path => `
                            <div class="regression-dependency-path">
                                ${(path.path || []).map(step => `<span>${escapeHtml(step)}</span>`).join(`<b>→</b>`)}
                            </div>
                        `).join("")}
                    </div>
                ` : (scenario.impact_status !== "UNAFFECTED" && scenario.impact_status !== "NO_BASELINE" ? `
                    <div class="muted-text regression-why-empty">Stored baseline intersects the changed class, but an ordered method path is unavailable for this older flow.</div>
                ` : "")}
            </div>
        `;
    }
    html += `</div></div>`;
    return html;
}
