import importlib.resources
import mimetypes
import pathlib

import fastapi
import fastapi.responses

from .. import paths

router = fastapi.APIRouter()

# Get the web directory for serving HTML templates
web_dir = pathlib.Path(str(importlib.resources.files("lemming").joinpath("web")))


@router.get("/api/files/{path:path}")
def get_files_api(request: fastapi.Request, path: str):
    base_path = request.app.state.root
    target_path = (base_path / path).resolve()

    if not target_path.is_relative_to(base_path) or paths.is_ignored(target_path):
        raise fastapi.HTTPException(403, "Forbidden")

    if not target_path.is_dir():
        raise fastapi.HTTPException(400, "Not a directory")

    contents = []
    for item in target_path.iterdir():
        if paths.is_ignored(item):
            continue
        rel_path = item.relative_to(base_path)
        is_dir = item.is_dir()
        stats = item.stat()
        contents.append(
            {
                "name": item.name + ("/" if is_dir else ""),
                "path": str(rel_path),
                "is_dir": is_dir,
                "size": None if is_dir else stats.st_size,
                "modified": stats.st_mtime,
            }
        )
    return {
        "path": path,
        "contents": sorted(
            contents, key=lambda x: (not x["is_dir"], x["name"].lower())
        ),
    }


@router.get("/tasks/{task_id}/log")
def serve_task_log(task_id: str):
    return fastapi.responses.FileResponse(web_dir / "logs.html")


@router.get("/files/{path:path}")
def serve_files(request: fastapi.Request, path: str):
    base_path = request.app.state.root
    target_path = (base_path / path).resolve()

    if not target_path.is_relative_to(base_path) or paths.is_ignored(target_path):
        raise fastapi.HTTPException(403, "Forbidden")

    if target_path.is_dir():
        return fastapi.responses.FileResponse(web_dir / "files.html")
    if target_path.is_file():
        # Guess the MIME type to identify binary formats.
        guess, _ = mimetypes.guess_type(target_path)

        # Consider images, video, audio, PDFs, and common archives as "binary" to be served as-is.
        is_binary = guess and (
            guess.startswith(("image/", "video/", "audio/"))
            or guess
            in (
                "application/pdf",
                "application/wasm",
                "application/zip",
                "application/x-zip-compressed",
            )
        )

        # Special case: .ts files are frequently misidentified as video/mp2t.
        if is_binary and target_path.suffix.lower() == ".ts":
            is_binary = False

        if is_binary:
            return fastapi.responses.FileResponse(target_path)

        # For everything else, force text/plain to ensure browser views source code.
        return fastapi.responses.FileResponse(target_path, media_type="text/plain")

    raise fastapi.HTTPException(404, "Not found")


@router.get("/files")
def redirect_files():
    return fastapi.responses.RedirectResponse("/files/")
