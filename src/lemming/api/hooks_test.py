from unittest.mock import patch


def test_list_hooks(client, test_tasks):
    """Test that the /api/hooks endpoint returns a list of available hooks."""
    with patch("lemming.prompts.list_hooks") as mock_list_hooks:
        mock_list_hooks.return_value = ["roadmap", "readability", "testing"]

        response = client.get("/api/hooks")

        assert response.status_code == 200
        assert response.json() == ["roadmap", "readability", "testing"]
        mock_list_hooks.assert_called_once()


def test_list_hooks_with_project(client, test_tasks):
    """Test that the /api/hooks endpoint works with a specified project."""
    import lemming.api

    root = lemming.api.app.state.root
    project_dir = root / "project1"
    project_dir.mkdir()
    tasks_file = project_dir / "tasks.yml"
    tasks_file.touch()

    with patch("lemming.prompts.list_hooks") as mock_list_hooks:
        mock_list_hooks.return_value = ["roadmap"]

        response = client.get("/api/hooks", params={"project": "project1"})

        assert response.status_code == 200
        assert response.json() == ["roadmap"]
        assert mock_list_hooks.called
