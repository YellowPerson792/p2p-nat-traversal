"""Remote HTTPS administrator UI; the same API is used by the CLI."""
import secrets

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .webui import install_assets, page_response
from .identity import revoke
from .management import credential_file


class EnrollmentDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    accept: bool = Field(strict=True)
    fingerprint: str = ''


def install_admin(app, directory, token, registry):
    install_assets(app, "admin", "/admin/ui")
    token = token or credential_file(str(registry) + '.admin-token')

    def authorize(request):
        supplied = request.headers.get('authorization', '')
        if not secrets.compare_digest(supplied.encode(), ('Bearer ' + token).encode()):
            raise HTTPException(401, 'Administrator credential required')
        origin = request.headers.get('origin')
        if origin is not None and origin != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'Cross-origin request rejected')

    @app.get('/admin')
    async def page():
        return page_response("admin")

    @app.get('/admin/api/status')
    async def status(request: Request):
        authorize(request)
        return directory.admin_status()

    @app.post('/admin/api/enrollments/{key}')
    async def decide(key: str, decision: EnrollmentDecision, request: Request):
        authorize(request)
        try:
            directory.decide_enrollment(key, decision.accept, decision.fingerprint)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {'accepted': decision.accept}

    @app.delete('/admin/api/members/{node}')
    async def remove(node: str, request: Request):
        authorize(request)
        try:
            revoke(registry, node)
            directory.coord.policy.load(force=True)
            await directory.reap()
        except (ValueError, KeyError) as exc:
            raise HTTPException(404, 'Member not found') from exc
        return {'revoked': node}
