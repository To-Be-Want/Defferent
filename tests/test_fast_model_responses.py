"""Regression checks for reasoning tokens consuming the answer budget."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import study_fast_channel as fast


class FastModelResponseTests(unittest.TestCase):
    config = {"api_key": "test", "base_url": "https://example.invalid/v1",
              "model": "deepseek-v4-flash"}

    def response(self, content, finish="stop"):
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": content},
                                                   "finish_reason": finish}]}
        return response

    def test_router_reserves_budget_for_classification(self):
        with patch.object(fast.requests, "post", return_value=self.response(
                '{"engine":"fast","confidence":0.95}')) as post:
            self.assertEqual(fast._classify_engine_by_llm("Solve this coding problem", self.config), "fast")
            self.assertEqual(post.call_args.kwargs["json"]["thinking"], {"type": "disabled"})

    def test_other_providers_do_not_receive_deepseek_options(self):
        with patch.object(fast.requests, "post", return_value=self.response(
                '{"engine":"fast","confidence":0.95}')) as post:
            fast._classify_engine_by_llm("Question", dict(self.config, model="other-model"))
            self.assertNotIn("thinking", post.call_args.kwargs["json"])

    def test_coding_answer_and_empty_or_truncated_responses(self):
        for content, finish, error in [("Use a linear scan.", "stop", None),
                                       ("", "stop", "没有返回答案"),
                                       ("partial code", "length", "长度上限")]:
            with self.subTest(finish=finish, content=content), patch.object(
                    fast, "get_preferences", return_value={"allow_cloud_model": True}), patch.object(
                    fast.requests, "post", return_value=self.response(content, finish)) as post:
                messages = [{"role": "user", "content": "Find longest increasing contiguous subarray"}]
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        fast._chat_with_tools(messages, self.config)
                else:
                    self.assertEqual(fast._chat_with_tools(messages, self.config), content)
                self.assertEqual(post.call_args.kwargs["json"]["thinking"], {"type": "disabled"})


if __name__ == "__main__":
    unittest.main()
