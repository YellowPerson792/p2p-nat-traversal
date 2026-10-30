const el = (id) => document.getElementById(id);
function text(tag, value, className = "") {
  const element = document.createElement(tag);
  element.textContent = value;
  element.className = className;
  return element;
}
function notice(id, message = "") {
  el(id).textContent = message;
  el(id).hidden = !message;
}
function empty(container, message) {
  container.replaceChildren(text("p", message, "empty"));
}
function badge(label, tone = "neutral") {
  return text("span", label, "badge " + tone);
}
function fingerprint(parent, value) {
  if (!value) return;
  const detail = document.createElement("details");
  detail.append(
    text("summary", "Identity fingerprint"),
    text("p", value, "fingerprint"),
  );
  parent.append(detail);
}
function action(label, fn, className = "", disabled = false) {
  const button = text("button", label, className);
  button.type = "button";
  button.disabled = disabled;
  button.onclick = () => act(fn);
  return button;
}
function item(name, status, tone) {
  const row = text("article", "", "item");
  const heading = text("div", "", "item-heading");
  heading.append(text("strong", name), badge(status, tone));
  row.append(heading);
  return row;
}

let unlocked = false,
  busy = false,
  refreshing = false,
  current = null,
  signature = "",
  sessionGeneration = 0;
async function api(path, method = "GET", body) {
  let response;
  try {
    response = await fetch("/admin/api/" + path, {
      method,
      headers: {
        Authorization: "Bearer " + el("token").value.trim(),
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(12000),
    });
  } catch {
    throw Error("Cannot reach the server. Check your network and retry.");
  }
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      lock();
      throw Error(
        "Administrator credential was not accepted. Paste the server token and try again.",
      );
    }
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : "The request could not be completed.",
    );
  }
  return data;
}
function controls() {
  el("refresh").disabled = busy;
  el("refresh").textContent = busy
    ? "Working…"
    : unlocked
      ? "Refresh"
      : "Unlock dashboard";
  el("lock").disabled = busy || !unlocked;
  el("token").disabled = busy || unlocked;
  el("session").textContent = unlocked ? "Unlocked" : "Locked";
  el("session").className = "badge " + (unlocked ? "" : "neutral");
}
async function act(fn) {
  if (busy) return;
  busy = true;
  notice("error");
  notice("feedback");
  controls();
  if (current) render(current);
  try {
    await fn();
  } catch (error) {
    notice("error", error.message);
  } finally {
    busy = false;
    controls();
    if (current) render(current);
  }
}
function lock() {
  sessionGeneration++;
  unlocked = false;
  current = null;
  signature = "";
  el("token").value = "";
  for (const id of ["pending-count", "online-count", "member-count"])
    el(id).textContent = "—";
  empty(el("pending"), "Unlock the dashboard to view joining devices.");
  empty(el("members"), "Unlock the dashboard to view members.");
  el("updated").textContent = "Enter a credential to get started.";
  controls();
}
function render(data) {
  const members = Object.entries(data.members);
  el("pending-count").textContent = data.pending.length;
  el("online-count").textContent = members.filter(([, m]) => m.online).length;
  el("member-count").textContent = members.filter(([, m]) => m.enabled).length;
  const next = JSON.stringify([data, busy]);
  if (next === signature) return;
  signature = next;
  el("pending").replaceChildren();
  for (const p of data.pending) {
    const row = item(p.node, "Pending approval", "warning");
    row.append(
      text("p", "Fingerprint — compare with the joining device", "hint"),
      text("p", p.fingerprint, "fingerprint"),
    );
    const actions = text("div", "", "actions");
    for (const accept of [true, false])
      actions.append(
        action(
          accept ? "Approve membership" : "Reject",
          async () => {
            await api("enrollments/" + encodeURIComponent(p.id), "POST", {
              accept,
              fingerprint: p.fingerprint,
            });
            notice(
              "feedback",
              p.node +
                (accept ? " is now an approved member." : " was rejected."),
            );
            await refresh();
          },
          accept ? "" : "secondary",
          busy,
        ),
      );
    row.append(actions);
    el("pending").append(row);
  }
  if (!data.pending.length)
    empty(
      el("pending"),
      "No devices awaiting approval. New join requests will appear automatically.",
    );
  el("members").replaceChildren();
  members.sort(
    (a, b) =>
      Number(b[1].online) - Number(a[1].online) || a[0].localeCompare(b[0]),
  );
  for (const [name, m] of members) {
    const row = item(
      name,
      !m.enabled ? "Revoked" : m.online ? "Online" : "Offline",
      !m.enabled ? "bad" : m.online ? "" : "neutral",
    );
    fingerprint(row, m.fingerprint);
    if (m.enabled) {
      const actions = text("div", "", "actions");
      actions.append(
        action(
          "Revoke membership",
          async () => {
            if (
              !window.confirm(
                "Revoke membership for " +
                  name +
                  "? This will disconnect the device and close its active connections.",
              )
            )
              return;
            await api("members/" + encodeURIComponent(name), "DELETE");
            notice("feedback", "Membership revoked for " + name + ".");
            await refresh();
          },
          "danger",
          busy,
        ),
      );
      row.append(actions);
    }
    el("members").append(row);
  }
  if (!members.length)
    empty(el("members"), "No members yet. Approve a joining device above.");
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  const generation = sessionGeneration;
  try {
    const data = await api("status");
    if (generation !== sessionGeneration) return;
    unlocked = true;
    current = data;
    render(data);
    controls();
    el("updated").textContent =
      "Last updated " +
      new Date().toLocaleTimeString() +
      " · refreshes every 3 seconds";
  } finally {
    refreshing = false;
  }
}
el("login").onsubmit = (event) => {
  event.preventDefault();
  if (el("token").reportValidity()) act(refresh);
};
el("lock").onclick = () => {
  lock();
  notice("error");
  notice("feedback", "Dashboard locked. Credential cleared from this tab.");
};
setInterval(() => {
  if (unlocked && !busy && !document.hidden)
    refresh().catch((error) => notice("error", error.message));
}, 3000);
controls();
