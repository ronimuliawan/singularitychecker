const defaults = window.APP_DEFAULTS || {};

const elements = {
  profileSelect: document.getElementById("profile_name"),
  sessionProfileSelect: document.getElementById("session_profile_name"),
  jobForm: document.getElementById("job-form"),
  sessionForm: document.getElementById("session-form"),
  reloadProfilesButton: document.getElementById("reload-profiles"),
  refreshJobButton: document.getElementById("refresh-job"),
  rerunButton: document.getElementById("rerun-uncertain"),
  jobMessage: document.getElementById("job-message"),
  sessionMessage: document.getElementById("session-message"),
  jobId: document.getElementById("job-id"),
  progressBar: document.getElementById("progress-bar"),
  counts: document.getElementById("counts"),
  jobList: document.getElementById("job-list"),
  resultsBody: document.getElementById("results-body"),
  downloadCsv: document.getElementById("download-csv"),
  pastedCodes: document.getElementById("pasted_codes"),
  codeFiles: document.getElementById("code_files"),
  redeemUrlOverride: document.getElementById("redeem_url_override"),
  sessionFile: document.getElementById("session_file"),
  httpConcurrency: document.getElementById("http_concurrency"),
  browserConcurrency: document.getElementById("browser_concurrency"),
  maxRetries: document.getElementById("max_retries"),
  requestDelayMs: document.getElementById("request_delay_ms"),
};

let currentJobId = null;
let pollHandle = null;

function setMessage(target, text, isError) {
  target.textContent = text;
  target.classList.toggle("error", Boolean(isError));
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 401) {
    window.location.href = "/login";
    return null;
  }

  let payload;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    payload = await response.json();
  } else {
    payload = { detail: await response.text() };
  }

  if (!response.ok) {
    const message = payload.detail || payload.message || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return payload;
}

function applyDefaults() {
  elements.httpConcurrency.value = defaults.default_http_concurrency || 20;
  elements.browserConcurrency.value = defaults.default_browser_concurrency || 1;
  elements.maxRetries.value = defaults.default_max_retries || 2;
  elements.requestDelayMs.value = defaults.default_request_delay_ms || 100;
}

function renderProfileOptions(profiles) {
  if (!profiles.length) {
    const empty = '<option value="" selected disabled>No profiles loaded</option>';
    elements.profileSelect.innerHTML = empty;
    elements.sessionProfileSelect.innerHTML = empty;
    return;
  }

  const optionHtml = profiles
    .map((profile) => {
      const sessionTag = profile.has_session_state ? "session ready" : "session missing";
      const loginTag = profile.login_required ? "login required" : "login optional";
      return `<option value="${profile.name}">${profile.name} (${profile.mode}, ${loginTag}, ${sessionTag})</option>`;
    })
    .join("");

  elements.profileSelect.innerHTML = optionHtml;
  elements.sessionProfileSelect.innerHTML = optionHtml;
}

async function loadProfiles() {
  const payload = await api("/api/profiles");
  const profiles = payload.profiles || [];
  if (!profiles.length) {
    renderProfileOptions([]);
    setMessage(
      elements.jobMessage,
      "No profiles found. Add a .yaml profile file under ./profiles and reload profiles.",
      true,
    );
    return;
  }
  renderProfileOptions(profiles);
}

function renderJobList(jobs) {
  if (!jobs.length) {
    elements.jobList.innerHTML = "<li class='muted'>No jobs yet.</li>";
    return;
  }

  elements.jobList.innerHTML = jobs
    .map((job) => {
      const shortId = job.id.slice(0, 8);
      const meta = `${job.status} | ${job.profile_name} | ${job.total_codes} codes`;
      return `<li><button type="button" data-job-id="${job.id}">#${shortId} - ${meta}</button></li>`;
    })
    .join("");

  elements.jobList.querySelectorAll("button[data-job-id]").forEach((button) => {
    button.addEventListener("click", () => {
      currentJobId = button.getAttribute("data-job-id");
      refreshCurrentJob().catch((error) => setMessage(elements.jobMessage, error.message, true));
    });
  });
}

async function loadRecentJobs() {
  const payload = await api("/api/jobs?limit=20");
  renderJobList(payload.jobs || []);
}

function renderCounts(countsPayload) {
  const counts = countsPayload.by_status || {};
  const statusOrder = [
    "pending",
    "running",
    "queued_browser",
    "valid",
    "invalid",
    "unknown",
    "blocked",
    "error",
  ];

  const rows = [
    `<div><strong>Total:</strong> ${countsPayload.total ?? 0}</div>`,
    `<div><strong>Processed:</strong> ${countsPayload.processed ?? 0}</div>`,
    `<div><strong>Progress:</strong> ${countsPayload.progress_percent ?? 0}%</div>`,
  ];

  statusOrder.forEach((status) => {
    if (counts[status] !== undefined) {
      rows.push(`<div><strong>${status}:</strong> ${counts[status]}</div>`);
    }
  });
  elements.counts.innerHTML = rows.join("");

  const progress = Number(countsPayload.progress_percent || 0);
  elements.progressBar.style.width = `${Math.max(0, Math.min(100, progress))}%`;
}

function renderResults(results) {
  if (!results.length) {
    elements.resultsBody.innerHTML = "<tr><td colspan='6' class='muted'>No results yet.</td></tr>";
    return;
  }

  elements.resultsBody.innerHTML = results
    .map((row) => {
      const checkedAt = row.checked_at ? new Date(row.checked_at).toLocaleString() : "-";
      return `
        <tr>
          <td class="mono">${row.code}</td>
          <td>${row.status}</td>
          <td>${row.source}</td>
          <td>${row.reason || ""}</td>
          <td>${row.attempts}</td>
          <td>${checkedAt}</td>
        </tr>
      `;
    })
    .join("");
}

async function refreshCurrentJob() {
  if (!currentJobId) {
    return;
  }

  const encodedId = encodeURIComponent(currentJobId);
  const [jobPayload, resultsPayload] = await Promise.all([
    api(`/api/jobs/${encodedId}`),
    api(`/api/jobs/${encodedId}/results?limit=100`),
  ]);

  const job = jobPayload.job;
  const counts = jobPayload.counts;

  elements.jobId.textContent = `Job ${job.id} | ${job.status} | profile=${job.profile_name}`;
  renderCounts(counts);
  renderResults(resultsPayload.results || []);

  elements.downloadCsv.hidden = false;
  elements.downloadCsv.href = `/api/jobs/${encodedId}/export.csv`;

  if (["completed", "failed"].includes(job.status)) {
    await loadRecentJobs();
  }
}

function startPolling() {
  if (pollHandle) {
    clearInterval(pollHandle);
  }
  pollHandle = setInterval(() => {
    if (!currentJobId) {
      return;
    }
    refreshCurrentJob().catch((error) => setMessage(elements.jobMessage, error.message, true));
  }, 2500);
}

async function handleCreateJob(event) {
  event.preventDefault();

  const form = new FormData();
  form.append("profile_name", elements.profileSelect.value);
  form.append("redeem_url_override", elements.redeemUrlOverride.value.trim());
  form.append("pasted_codes", elements.pastedCodes.value || "");
  form.append("http_concurrency", String(elements.httpConcurrency.value));
  form.append("browser_concurrency", String(elements.browserConcurrency.value));
  form.append("max_retries", String(elements.maxRetries.value));
  form.append("request_delay_ms", String(elements.requestDelayMs.value));

  Array.from(elements.codeFiles.files).forEach((file) => {
    form.append("code_files", file, file.name);
  });

  const payload = await api("/api/jobs", {
    method: "POST",
    body: form,
  });

  currentJobId = payload.job_id;
  setMessage(
    elements.jobMessage,
    `Job ${payload.job_id} created. ${payload.total_codes} unique codes (removed ${payload.duplicates_removed} duplicates).`,
    false,
  );
  await loadRecentJobs();
  await refreshCurrentJob();
}

async function handleRerunUncertain() {
  if (!currentJobId) {
    setMessage(elements.jobMessage, "Select a job first.", true);
    return;
  }

  const payload = await api(`/api/jobs/${encodeURIComponent(currentJobId)}/rerun-uncertain`, {
    method: "POST",
  });
  setMessage(elements.jobMessage, `${payload.message}. Updated rows: ${payload.updated}.`, false);
  await refreshCurrentJob();
}

async function handleUploadSession(event) {
  event.preventDefault();

  if (!elements.sessionFile.files.length) {
    setMessage(elements.sessionMessage, "Pick a session-state JSON file first.", true);
    return;
  }

  const profileName = elements.sessionProfileSelect.value;
  const form = new FormData();
  form.append("session_file", elements.sessionFile.files[0], elements.sessionFile.files[0].name);

  const payload = await api(`/api/profiles/${encodeURIComponent(profileName)}/session-state`, {
    method: "POST",
    body: form,
  });
  setMessage(elements.sessionMessage, `${payload.message} for ${payload.profile}.`, false);
  await loadProfiles();
}

async function handleReloadProfiles() {
  await api("/api/profiles/reload", { method: "POST" });
  await loadProfiles();
  setMessage(elements.jobMessage, "Profiles reloaded from disk.", false);
}

async function initialize() {
  applyDefaults();
  await loadProfiles();
  await loadRecentJobs();
  startPolling();

  elements.jobForm.addEventListener("submit", (event) => {
    handleCreateJob(event).catch((error) => setMessage(elements.jobMessage, error.message, true));
  });

  elements.sessionForm.addEventListener("submit", (event) => {
    handleUploadSession(event).catch((error) => setMessage(elements.sessionMessage, error.message, true));
  });

  elements.reloadProfilesButton.addEventListener("click", () => {
    handleReloadProfiles().catch((error) => setMessage(elements.jobMessage, error.message, true));
  });

  elements.refreshJobButton.addEventListener("click", () => {
    refreshCurrentJob().catch((error) => setMessage(elements.jobMessage, error.message, true));
  });

  elements.rerunButton.addEventListener("click", () => {
    handleRerunUncertain().catch((error) => setMessage(elements.jobMessage, error.message, true));
  });
}

initialize().catch((error) => {
  setMessage(elements.jobMessage, `Initialization failed: ${error.message}`, true);
});
