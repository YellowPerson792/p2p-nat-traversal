# Web frontend and HTTP backend

The browser frontend lives in `overlay/web/client/` and `overlay/web/admin/`.
Each contains `index.html`, `style.css` and `app.js`. HTML defines the page,
CSS handles appearance, and JavaScript renders API responses and submits actions.
There is no Node runtime, build step or frontend framework required for deployment.

The device dashboard explains the next step for each connection state. It shows
the approved peer port, direction and actual local forwarding address; raw
connection data and fingerprints are expandable. Inputs are validated before
requests, conflicting actions are disabled, and unchanged peer/request lists
are retained during polling to preserve focus. The layout collapses to one
column on narrow screens.

The administrator dashboard loads only after unlocking, shows pending/online/
approved counts, and asks for confirmation before revocation. Locking clears
the credential and displayed membership data. An in-flight status response
cannot unlock it again. Locking the page does not revoke the server token.

`overlay/control.py` implements the device HTTP API and loopback authentication.
`overlay/admin_control.py` implements the administrator API and authorization.
Neither contains HTML, CSS or JavaScript. `overlay/webui.py` only serves packaged
files, including when running the phone zipapp. CLI commands use the same API;
they do not depend on frontend files or browser state.

| Frontend | Page | Assets | API |
| --- | --- | --- | --- |
| Device | `/` | `/ui/app.js`, `/ui/style.css` | `/api/*` |
| Server administrator | `/admin` | `/admin/ui/app.js`, `/admin/ui/style.css` | `/admin/api/*` |

Frontend and backend remain on the same origin by default. Code separation does
not require separate processes or ports. Relative API URLs also allow serving
the static files with a reverse proxy that routes API calls to the Python
backend. That proxy must preserve the original authority/scheme used by origin
checks; the local device backend still requires loopback Host and HTTP access.
Arbitrary cross-origin hosting is deliberately not enabled. Preserve the local
root response's cookie bootstrap when proxying the device UI; replacing it with
an unrelated static server would omit authentication.

Pages contain no inline scripts/styles. CSP permits scripts, styles and requests
only from the same origin. Local assets require the browser's existing device
cookie; server-admin assets are public, while every admin API call requires the
administrator Bearer credential. The frontend keeps that credential in the
password input, not in a URL, HTML template or persistent browser storage.

## Direct remote administration

The coordinator exposes `https://SERVER:8443/admin` directly. This is
different from device control, which intentionally listens on `127.0.0.1`.
The administrator page does not require a local client daemon or test proxy.

When using a self-signed certificate, browsers need a verified trust configuration,
or a server certificate issued by a browser-trusted CA for the chosen address.
Uploading a CA in the device UI configures the Python client, not Edge's trust
store. Verify certificate identity independently before trusting it.

Enter the administrator credential in the remote page. For systemd deployment
its file is `/etc/p2p-overlay/admin.token`; the temporary test copy, if retained,
is `.local/remote/admin.token`. Do not put it in a URL or commit it.

The earlier `http://127.0.0.1:8773/admin` was a test helper: it verified upstream
TLS using the pinned CA and kept the admin credential on its backend. It was not
the production server's binding address or an architectural requirement.

Both server deployment archives/wheels and phone client bundles include the
necessary static files. A deployed process must be updated/restarted to use this
layout; modifying the source tree alone does not update remote releases.

The Port connections list shows one row per approved instance, including peer,
target port/protocol, direction, local listener, connection ID and path. Each
row can be closed independently. Pending outgoing requests can be cancelled
individually. The device-level disconnect action closes every instance; the
shared path preference applies to all current and new connections.
