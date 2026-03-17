from lemming import agent


def test_load_prompt():
    prompt = agent.load_prompt("taskrunner")
    assert "roadmap" in prompt
    assert "description" in prompt


def test_build_agent_command_gemini():
    cmd = agent.build_agent_command("gemini", "my prompt", yolo=True)
    assert "--yolo" in cmd
    assert "--prompt" in cmd
    assert "my prompt" in cmd


def test_build_agent_command_aider():
    cmd = agent.build_agent_command("aider", "my prompt", yolo=True)
    assert "--yes" in cmd
    assert "--message" in cmd


def test_prepare_prompt(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = {
        "context": "My context",
        "tasks": [
            {"id": "1", "description": "T1", "status": "completed", "outcomes": ["O1"]},
            {"id": "2", "description": "T2", "status": "pending"},
        ],
    }
    task = data["tasks"][1]
    prompt = agent.prepare_prompt(data, task, tasks_file)
    assert "My context" in prompt
    assert "T2" in prompt
    assert "T1" in prompt
    assert "O1" in prompt
