"""Serve packaged frontend files; browser behavior lives in web/, not API handlers.

Resources work both in an installed wheel and in the phone client zipapp.
Only the explicitly registered files are exposed, never arbitrary filesystem paths.
"""
from importlib.resources import files

from fastapi.responses import HTMLResponse, Response


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "frame-ancestors 'none'; connect-src 'self'; base-uri 'none'; form-action 'self'"
)


def resource(frontend, name):
    return files('overlay').joinpath('web', frontend, name).read_bytes()


def page_response(frontend):
    return HTMLResponse(resource(frontend, 'index.html'), headers={
        'Cache-Control': 'no-store',
        'Content-Security-Policy': CONTENT_SECURITY_POLICY,
        'X-Content-Type-Options': 'nosniff',
    })


def install_assets(app, frontend, prefix):
    def endpoint(name, media_type):
        async def serve():
            return Response(resource(frontend, name), media_type=media_type,
                headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return serve

    for name, media_type in [('app.js', 'text/javascript'), ('style.css', 'text/css')]:
        app.add_api_route(prefix + '/' + name, endpoint(name, media_type),
            methods=['GET'], include_in_schema=False)
