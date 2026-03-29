import pathlib
import fastapi
from .. import paths


def resolve_project_dir(
    app_state: fastapi.datastructures.State, project: str | None = None
) -> pathlib.Path:
    """Resolve a project query parameter to its absolute directory path."""
    if not project:
        return app_state.root

    root = app_state.root
    target = (root / project).resolve()
    if not target.is_relative_to(root):
        raise fastapi.HTTPException(403, "Path is outside the server root")
    if not target.is_dir():
        raise fastapi.HTTPException(400, "Not a directory")

    return target


def resolve_tasks_file(
    app_state: fastapi.datastructures.State, project: str | None = None
) -> pathlib.Path:
    """Resolve a project query parameter to a tasks file path.

    When *project* is ``None`` or empty the server-wide default is returned.
    Otherwise the value is treated as a relative directory under the server
    root and the tasks file path is derived deterministically.
    """
    if not project:
        return app_state.tasks_file

    return paths.get_tasks_file_for_dir(resolve_project_dir(app_state, project))
