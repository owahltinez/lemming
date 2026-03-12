import unittest
from lemming.core import load_prompt


class TestPrompts(unittest.TestCase):
    def test_load_taskrunner_prompt(self):
        prompt = load_prompt("taskrunner")
        self.assertIn("You are an autonomous AI coding agent", prompt)
        self.assertIn("{{roadmap}}", prompt)
        self.assertIn("{{description}}", prompt)

    def test_prompt_replacement_logic(self):
        template = "Hello {{name}}, welcome to {{place}}!"
        prompt = template.replace("{{name}}", "World").replace("{{place}}", "Lemming")
        self.assertEqual(prompt, "Hello World, welcome to Lemming!")


if __name__ == "__main__":
    unittest.main()
