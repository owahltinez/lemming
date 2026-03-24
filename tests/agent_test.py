from lemming import agent
from lemming import tasks


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


def test_build_agent_command_with_flags_in_name():
    cmd = agent.build_agent_command(
        "claude-corp -- --output-format=stream-json", "my prompt", yolo=True
    )
    assert cmd[0] == "claude-corp"
    assert "--" in cmd
    assert "--output-format=stream-json" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--print" in cmd
    assert "my prompt" in cmd


def test_build_agent_command_with_quoted_flags_in_name():
    cmd = agent.build_agent_command(
        'my-agent --model "gpt 4"', "my prompt", yolo=True, no_defaults=True
    )
    assert cmd[0] == "my-agent"
    assert "--model" in cmd
    assert "gpt 4" in cmd


def test_prepare_prompt(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = tasks.Roadmap(
        context="My context",
        tasks=[
            tasks.Task(id="1", description="T1", status="completed", outcomes=["O1"]),
            tasks.Task(id="2", description="T2", status="pending"),
        ],
    )
    task = data.tasks[1]
    prompt = agent.prepare_prompt(data, task, tasks_file)
    assert "My context" in prompt
    assert "T2" in prompt
    assert "T1" in prompt
    assert "O1" in prompt


def test_pretty_quote():
    # Test fallback to shlex
    assert agent._pretty_quote("simple") == "simple"
    assert agent._pretty_quote("has space") == "'has space'"
    
    # Test readable double quotes for single quotes
    assert agent._pretty_quote("has 'single' quotes") == '"has \'single\' quotes"'
    assert agent._pretty_quote("You are 'Lemming'") == '"You are \'Lemming\'"'
    
    # Test string with double quotes (should fall back to single quotes)
    assert agent._pretty_quote('has "double" quotes') == "'has \"double\" quotes'"
    
    # Test escaping specials inside double quotes
    assert agent._pretty_quote("has 'single' and \"double\" quotes") == '"has \'single\' and \\"double\\" quotes"'
    
    # Test exclamation mark fallback
    assert agent._pretty_quote("Hello!") == "'Hello!'"
    import shlex
    assert agent._pretty_quote("has 'single' and !") == shlex.quote("has 'single' and !")


def test_shlex_join_pretty():
    cmd = ["example-cli", "--dangerously-skip-permissions", "--print", "You are 'Lemming'"]
    joined = agent._shlex_join_pretty(cmd)
    assert joined == 'example-cli --dangerously-skip-permissions --print "You are \'Lemming\'"'
