import unittest
from lemming.main import build_agent_command


class TestAgentCommand(unittest.TestCase):
    def test_gemini_full_path(self):
        cmd = build_agent_command("/usr/bin/gemini", "hello", yolo=True)
        self.assertEqual(cmd[0], "/usr/bin/gemini")
        self.assertIn("--yolo", cmd)
        self.assertNotIn("--quiet", cmd)
        self.assertIn("hello", cmd)

    def test_aider_full_path(self):
        cmd = build_agent_command("/opt/aider", "hello", yolo=True)
        self.assertEqual(cmd[0], "/opt/aider")
        self.assertIn("--yes", cmd)
        self.assertIn("--quiet", cmd)
        self.assertIn("--message", cmd)
        self.assertIn("hello", cmd)

    def test_claude_yolo(self):
        cmd = build_agent_command("claude", "hello", yolo=True)
        self.assertEqual(cmd[0], "claude")
        self.assertIn("--dangerously-skip-permissions", cmd)
        self.assertIn("--print", cmd)
        self.assertIn("hello", cmd)

    def test_codex_yolo(self):
        cmd = build_agent_command("codex", "hello", yolo=True)
        self.assertEqual(cmd[0], "codex")
        self.assertIn("--yolo", cmd)
        self.assertIn("--instructions", cmd)
        self.assertIn("hello", cmd)

    def test_fuzzy_gemini_match(self):
        cmd = build_agent_command("gemini-v2", "hello", yolo=True)
        self.assertEqual(cmd[0], "gemini-v2")
        self.assertIn("--yolo", cmd)
        self.assertIn("--no-sandbox", cmd)
        self.assertNotIn("--quiet", cmd)
        self.assertIn("--prompt", cmd)
        self.assertEqual(cmd[-1], "hello")

    def test_no_defaults_flag(self):
        cmd = build_agent_command("gemini", "hello", yolo=True, no_defaults=True)
        self.assertEqual(cmd, ["gemini", "hello"])

    def test_custom_prompt_flag(self):
        cmd = build_agent_command(
            "custom-agent", "hello", yolo=True, prompt_flag="--input"
        )
        self.assertEqual(cmd, ["custom-agent", "--input", "hello"])

    def test_custom_prompt_flag_no_dash(self):
        cmd = build_agent_command(
            "custom-agent", "hello", yolo=True, prompt_flag="input"
        )
        self.assertEqual(cmd, ["custom-agent", "--input", "hello"])

    def test_agent_args(self):
        cmd = build_agent_command(
            "gemini", "hello", yolo=True, agent_args=("--model", "flash")
        )
        self.assertEqual(cmd[0], "gemini")
        self.assertIn("--model", cmd)
        self.assertIn("flash", cmd)
        self.assertIn("hello", cmd)
        self.assertEqual(cmd[-1], "hello")


if __name__ == "__main__":
    unittest.main()
