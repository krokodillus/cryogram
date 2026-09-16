// All network calls to the local backend live here; every one carries the per-start token the server put into this page
const TOKEN = document.querySelector('meta[name="cryogram-token"]')?.content || "";

// Every request carries the per-start token; no token means a dev page opened straight from disk
export function headers(extra) {
  const h = { "X-Cryogram-Token": TOKEN, ...(extra || {}) };
  if (!TOKEN) delete h["X-Cryogram-Token"];
  return h;
}

// GET helper: parses JSON and throws with the HTTP status attached, so callers can fork on it
async function j(url, opts) {
  const o = opts ? { ...opts, headers: headers(opts.headers) }
                 : { headers: headers() };
  const r = await fetch(url, o);

  if (!r.ok) {
    const e = new Error(`${r.status} ${url}`);
    e.status = r.status;
    throw e;
  }
  return r.json();
}

// JSON-body helper for POST, PUT and DELETE
const send = (url, method, body) =>
  j(url, { method, headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body) });

// Admin > Preferences: wipes everything stored on this machine, then the server closes itself
export const deleteAllData = () => send("/api/admin/delete-all-data", "POST", {});
// Stops the app on this computer; the page is told before the server goes
export const shutdown = () => send("/api/shutdown", "POST", {});

// The share preview IS the consent: the GET returns exactly what the POST would send
export const getSharePreview = (id, tid) =>
  j(`/api/workflows/${id}/tickets/${tid}/share-preview`);
export const shareIssue = (id, tid) =>
  send(`/api/workflows/${id}/tickets/${tid}/share`, "POST", {});

// The request helpers an integration uses to call its own backend routes
export { j as getJson, send as sendJson };

// The integrations installed alongside the app, if any
export const getExtensions = () => j("/api/extensions");

// Update check and the two-step download/approve; inert in a dev checkout
export const getUpdateCheck = () => j("/api/update/check");

export const getWorkflows = () => j("/api/workflows");
export const getWorkflow = (id) => j(`/api/workflows/${id}`);
export const createWorkflow = (name, description, group, environmentIds) =>
  send("/api/workflows", "POST",
    { name, description, group, environment_ids: environmentIds || [] });
// One page of the run history: completed manifests plus paused attempts
export const getWorkflowRuns = (id, page = 1) =>
  j(`/api/workflows/${id}/runs${page > 1 ? `?page=${page}` : ""}`);

// The step drawer's model picker: workflow models, cheapest first
export const getWorkflowModels = () => send("/api/models/workflow");

// The drawer's Evidence section, fetched lazily when it opens
export const getNodeEvidence = (id, nodeId) =>
  send(`/api/workflows/${id}/nodes/${nodeId}/evidence`);

// The one reply route: typed words or a card click; the server decides how it lands
export const say = (id, body) => send(`/api/workflows/${id}/say`, "POST", body);

// Saving an edited prompt, or resetting it to the agent's version
export const setNodePrompt = (id, nodeId, body) =>
  send(`/api/workflows/${id}/nodes/${nodeId}/prompt`, "POST", body);
// Picking an AI step's model - applied at once; the step is proven again on its next run
export const setNodeModelSettings = (id, nodeId, patch) =>
  send(`/api/workflows/${id}/nodes/${nodeId}/model`, "POST", patch);

export const setWorkflowGroup = (id, group) =>
  send(`/api/workflows/${id}/group`, "PUT", { group });
export const setWorkflowMeta = (id, name, description) =>
  send(`/api/workflows/${id}/meta`, "PUT", { name, description });
// Hard delete of a live workflow; archiving is the reversible path, this is permanent
export const deleteWorkflow = (id) => j(`/api/workflows/${id}`, { method: "DELETE" });
// Copies a workflow as a new independent one - fresh id, history reset
export const duplicateWorkflow = (id, name) =>
  send(`/api/workflows/${id}/duplicate`, "POST", name ? { name } : {});
export const duplicateEnvironment = (id) =>
  send(`/api/environments/${id}/duplicate`, "POST", {});
// Imports a shared workflow from a parsed file, or from a pasted link the server fetches
export async function importWorkflow(payload) {
  const r = await fetch("/api/workflows/import", {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {

    const e = new Error(data.error || `${r.status} import`);
    e.status = r.status;
    throw e;
  }
  return data;
}

export const setWorkflowEnvironments = (id, environmentIds) =>
  send(`/api/workflows/${id}/environment`, "PUT", { environment_ids: environmentIds });

// Environments: reusable variable sets, edited only on their own page
export const getEnvironments = () => j("/api/environments");
export const getEnvironment = (id) => j(`/api/environments/${id}`);
export const createEnvironment = (name, description, group = "") =>
  send("/api/environments", "POST", { name, description, group });
export const updateEnvironment = (id, patch) =>
  send(`/api/environments/${id}`, "PUT", patch);
export const deleteEnvironment = (id) =>
  j(`/api/environments/${id}`, { method: "DELETE" });
export const addEnvVariable = (id, variable) =>
  send(`/api/environments/${id}/variables`, "POST", variable);
// One environment value, saved as it is typed - the same shape as a workflow's own
export const setEnvVariable = (id, name, value) =>
  send(`/api/environments/${id}/variables/${encodeURIComponent(name)}`, "PUT", { value });
export const deleteEnvVariable = (id, name) =>
  j(`/api/environments/${id}/variables/${encodeURIComponent(name)}`, { method: "DELETE" });
// The In-use tick: a pure save deciding which instance of a name feeds runs, applied from the next run
export const setVariableUse = (id, name, source) =>
  send(`/api/workflows/${id}/variables/use`, "POST", { name, source });
// The in-flight turn's buffered events and open cards - how a reloaded page finds and follows a running turn
export const getTurn = (id, after = 0) =>
  j(`/api/workflows/${id}/turn${after ? `?after=${after}` : ""}`);

// A refused start carries a reasoned JSON error; callers fork on the attached status, never on message text
async function streamError(r, fallback) {
  const e = await r.json().catch(() => ({}));
  const err = new Error(e.error || fallback);
  err.status = r.status;
  return err;
}

// Drains a response body without reading it for content - the poller is the one display path
function drain(r) {
  (async () => {
    const rd = r.body.getReader();
    try { while (!(await rd.read()).done); } catch { }
  })();
}

// The drawer's if-an-item-doesn't-fit policy - a pure save, mirrored onto the plan
export const setNodeInvalidItems = (id, nodeId, policy) =>
  send(`/api/workflows/${id}/nodes/${nodeId}/invalid-items`, "PUT", { policy });
// The drawer's don't-ask-again toggle for a write step's approval
export const setNodeApproval = (id, nodeId, suppressed) =>
  send(`/api/workflows/${id}/nodes/${nodeId}/approval`, "PUT", { suppressed });
export const setVariable = (id, name, value) =>
  send(`/api/workflows/${id}/variables/${encodeURIComponent(name)}`, "PUT", { value });
export const addVariable = (id, variable) =>
  send(`/api/workflows/${id}/variables`, "POST", variable);
// Refused with 409 while the variable is the value runs actually use
export const deleteVariable = (id, name) =>
  j(`/api/workflows/${id}/variables/${encodeURIComponent(name)}`, { method: "DELETE" });

// Answers a RUN's approval pause (a chat card answers through `say`)
export const answerInteraction = (id, answer) =>
  send(`/api/interactions/${id}`, "PUT", { answer });

// Uploads a file to the blob store and returns its blob: reference
export async function uploadBlob(file, workflowId = "") {
  const buf = await file.arrayBuffer();
  let bin = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000)
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  const res = await send("/api/blobs", "POST",
    { name: file.name, mime: file.type, data_b64: btoa(bin), workflow_id: workflowId });
  return res.ref;
}

// Stops the in-flight run or turn, naming the exact turn so a race can never kill the next one
export const stopWorkflow = (id, turnId) =>
  send(`/api/workflows/${id}/stop`, "POST", turnId ? { turn_id: turnId } : {});
// What the run form must collect before a run can start
export const getRunPreflight = (id) => j(`/api/workflows/${id}/run-preflight`);
// Clears the workflow's attention dot - opening it is the acknowledgement
export const markWorkflowSeen = (id) => send(`/api/workflows/${id}/seen`, "POST", {});
export const deleteSample = (id, name) =>
  send(`/api/workflows/${id}/samples/${encodeURIComponent(name)}`, "DELETE");

// The did-it-land form: what the paused write step would have produced
export const getStepOutputsForm = (id, runId) =>
  j(`/api/workflows/${id}/runs/${runId}/step-outputs`);

// The No on the approval popup: ends the run, kept under Run history to pick up later
export const declineRun = (id, runId) =>
  send(`/api/workflows/${id}/runs/${runId}/decline`, "POST", {});

// The run's outcome was shown; the page stops reopening it on arrival
export const markRunSeen = (id, runId) =>
  send(`/api/workflows/${id}/runs/${runId}/seen`, "POST", {});

// Proceed with the saved results: the run keeps its done items and a second run holds the rest
export const proceedRun = (id, runId) =>
  send(`/api/workflows/${id}/runs/${runId}/proceed`, "POST", {});

// Starts a run; progress arrives through the poller like chat
export async function startRun(id, body) {
  const r = await fetch(`/api/workflows/${id}/run`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw await streamError(r, `${r.status} /api/workflows/${id}/run`);
  drain(r);
}

// Learnings are global - what the assistant found out, shared by every workflow, curated in Admin
export const getLearnings = () => j(`/api/learnings`);
export const deleteLearning = (id) =>
  j(`/api/learnings/${id}`, { method: "DELETE" });
// What one workflow contributed; a learning deleted since is simply absent, never an error
export const getWorkflowLearnings = (id) => j(`/api/workflows/${id}/learnings`);

// Uploads a sample document for the agent to explore against; re-upload replaces by name
export async function uploadSample(workflowId, file) {
  const buf = await file.arrayBuffer();
  let bin = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000)
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return send(`/api/workflows/${workflowId}/samples`, "POST",
    { name: file.name, mime: file.type, data_b64: btoa(bin) });
}

// Marks list outputs where empty is normal, so runs stop flagging them
export const setMayBeEmpty = (id, nodeId, ports) =>
  send(`/api/workflows/${id}/nodes/${nodeId}/may-be-empty`, "PUT", { ports });
// A plain status update on an issue
export const setTicketStatus = (id, ticketId, patch) =>
  send(`/api/workflows/${id}/tickets/${ticketId}`, "PUT",
       typeof patch === "string" ? { status: patch } : patch);
// The did-it-land submit: records the step done with what the user could see
export const submitUserOutputs = (id, ticketId, body) =>
  send(`/api/workflows/${id}/tickets/${ticketId}/user-outputs`, "POST", body);
// Starts the fix turn for an issue
export async function startInvestigate(id, ticketId) {
  const r = await fetch(`/api/workflows/${id}/tickets/${ticketId}/investigate`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }), body: "{}",
  });
  if (!r.ok) throw await streamError(r,
    `The fix could not start (error ${r.status}).`);
  drain(r);
}

export const getSettings = () => j("/api/settings");
export const putSettings = (patch) => send("/api/settings", "PUT", patch);
export const putSecret = (name, value) => send(`/api/secrets/${name}`, "PUT", { value });
// A workflow's own secret (the masked chat ask) lands under that workflow, never the app
export const putWorkflowSecret = (pid, name, value) => send(`/api/workflows/${pid}/secrets/${name}`, "PUT", { value });
export const deleteSecret = (name) => send(`/api/secrets/${name}`, "DELETE");
export const getProviderUsage = () => j("/api/providers/usage");
export const testProviderKey = (body) => send("/api/providers/test", "POST", body);
// The Codex Check connection button: installed and signed in
export const checkCodex = () => send("/api/codex/check", "POST", {});
// Is Claude Code installed and signed in on this computer
export const checkClaude = () => send("/api/claude/check", "POST", {});
// The Refresh models picker: the provider's own live model list
export const refreshProviderModels = (id) => send(`/api/providers/${id}/models`, "POST", {});
export const testProvider = (id) => send(`/api/providers/${id}/test`, "POST", {});
// One workflow's AI token counts by source, all time
export const getWorkflowAiUsage = (id) => j(`/api/workflows/${id}/ai-usage`);
// Version history: the metadata list, and restore to revert the workflow's configuration
export const getWorkflowVersions = (id) => j(`/api/workflows/${id}/versions`);
export const restoreWorkflowVersion = (id, vid) =>
  send(`/api/workflows/${id}/versions/${vid}/restore`, "POST", {});
