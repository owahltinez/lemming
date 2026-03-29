import fastapi


async def share_token_middleware(request: fastapi.Request, call_next):
    share_token = getattr(request.app.state, "share_token", None)
    if not share_token:
        return await call_next(request)

    host = request.headers.get("host", "")
    if host.startswith("127.0.0.1") or host.startswith("localhost"):
        return await call_next(request)

    token = request.query_params.get("token")
    if token == share_token:
        response = await call_next(request)
        response.set_cookie(key="lemming_share_token", value=token, httponly=True)
        return response

    cookie_token = request.cookies.get("lemming_share_token")
    if cookie_token == share_token:
        return await call_next(request)

    return fastapi.Response("Unauthorized", status_code=401)
