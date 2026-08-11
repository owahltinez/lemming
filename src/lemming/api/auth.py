"""Share-token authentication middleware for remotely shared servers."""

import fastapi


async def share_token_middleware(request: fastapi.Request, call_next):
    """Require a share token for every request when one is configured.

    Accepts the token via the ``token`` query parameter (persisting it in a
    cookie) or via the ``lemming_share_token`` cookie. Returns 401 when the
    token is missing or invalid.

    There is deliberately no local-request exemption. A token is only set in
    tunnel mode, so this is already inert locally, and both a client-supplied
    ``Host`` header and the peer address are forgeable or misleading behind a
    tunnel (the tunnel daemon reaches the origin over loopback, so every
    public request also appears to come from 127.0.0.1). Requiring the token
    unconditionally keeps correctness independent of how each tunnel provider
    handles those.
    """
    share_token = getattr(request.app.state, "share_token", None)
    if not share_token:
        return await call_next(request)

    token = request.query_params.get("token")
    if token == share_token:
        response = await call_next(request)
        response.set_cookie(
            key="lemming_share_token", value=token, httponly=True
        )
        return response

    cookie_token = request.cookies.get("lemming_share_token")
    if cookie_token == share_token:
        return await call_next(request)

    return fastapi.Response("Unauthorized", status_code=401)
