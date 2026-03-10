import unittest
from lemming.main import build_agent_command

class TestAgentCommand(unittest.TestCase):
    def test_gemini_full_path(self):
        cmd = build_agent_command("/usr/bin/gemini", "hello", yolo=True)
        self.assertEqual(cmd[0], "/usr/bin/gemini")
        self.assertIn("--yolo", cmd)
        self.assertIn("hello", cmd)

    def test_aider_full_path(self):
        cmd = build_agent_command("/opt/aider", "hello", yolo=True)
        self.assertEqual(cmd[0], "/opt/aider")
        self.assertIn("--yes", cmd)
        self.assertIn("--message", cmd)
        self.assertIn("hello", cmd)

    def test_fuzzy_gemini_match(self):
        cmd = build_agent_command("gemini-v2", "hello", yolo=True)
        self.assertEqual(cmd[0], "gemini-v2")
        self.assertIn("--yolo", cmd)
        self.assertIn("--no-sandbox", cmd)
        self.assertIn("--prompt", cmd)
        self.assertEqual(cmd[-1], "hello")

    def test_no_defaults_flag(self):
        cmd = build_agent_command("gemini", "hello", yolo=True, no_defaults=True)
        self.assertEqual(cmd, ["gemini", "hello"])

if __name__ == "__main__":
    unittest.main()
