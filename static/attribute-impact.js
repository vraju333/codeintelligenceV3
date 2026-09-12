async function analyseAttributeImpact() {
    const input = document.getElementById("attributeImpactInput");
    const container = document.getElementById("attributeImpactResult");
    const attribute = (input?.value || "").trim();

    if (!attribute) {
        container.innerHTML = `<div class="warning">Enter an attribute name first.</div>`;
        return;
    }

    container.innerHTML = "Analysing attribute impact across the active Python project...";

    try {
        const response = await fetch(`/api/attribute-lineage/impact?attribute=${encodeURIComponent(attribute)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));
        container.innerHTML = renderAttributeImpact(data);
    } catch (error) {
        container.innerHTML = renderError(error.message);
    }
}

function renderAttributeImpact(data) {
    const layers = data.layers || [];
    const endpoints = data.affected_endpoints || [];
    const scenarios = data.affected_scenarios || [];
    const confidence = data.confidence || {};
    const relatedJiras = data.related_jiras || [];

    const layerHtml = layers.length ? layers.map(layer => `
        <div class="attribute-layer-card">
            <div class="attribute-layer-role">${escapeHtml(layer.role || "PYTHON_SYMBOL")}</div>
            ${(layer.classes || []).map(name => `<div class="attribute-class-name">${escapeHtml(name)}</div>`).join("")}
            ${(layer.methods || []).length ? `
                <div class="muted-text attribute-methods">${(layer.methods || []).map(escapeHtml).join(" · ")}</div>
            ` : ""}
        </div>
    `).join("") : `<div class="muted-box">No Python occurrence found for this attribute.</div>`;

    const endpointHtml = endpoints.length ? endpoints.map(endpoint => `
        <div class="attribute-impact-row">
            <div>
                <strong>${escapeHtml(endpoint.http_method)} ${escapeHtml(endpoint.endpoint)}</strong>
                <div class="muted-text">${escapeHtml(endpoint.controller || "")} . ${escapeHtml(endpoint.method_name || "")}</div>
            </div>
            <span class="tag">${escapeHtml(endpoint.relevance || "FLOW")}</span>
            ${(endpoint.matched_methods || []).length ? `<div class="attribute-evidence">Direct methods: ${endpoint.matched_methods.map(escapeHtml).join(", ")}</div>` : ""}
            ${(endpoint.matched_classes || []).length ? `<div class="attribute-evidence">Classes: ${endpoint.matched_classes.map(escapeHtml).join(", ")}</div>` : ""}
            ${(endpoint.dependency_path || []).length ? `
                <div class="dependency-path-inline">
                    ${(endpoint.dependency_path || []).map(step => `<span>${escapeHtml(step)}</span>`).join(`<b>→</b>`)}
                </div>
            ` : ""}
        </div>
    `).join("") : `<div class="muted-box">No endpoint flow currently intersects this attribute.</div>`;

    const jiraHtml = relatedJiras.length ? relatedJiras.map(jira => `
        <div class="attribute-impact-row">
            <div>
                <strong>${escapeHtml(jira.jira_id || "")}</strong>
                <div class="muted-text">${escapeHtml(jira.title || "")}</div>
            </div>
            <span class="tag">${escapeHtml(jira.relationship || "SEMANTIC_RELATED")}</span>
            ${(jira.reasons || []).map(reason => `<div class="attribute-evidence">• ${escapeHtml(reason)}</div>`).join("")}
            <div class="attribute-evidence">${escapeHtml(jira.requirement || "")}</div>
        </div>
    `).join("") : `<div class="muted-box">No saved JIRA requirement matched this attribute yet.</div>`;

    const scenarioHtml = scenarios.length ? scenarios.map(scenario => {
        const releases = scenario.release_history || [];
        const releaseHistory = releases.length ? `
            <div class="attribute-release-history">
                <div class="attribute-release-title">Release / Version history</div>
                ${releases.map(release => `
                    <div class="attribute-release-group">
                        <div class="attribute-release-name">${escapeHtml(release.release || "Legacy")}</div>
                        <div class="attribute-release-versions">
                            ${(release.versions || []).map(version => `
                                <div class="attribute-release-version ${version.is_active ? "is-active" : ""}">
                                    <div class="attribute-release-version-head">
                                        <strong>V${Number(version.release_version || 1)}</strong>
                                        ${version.is_active ? `<span class="tag">ACTIVE</span>` : ``}
                                    </div>
                                    ${(version.tests || []).length ? (version.tests || []).map(test => `
                                        <div class="attribute-linked-test">
                                            <span>${escapeHtml(test.test_scenario || "Test Scenario")}</span>
                                            <span class="tag">${escapeHtml(test.status || "NOT_RUN")}</span>
                                            <div class="attribute-linked-jiras">
                                                ${(test.jira_ids || []).length
                                                    ? (test.jira_ids || []).map(id => `<span class="testing-jira-chip">${escapeHtml(id)}</span>`).join("")
                                                    : `<span class="muted-text">No JIRA</span>`}
                                            </div>
                                        </div>
                                    `).join("") : `<div class="muted-text">No test baseline linked to this version.</div>`}
                                </div>
                            `).join("")}
                        </div>
                    </div>
                `).join("")}
            </div>
        ` : `<div class="muted-text attribute-no-release">No release baseline captured for this scenario yet.</div>`;

        return `
            <div class="attribute-impact-row">
                <div class="attribute-scenario-heading">
                    <div>
                        <strong>${escapeHtml(scenario.scenario_code || "")}</strong>
                        <div class="muted-text">${escapeHtml(scenario.http_method || "")} ${escapeHtml(scenario.endpoint || "")}</div>
                    </div>
                </div>
                ${(scenario.reasons || []).map(reason => `<div class="attribute-evidence">• ${escapeHtml(reason)}</div>`).join("")}
                ${releaseHistory}
            </div>
        `;
    }).join("") : `<div class="muted-box">No registered scenario intersects this attribute yet.</div>`;

    return `
        <div class="attribute-impact-summary">
            <div><span>Attribute</span><strong>${escapeHtml(data.attribute || "")}</strong></div>
            <div><span>Occurrences</span><strong>${data.total_occurrences || 0}</strong></div>
            <div><span>Endpoints</span><strong>${endpoints.length}</strong></div>
            <div><span>Scenarios</span><strong>${scenarios.length}</strong></div>
            <div><span>Confidence</span><strong>${escapeHtml(confidence.level || "LOW")} · ${confidence.score || 0}%</strong></div>
        </div>

        <div class="attribute-impact-section">
            <h3>Attribute path through code</h3>
            <div class="attribute-layer-grid">${layerHtml}</div>
        </div>

        <div class="attribute-impact-two-column">
            <div class="attribute-impact-section">
                <h3>Affected endpoints</h3>
                ${endpointHtml}
            </div>
            <div class="attribute-impact-section">
                <h3>Affected scenarios</h3>
                ${scenarioHtml}
            </div>
        </div>

        <div class="attribute-impact-section">
            <h3>Related JIRAs</h3>
            ${jiraHtml}
        </div>

        <div class="muted-text attribute-local-note">LangGraph parallel analysis · code impact finds the scenario; exact Release/Version/Test/JIRA history comes from the baseline database · local JIRA RAG supplies semantic JIRA evidence.</div>
    `;
}
