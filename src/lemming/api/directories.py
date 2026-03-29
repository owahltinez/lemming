import fastapi
import pydantic


router = fastapi.APIRouter()


@router.get("/api/directories")
def list_directories(request: fastapi.Request, path: str = ""):
    """List subdirectories under the server root for the project picker."""
    root = request.app.state.root
    target = (root / path).resolve() if path else root
    if not target.is_relative_to(root):
        raise fastapi.HTTPException(403, "Path is outside the server root")
    if not target.is_dir():
        raise fastapi.HTTPException(400, "Not a directory")

    dirs = []
    for item in sorted(target.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            rel = item.relative_to(root)
            dirs.append({"name": item.name, "path": str(rel)})
    return {"path": path, "directories": dirs}


class CreateDirectoryRequest(pydantic.BaseModel):
    path: str = ""
    name: str


@router.post("/api/directories")
def create_directory(request: fastapi.Request, dir_request: CreateDirectoryRequest):
    """Create a new directory under the server root."""
    root = request.app.state.root
    parent = (root / dir_request.path).resolve() if dir_request.path else root
    if not parent.is_relative_to(root):
        raise fastapi.HTTPException(403, "Path is outside the server root")
    if not parent.is_dir():
        raise fastapi.HTTPException(400, "Parent is not a directory")

    new_dir = parent / dir_request.name
    if not new_dir.resolve().is_relative_to(root):
        raise fastapi.HTTPException(403, "Target path is outside the server root")

    if new_dir.exists():
        raise fastapi.HTTPException(400, "Directory already exists")

    new_dir.mkdir()
    rel = new_dir.relative_to(root)
    return {"name": new_dir.name, "path": str(rel)}
