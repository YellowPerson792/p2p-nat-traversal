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

let initialized = false,
  busy = false,
  refreshing = false,
  current = null;
let peerSignature = "",
  requestSignature = "",
  identitySignature = "",
  connectionSignature = "";
async function api(path, method = "GET", body) {
  let response;
  try {
    response = await fetch("/api/" + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(12000),
    });
  } catch {
    throw Error(
      "Cannot reach local control. Check that the client is running, then retry.",
    );
  }
  const data = await response.json();
  if (!response.ok)
    throw Error(
      response.status === 401
        ? "Your local session expired. Reload this page to reconnect."
        : typeof data.detail === "string"
          ? data.detail
          : "Check your input and try again.",
    );
  return data;
}
async function act(fn, message = "") {
  if (busy) return;
  busy = true;
  notice("error");
  notice("feedback");
  if (current) render(current);
  try {
    await fn();
    if (message) notice("feedback", message);
  } catch (error) {
    notice("error", error.message);
  } finally {
    busy = false;
    if (current) render(current);
  }
}
function valid(id) {
  const field = el(id),
    disabled = field.disabled;
  field.disabled = false;
  const accepted = field.reportValidity();
  field.disabled = disabled;
  if (!accepted) throw Error("Check the highlighted field.");
  return field.value.trim();
}
function render(s) {
  const offline = s.state === "offline",
    online = s.state === "online";
  const labels = {
    offline: "Offline",
    connecting: "Connecting",
    awaiting_admin: "Awaiting approval",
    online: "Online",
  };
  el("state").textContent = labels[s.state] || s.state;
  el("state").className =
    "badge " + (online ? "" : offline ? "neutral" : "warning");
  el("device-name").textContent = s.identity?.node || "No identity yet";
  let title, guidance;
  if (!s.identity) {
    title = "Start with your device identity";
    guidance = "Give this device a name and create an identity below.";
  } else if (!s.server) {
    title = "Choose a coordination server";
    guidance = "Enter a server address, save settings, then join.";
  } else if (s.state === "awaiting_admin") {
    title = "Waiting for the server administrator";
    guidance =
      "Your join request was sent. Ask the administrator to approve this device. Requests expire after five minutes.";
  } else if (s.state === "connecting") {
    title = "Contacting your server…";
    guidance = "Establishing a connection and proving your device identity.";
  } else if (online) {
    title = s.connections.length ? s.connections.length + " port connection(s) active" : "Ready to connect";
    guidance =
      "Choose an available device and request its application port, or review an incoming request.";
  } else {
    title = "Not connected to the server";
    guidance =
      "Check your settings, then select Join server. If this client uses the phone cellular bridge, make sure that bridge is running.";
  }
  el("summary").textContent = title;
  el("guidance").textContent = guidance;
  el("server-error").textContent = s.error
    ? "Connection detail: " + s.error
    : "";
  el("server-error").hidden = !s.error;
  const identityKey = JSON.stringify(s.identity);
  if (identityKey !== identitySignature) {
    identitySignature = identityKey;
    el("identity").replaceChildren();
    if (s.identity) {
      el("identity").append(text("strong", s.identity.node));
      fingerprint(el("identity"), s.identity.fingerprint);
    } else el("identity").textContent = "No identity selected";
  }
  if (!initialized) {
    el("server").value = s.server || "";
    el("node").value = s.identity?.node || "";
    el("forward").value = 0;
    el("mode").value = s.mode;
    initialized = true;
  }
  for (const id of [
    "node",
    "generate",
    "keyfile",
    "load",
    "server",
    "cafile",
    "save",
  ])
    el(id).disabled = busy || !offline;
  el("settings-hint").hidden = offline;
  el("join").disabled = busy || !offline || !s.identity || !s.server;
  el("leave").disabled = busy || offline;
  el("disconnect").disabled = busy || !(s.connections.length || s.requests.length);
  el("apply").disabled = busy;
  el("refresh").disabled = busy;
  const blocked = busy || !online || s.connections.length + s.requests.length >= 16;
  el("target").disabled = blocked;
  el("forward").disabled = blocked;
  el("protocol").disabled = blocked;
  el("peer-count").textContent =
    s.peers.filter((p) => p.available).length + " available";
  const peersKey = JSON.stringify([s.peers, blocked, online]);
  if (peersKey !== peerSignature) {
    peerSignature = peersKey;
    el("peers").replaceChildren();
    for (const p of s.peers) {
      const row = item(
        p.node,
        p.available ? "Available" : "At connection limit",
        p.available ? "" : "neutral",
      );
      fingerprint(row, p.fingerprint);
      const actions = text("div", "", "actions");
      actions.append(
        action(
          "Request access",
          async () => {
            const port = Number(valid("target"));
            const localPort = Number(valid("forward"));
            await api("requests", "POST", {
              peer: p.node,
              port,
              local_port: localPort,
              protocol: el("protocol").value,
            });
            notice(
              "feedback",
              "Request sent to " + p.node + ". Waiting for its approval.",
            );
            await refresh();
          },
          "",
          blocked || !p.available,
        ),
      );
      row.append(actions);
      el("peers").append(row);
    }
    if (!s.peers.length)
      empty(
        el("peers"),
        online
          ? "No other devices are online. Join the same server on another device."
          : "Join the server to discover other devices.",
      );
  }
  el("request-count").textContent = s.requests.length;
  const requestsKey = JSON.stringify([s.requests, busy]);
  if (requestsKey !== requestSignature) {
    requestSignature = requestsKey;
    el("requests").replaceChildren();
    for (const q of s.requests) {
      const incoming = q.direction === "incoming";
      const row = item(
        q.peer,
        incoming ? "Needs your approval" : "Waiting for peer",
        "warning",
      );
      row.append(
        text(
          "p",
          incoming
            ? "Requests access to YOUR " +
                q.protocol.toUpperCase() +
                " port " +
                q.port +
                "."
            : "You requested " +
                q.protocol.toUpperCase() +
                " port " +
                q.port +
                " on this device.",
        ),
      );
      fingerprint(row, q.fingerprint);
      if (incoming) {
        const actions = text("div", "", "actions");
        for (const accept of [true, false])
          actions.append(
            action(
              accept ? "Approve port access" : "Reject",
              async () => {
                await api("requests/" + encodeURIComponent(q.id), "POST", {
                  accept,
                });
                notice(
                  "feedback",
                  accept ? "Port access approved." : "Request rejected.",
                );
                await refresh();
              },
              accept ? "" : "secondary",
              busy,
            ),
          );
        row.append(actions);
      }
      if (!incoming) {
        const actions = text("div", "", "actions");
        actions.append(action("Cancel this request", async () => {
          await api("connections/" + encodeURIComponent(q.id), "DELETE");
          await refresh();
        }, "secondary", busy));
        row.append(actions);
      }
      el("requests").append(row);
    }
    if (!s.requests.length)
      empty(el("requests"), "No pending connection requests.");
  }
  el("path").textContent = s.connections.length + " active";
  el("path").className = "badge " + (s.connections.length ? "" : "neutral");
  const signature = JSON.stringify([s.connections.map(c => ({id:c.id, peer:c.peer,
    target_port:c.target_port, protocol:c.protocol, direction:c.direction,
    connected:c.connected, path:c.path, forwards:c.forwards, channels:c.channels})), busy]);
  if (signature !== connectionSignature) {
    connectionSignature = signature;
    el("connections").replaceChildren();
    for (const c of s.connections) {
      const row = item(c.peer, c.connected ? c.path : "Establishing…", c.connected ? "" : "warning");
      row.append(text("p", (c.direction === "outgoing" ? "You requested " : "Peer requested your ")
        + c.protocol.toUpperCase() + " port " + c.target_port + " · " + c.channels + " open channel(s)"));
      row.append(text("small", "Connection ID: " + c.id, "fingerprint"));
      for (const f of c.forwards) {
        const endpoint = text("div", "", "endpoint");
        endpoint.append(text("small", "Connect your application here"),
          text("code", f.address[0] + ":" + f.address[1])); row.append(endpoint);
      }
      const actions = text("div", "", "actions");
      actions.append(action("Close this connection", async () => {
        await api("connections/" + encodeURIComponent(c.id), "DELETE");
        notice("feedback", "Connection closed. Other port connections are unchanged.");
        await refresh();
      }, "danger", busy)); row.append(actions); el("connections").append(row);
    }
    if (!s.connections.length) empty(el("connections"), "No active port connections. You can request multiple ports on the same or different devices.");
  }
  el("connection").textContent = s.connections.length
    ? JSON.stringify(s.connections, null, 2) : "No active peer connections";
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    current = await api("status");
    render(current);
    el("updated").textContent =
      "Last updated " + new Date().toLocaleTimeString();
  } finally {
    refreshing = false;
  }
}
el("generate").onclick = () =>
  act(async () => {
    await api("identity/generate", "POST", { node: valid("node") });
    await refresh();
  }, "Identity created. Save your server settings next.");
el("load").onclick = () =>
  act(async () => {
    const node = valid("node"),
      file = el("keyfile").files[0];
    if (!file) throw Error("Select an Ed25519 private key file first.");
    await api("identity/load", "POST", { node, key_pem: await file.text() });
    el("keyfile").value = "";
    await refresh();
  }, "Identity loaded.");
el("save").onclick = () =>
  act(async () => {
    const body = {
        server: valid("server"),
      },
      ca = el("cafile").files[0];
    if (ca) body.ca_pem = await ca.text();
    await api("setup", "POST", body);
    el("cafile").value = "";
    await refresh();
  }, "Settings saved. You can now join the server.");
for (const op of ["join", "leave", "disconnect"])
  el(op).onclick = () =>
    act(async () => {
      await api(op, "POST");
      await refresh();
    }, { join: "Join started. Follow the connection status above.", leave: "Left the server.", disconnect: "All peer connections and pending requests closed." }[op]);
el("apply").onclick = () =>
  act(async () => {
    await api("mode", "POST", { mode: el("mode").value });
    await refresh();
  }, "Path preference saved.");
el("refresh").onclick = () => act(refresh);
refresh().catch((error) => notice("error", error.message));
setInterval(() => {
  if (!busy && !document.hidden)
    refresh().catch((error) => notice("error", error.message));
}, 2500);
