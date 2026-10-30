"""Browser and CLI share the same local device-management HTTP API."""
import secrets
import hashlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .webui import install_assets, page_response, CONTENT_SECURITY_POLICY


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Setup(Input):
    server: str = Field(min_length=1, max_length=512)
    ca_pem: str | None = Field(default=None, max_length=32768)
    ca_path: str | None = Field(default=None, max_length=4096)


class Identity(Input):
    node: str = Field(min_length=1, max_length=64)


class LoadIdentity(Identity):
    path: str | None = Field(default=None, max_length=4096)
    key_pem: str | None = Field(default=None, max_length=16384, repr=False)


class Peer(Input):
    peer: str = Field(min_length=1, max_length=64)
    port: int = Field(ge=1, le=65535, strict=True)
    protocol: str = 'tcp'
    local_port: int = Field(default=0, ge=0, le=65535, strict=True)


class Decision(Input):
    accept: bool = Field(strict=True)


class Mode(Input):
    mode: str


def create_app(device, token):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_assets(app, "client", "/ui")
    # Cookies are scoped by host, not port. Distinct local daemons must not
    # overwrite each other's session when opened in the same browser.
    cookie_name = 'overlay_control_' + hashlib.sha256(token.encode()).hexdigest()[:16]

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        authority = request.headers.get('host', '')
        if authority.split(':')[0] != '127.0.0.1':
            return JSONResponse({'detail': 'Loopback Host required'}, status_code=403)
        origin = request.headers.get('origin')
        if origin is not None and origin != 'http://' + authority:
            return JSONResponse({'detail': 'Cross-origin request rejected'}, status_code=403)
        if request.url.path != '/':
            header = request.headers.get('authorization', '')
            cookie = request.cookies.get(cookie_name, '')
            if not (secrets.compare_digest(header.encode(), ('Bearer ' + token).encode())
                    or secrets.compare_digest(cookie.encode(), token.encode())):
                return JSONResponse({'detail': 'Local management credential required'}, status_code=401)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = CONTENT_SECURITY_POLICY
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(OSError)
    async def io_error(request, exc):
        return JSONResponse({'detail': 'Local file or network operation failed: ' + type(exc).__name__}, status_code=400)

    @app.get('/')
    async def page():
        response = page_response("client")
        # Browser access is granted only on loopback, with strict same-site cookies.
        # The CLI uses the same backend with a local Bearer credential.
        response.set_cookie(cookie_name, token, httponly=True, samesite='strict', path='/')
        return response

    @app.get('/api/status')
    async def status():
        return device.status()

    @app.post('/api/setup')
    async def setup(spec: Setup):
        return await device.setup(**spec.model_dump())

    @app.post('/api/identity/generate')
    async def generate(spec: Identity):
        return await device.generate(spec.node)

    @app.post('/api/identity/load')
    async def load(spec: LoadIdentity):
        return await device.load_identity(spec.node, spec.path, spec.key_pem)

    @app.post('/api/join')
    async def join():
        await device.join()
        return {'state': device.state}

    @app.post('/api/leave')
    async def leave():
        await device.leave()
        return {'state': device.state}

    @app.post('/api/requests', status_code=202)
    async def request_connection(spec: Peer):
        key = await device.request(spec.peer, spec.port, spec.protocol, spec.local_port)
        return {'queued': True, 'id': key}

    @app.post('/api/requests/{key}', status_code=202)
    async def respond(key: str, spec: Decision):
        await device.respond(key, spec.accept)
        return {'queued': True}

    @app.post('/api/disconnect')
    async def disconnect():
        await device.disconnect()
        return {'disconnected': True}

    @app.delete('/api/connections/{key}')
    async def close_connection(key: str):
        await device.disconnect(key)
        return {'disconnected': key}

    @app.post('/api/mode')
    async def mode(spec: Mode):
        await device.mode(spec.mode)
        return {'mode': spec.mode}

    return app
