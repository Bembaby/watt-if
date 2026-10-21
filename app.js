"use strict";
const $ = (id) => document.getElementById(id);
let home = null,
  proposal = null,
  busy = false,
  requestId = 0,
  unresolved = false,
  dirty = false,
  visiblePlan = null;
let runtimeLabel = "Local AI";
const runtimeIdle = () => `${runtimeLabel}. Review every proposed time.`;
const times = Array.from(
  { length: 13 },
  (_, i) => `${18 + Math.floor(i / 2)}:${i % 2 ? "30" : "00"}`,
);
const minutes = (time) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
const shortTime = (time) =>
  time === "24:00"
    ? "midnight"
    : `${Number(time.slice(0, 2)) % 12 || 12}${time.endsWith(":30") ? ":30" : ""} pm`;
const defaultConstraints = () => ({
  quiet_after: "21:00",
  jobs: Object.fromEntries(
    home.jobs.map((j) => [
      j.id,
      { earliest: "18:00", latest_finish: "24:00", skip: false, exact_start: null },
    ]),
  ),
});
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "The local server returned an unreadable response. Check that Watt If is running.",
    );
  }
  if (!response.ok) {
    const error = new Error(
      data.error || data.message || `Request failed (${response.status}).`,
    );
    error.data = data;
    throw error;
  }
  return data;
}
function setBusy(value) {
  busy = value;
  $("workbench").setAttribute("aria-busy", String(value));
  document
    .querySelectorAll("button,select,input")
    .forEach((n) => (n.disabled = value));
  $("approve").disabled = value || dirty || unresolved || !proposal || proposal.status !== "proposed";
  $("message").disabled = value;
  if (value) {
    $("download").hidden = true;
    $("approve").hidden = false;
  }
}
function phase(text, state = "idle") {
  $("plan-state").textContent = text;
  $("plan-state").dataset.state = state;
}
function pendingPlan(text, state = "pending") {
  clearApproval();
  proposal = null;
  unresolved = true;
  if (visiblePlan) $("plan-content").hidden = false;
  phase(text, state);
}
function reviewConstraints(plan, previous) {
  const out = $("constraint-review");
  out.replaceChildren();
  out.append(el("p", "quiet-summary", `Quiet after ${shortTime(plan.constraints.quiet_after)}. Noisy chores must finish first.`));
  for (const job of home.jobs) {
    const c = plan.constraints.jobs[job.id];
    const row = el("div", "constraint-row");
    row.append(el("strong", null, job.name.replace("Optional ", "")));
    row.append(el("span", null, c.skip ? "Left out. Restore it explicitly to schedule it again." : `${c.exact_start ? 'Start exactly at ' + shortTime(c.exact_start) : 'Not before ' + shortTime(c.earliest)}; finished by ${shortTime(c.latest_finish)}.`));
    const old = previous?.schedule.find(j => j.job_id === job.id);
    const current = plan.schedule.find(j => j.job_id === job.id);
    if (old && current && (old.start !== current.start || old.end !== current.end))
      row.append(el("small", "change-note", `Was ${shortTime(old.start)} to ${shortTime(old.end)}. Now ${shortTime(current.start)} to ${shortTime(current.end)}.`));
    out.append(row);
  }
}
function say(text, role = "assistant") {
  const node = el(
    "p",
    role === "user" ? "user-message" : "assistant-message",
    text,
  );
  $("messages").append(node);
  $("messages").scrollTop = $("messages").scrollHeight;
}
function showError(message) {
  $("plan-error").textContent = message;
  $("plan-error").hidden = false;
}
function clearApproval() {
  $("download").hidden = true;
  $("download").removeAttribute("href");
  $("approve").hidden = false;
  $("approve").disabled = true;
  $("approval-title").textContent = "A proposal, ready for your review.";
  $("approval-caption").textContent =
    "Nothing runs automatically. Approving creates an advisory calendar for fictional October 1.";
}
function makeSelect(id, value) {
  const select = el("select");
  select.id = id;
  times.forEach((time) => {
    const option = el(
      "option",
      null,
      time === "24:00" ? "Midnight" : shortTime(time),
    );
    option.value = time;
    select.append(option);
  });
  select.value = value;
  return select;
}
function controls(constraints) {
  $("quiet").replaceChildren(
    ...times.map((time) => {
      const option = el(
        "option",
        null,
        time === "24:00" ? "Midnight" : shortTime(time),
      );
      option.value = time;
      return option;
    }),
  );
  $("quiet").value = constraints.quiet_after;
  $("job-controls").replaceChildren();
  for (const job of home.jobs) {
    const group = el("div", "job-control");
    group.append(el("h4", null, job.name));
    for (const [key, title] of [
      ["earliest", "Start no earlier than"],
      ["latest_finish", "Finish by"],
    ]) {
      const wrapper = el("div");
      const id = `${job.id}-${key}`;
      const label = el("label", null, title);
      label.htmlFor = id;
      wrapper.append(label, makeSelect(id, constraints.jobs[job.id][key]));
      group.append(wrapper);
    }
    const exactLabel = el("label", "skip-label");
    const exactInput = el("input");
    exactInput.type = "checkbox";
    exactInput.id = `${job.id}-exact`;
    exactInput.checked = !!constraints.jobs[job.id].exact_start;
    exactLabel.append(exactInput, document.createTextNode("Start exactly at the chosen start time"));
    group.append(exactLabel);
    const label = el("label", "skip-label");
    const input = el("input");
    input.type = "checkbox";
    input.id = `${job.id}-skip`;
    input.checked = constraints.jobs[job.id].skip;
    label.append(
      input,
      document.createTextNode("Leave this chore out tonight"),
    );
    group.append(label);
    $("job-controls").append(group);
  }
}
function drawTimeline(plan) {
  const axis = el("div", "time-axis");
  axis.append(el("span", null, "Fictional Oct 1"));
  const ticks = el("div", "ticks");
  ["18:00", "19:00", "20:00", "21:00", "22:00", "23:00", "24:00"].forEach((t) =>
    ticks.append(
      el(
        "span",
        null,
        t === "24:00" ? "12 am" : `${Number(t.slice(0, 2)) - 12} pm`,
      ),
    ),
  );
  axis.append(ticks);
  $("timeline").replaceChildren(axis);
  for (const job of home.jobs) {
    const row = el("div", "time-row");
    const name = el("div", "job-name", job.name.replace("Optional ", ""));
    name.append(
      el(
        "small",
        null,
        `${job.duration_minutes} min · ${(job.watts / 1000).toFixed(1)} kW`,
      ),
    );
    const track = el("div", "track");
    const item = plan.schedule.find((x) => x.job_id === job.id);
    if (item) {
      const bar = el(
        "div",
        `bar ${job.id}`,
        `${shortTime(item.start)} – ${shortTime(item.end)}`,
      );
      bar.style.left = `${((minutes(item.start) - 1080) / 360) * 100}%`;
      bar.style.width = `${(item.duration_minutes / 360) * 100}%`;
      bar.title = `${job.name}: ${item.start}–${item.end}`;
      track.append(bar);
    } else track.append(el("span", "omitted", "Not included tonight"));
    if (job.noisy) {
      const quiet = el("span", "quiet-line");
      quiet.style.left = `${((minutes(plan.constraints.quiet_after) - 1080) / 360) * 100}%`;
      quiet.title = `No noisy chores after ${plan.constraints.quiet_after}`;
      track.append(quiet);
    }
    row.append(name, track);
    $("timeline").append(row);
  }
}
function metric(label, value, note) {
  const item = el("div", "metric");
  item.append(
    el("span", null, label),
    el("strong", null, value),
    el("small", null, note),
  );
  return item;
}
function drawChart(plan) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 600 84");
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    "Modeled household load before and after scheduling, from 6 pm to midnight",
  );
  const all = [...plan.metrics.load_watts, ...plan.baseline_metrics.load_watts];
  const max = Math.max(...all, 1);
  for (const [loads, color] of [
    [plan.baseline_metrics.load_watts, "#8f96b3"],
    [plan.metrics.load_watts, "#9c9aff"],
  ]) {
    const poly = document.createElementNS(ns, "polyline");
    poly.setAttribute(
      "points",
      loads
        .map(
          (w, i) =>
            `${4 + (i * 592) / (loads.length - 1)},${78 - (w / max) * 68}`,
        )
        .join(" "),
    );
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", color);
    poly.setAttribute("stroke-width", "3");
    if (loads === plan.baseline_metrics.load_watts) poly.setAttribute("stroke-dasharray", "7 5");
    svg.append(poly);
  }
  const key = el("div", "chart-key");
  key.append(
    el("span", null, "Same chores at earliest allowed starts"),
    el("span", null, "Proposed timing"),
  );
  $("load-chart").replaceChildren(key, svg);
}
function renderPlan(plan) {
  const previous = visiblePlan;
  proposal = plan;
  dirty = false;
  unresolved = false;
  home.revision = plan.revision;
  home.constraints = plan.constraints;
  delete home.pending_request;
  $("replacement-control").hidden = true;
  $("replace-pending").checked = false;
  $("retry-request").hidden = true;
  clearApproval();
  $("empty-plan").hidden = true;
  $("plan-error").hidden = true;
  $("revision").textContent = `Revision ${plan.revision}`;
  controls(plan.constraints);
  if (plan.status !== "proposed" || !plan.metrics) {
    $("plan-content").hidden = !visiblePlan;
    phase(visiblePlan ? `Conflict in revision ${plan.revision}. The timetable below is the previous plan, not a solution to this request.` : `Conflict in revision ${plan.revision}. No feasible timetable.`, "conflict");
    showError(`These times cannot fit. ${plan.explanation}`);
    $("plan-caption").textContent =
      "No calendar can be approved for this request.";
    return;
  }
  $("plan-content").hidden = false;
  phase(`Revision ${plan.revision} checked. Review the constraints before approving.`, "review");
  reviewConstraints(plan, previous);
  visiblePlan = plan;
  $("plan-caption").textContent =
    `Quiet time after ${shortTime(plan.constraints.quiet_after)} for noisy chores. Dashed lines mark the cutoff.`;
  drawTimeline(plan);
  const m = plan.metrics,
    b = plan.baseline_metrics;
  $("metrics").replaceChildren(
    metric(
      "Modeled peak",
      `${(m.peak_watts / 1000).toFixed(2)} kW`,
      `Earliest starts: ${(b.peak_watts / 1000).toFixed(2)} kW`,
    ),
    metric(
      "Same included chores",
      `${m.energy_kwh.toFixed(2)} kWh`,
      "Timing changes, energy stays the same",
    ),
    metric(
      "Fictional tariff total",
      `$${m.cost_dollars.toFixed(2)}`,
      `Earliest starts: $${b.cost_dollars.toFixed(2)}`,
    ),
  );
  drawChart(plan);
  $("skipped").hidden = !plan.skipped.length;
  $("skipped").textContent = plan.skipped.length
    ? `Left out: ${plan.skipped.map((id) => home.jobs.find((j) => j.id === id).name).join(", ")}. This is a smaller scenario, not an efficiency improvement.`
    : "";
  $("explanation").textContent =
    plan.explanation +
    (plan.optimality_proven
      ? " The bounded solver proved this result for these inputs."
      : " Search stopped before optimality was proved.");
  $("approve").disabled = busy;
}
async function refresh() {
  const data = await api("/api/household");
  home = data.household;
  const pending = home.pending_request;
  $("date-label").textContent = "Fictional October 1 · 6 pm to midnight";
  controls(home.constraints);
  if (home.latest_proposal) renderPlan(home.latest_proposal);
  if (pending) {
    home.pending_request = pending;
    $("message").value = home.pending_request.message;
    $("retry-request").hidden = false;
    $("replacement-control").hidden = false;
    pendingPlan("An earlier request still needs a plan. The previous timetable is not its answer. Retry or preview replacement manual constraints.", "failed");
  }
}
async function plan(constraints) {
  if (busy) return;
  const serial = ++requestId;
  const expectedRevision = home.revision;
  pendingPlan("Checking your manual constraints. The previous timetable is awaiting replacement.");
  setBusy(true);
  $("plan-error").hidden = true;
  $("request-status").textContent = "Checking the schedule…";
  try {
    const body = { constraints, expected_revision: expectedRevision };
    if (home.pending_request && $("replace-pending").checked) body.resolve_request_id = home.pending_request.id;
    const data = await api("/api/plan", body);
    if (serial !== requestId) return;
    renderPlan(data.proposal);
    $("trace-caption").textContent =
      "This preview used the deterministic planner directly. It is not an AI run.";
    $("trace").textContent = "";
  } catch (error) {
    showError(error.message);
    pendingPlan("Manual preview failed. The previous timetable is not an answer to these edits. Reload if another tab changed the household.", "failed");
  } finally {
    setBusy(false);
    $("request-status").textContent = runtimeIdle();
  }
}
$("default-plan").addEventListener("click", () => plan(defaultConstraints()));
const resetButton = el("button", "secondary", "Reset the example");
resetButton.type = "button";
resetButton.id = "reset-example";
$("constraint-form").append(resetButton);
resetButton.addEventListener("click", () => plan(defaultConstraints()));
$("constraint-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const constraints = { quiet_after: $("quiet").value, jobs: {} };
  for (const job of home.jobs)
    constraints.jobs[job.id] = {
      earliest: $(`${job.id}-earliest`).value,
      latest_finish: $(`${job.id}-latest_finish`).value,
      skip: $(`${job.id}-skip`).checked,
      exact_start: $(`${job.id}-exact`).checked ? $(`${job.id}-earliest`).value : null,
    };
  plan(constraints);
});
$("constraint-form").addEventListener("input", () => {
  dirty = true;
  clearApproval();
  phase("Manual constraints changed. Preview them before approving a calendar.", "pending");
  $("approval-title").textContent = "Times changed in the controls.";
  $("approval-caption").textContent =
    "Preview these times before approving a calendar.";
});
document.querySelectorAll("[data-prompt]").forEach((button) =>
  button.addEventListener("click", () => {
    $("message").value = button.dataset.prompt;
    $("message").focus();
  }),
);
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("message").value.trim();
  if (!message || busy) return;
  const serial = ++requestId;
  say(message, "user");
  pendingPlan("Planning your new request. The previous timetable stays visible until a checked replacement is ready.");
  $("empty-plan").hidden = true;
  $("plan-error").hidden = true;
  setBusy(true);
  const started = Date.now();
  const timer = setInterval(() => {
    $("request-status").textContent =
      `${runtimeLabel} is working… ${Math.floor((Date.now() - started) / 1000)}s`;
  }, 1000);
  try {
    const data = await api("/api/chat", { message });
    if (serial !== requestId) return;
    if (data.reply) say(data.reply);
    if (data.proposal) renderPlan(data.proposal);
    else {
      if (data.pending_request) {
        home.pending_request = data.pending_request;
        $("message").value = home.pending_request.message;
        $("replacement-control").hidden = false;
      }
      $("retry-request").hidden = false;
      pendingPlan("Clarification needed. Your request is retained; the previous timetable is not approved for it.", "failed");
      showError(
        data.error ||
          "The assistant did not create a validated proposal. Clarify your request or use the time controls.",
      );
    }
    $("trace").textContent = JSON.stringify(data.trace || {}, null, 2);
    $("trace-caption").textContent = data.trace
      ? `Recorded execution from this request. ${data.trace.provider || "Configured model"} · ${data.trace.model_id || ""} · ${data.trace.execution_location || "location unrecorded"}. A trace shows tool activity, not proof that every interpretation is correct.`
      : "No model trace was returned.";
    if (data.proposal) $("message").value = "";
  } catch (error) {
    showError(error.message);
    say(error.message);
    $("retry-request").hidden = false;
    if (error.data?.pending_request) {
      home.pending_request = error.data.pending_request;
      $("message").value = home.pending_request.message;
      $("replacement-control").hidden = false;
    }
    pendingPlan("The request did not finish. Your text is retained. Retry it or use manual controls; the previous plan cannot be newly approved.", "failed");
    $("trace").textContent = error.data?.trace
      ? JSON.stringify(error.data.trace, null, 2)
      : "";
    $("trace-caption").textContent = error.data?.trace
      ? "Recorded failed execution. No proposal was saved."
      : "The request failed. No successful model execution is claimed.";
  } finally {
    clearInterval(timer);
    setBusy(false);
    $("request-status").textContent = runtimeIdle();
  }
});
$("retry-request").addEventListener("click", () => $("chat-form").requestSubmit());
$("approve").addEventListener("click", async () => {
  if (!proposal || busy) return;
  const p = proposal;
  setBusy(true);
  try {
    const data = await api("/api/approve", {
      proposal_id: p.id,
      revision: p.revision,
    });
    if (proposal?.id !== p.id) return;
    const approval = data.approval;
    if (!approval?.export_url?.startsWith("/api/export?"))
      throw new Error(
        "The server did not provide a valid local calendar link.",
      );
    $("approval-title").textContent =
      `Advisory calendar approved · revision ${approval.revision}`;
    $("approval-caption").textContent =
      "For fictional October 1. Review the dates before importing it. No appliance was started.";
    $("download").href = approval.export_url;
    $("download").setAttribute("download", "watt-if-fictional-october-1.ics");
    $("download").hidden = false;
    $("approve").hidden = true;
    phase(`Approved revision ${approval.revision}. Download creates advice, not appliance actions.`, "approved");
  } catch (error) {
    showError(error.message);
    $("approve").disabled = true;
  } finally {
    setBusy(false);
  }
});
(async () => {
  setBusy(true);
  try {
    await refresh();
    const status = await api("/api/status");
    runtimeLabel = status.execution_location === "runpod_ssh_tunnel" ? "RunPod AI via SSH" : "Local AI";
    $("request-status").textContent = runtimeIdle();
    $("model-status").textContent = status.ready
      ? `${runtimeLabel} · ${status.model_id || "Ollama"} installed`
      : `${runtimeLabel} is offline`;
    $("model-status").classList.toggle("ready", status.ready === true);
    if (!status.ready) {
      $("request-status").textContent =
        "Manual time controls remain available.";
      showError(
        status.error ||
          "Connect the configured Ollama model to use the assistant. Manual planning is available.",
      );
    }
  } catch (error) {
    showError(error.message);
    $("model-status").textContent = "Local server unavailable";
  } finally {
    setBusy(false);
    if (!home)
      document.querySelectorAll("button").forEach((b) => (b.disabled = true));
  }
})();
$("download").addEventListener("click", () => phase("Calendar download requested. It has not been imported into any calendar or sent to a device.", "exported"));
