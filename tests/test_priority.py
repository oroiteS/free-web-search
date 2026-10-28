"""Offline unit tests for searchPriority / fetchPriority: python3 -m unittest discover -s tests.

直接导入 scripts/search.py 测纯函数（配置解析、路由排序），不发起网络请求。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import search as s  # noqa: E402


class ParsePriorities(unittest.TestCase):
    def test_numbered_mapping(self):
        vals = {"searchPriority.deepseek": 1, "searchPriority.bing": "2", "searchPriority.exa": 10}
        pr, warns = s._extract_priorities(vals, "searchPriority", set(s.ALL_ENGINES))
        self.assertEqual(pr, {"deepseek": 1.0, "bing": 2.0, "exa": 10.0})
        self.assertEqual(warns, [])

    def test_unknown_engine_and_non_number_warn(self):
        vals = {"searchPriority.nothing": 1, "searchPriority.bing": "fast"}
        pr, warns = s._extract_priorities(vals, "searchPriority", set(s.ALL_ENGINES))
        self.assertEqual(pr, {})
        self.assertEqual(len(warns), 2)

    def test_list_and_comma_forms(self):
        for raw in (["deepseek", "bing"], "deepseek,bing", " deepseek , bing "):
            pr, warns = s._extract_priorities({"searchPriority": raw}, "searchPriority", set(s.ALL_ENGINES))
            self.assertEqual(pr, {"deepseek": 1.0, "bing": 2.0}, raw)
            self.assertEqual(warns, [])

    def test_mapping_wins_list_fills(self):
        vals = {"searchPriority.bing": 5, "searchPriority": ["deepseek", "bing", "exa"]}
        pr, _ = s._extract_priorities(vals, "searchPriority", set(s.ALL_ENGINES))
        self.assertEqual(pr, {"bing": 5.0, "deepseek": 1.0, "exa": 3.0})


class PrioritizedHelper(unittest.TestCase):
    def test_reorder_and_ties(self):
        self.assertEqual(s._prioritized(["a", "b", "c"], {"c": 1, "a": 2}), ["c", "a", "b"])
        self.assertEqual(s._prioritized(["a", "b"], None), ["a", "b"])
        self.assertEqual(s._prioritized(["x", "y"], {"y": 1, "x": 1}), ["x", "y"])  # 同数保持原序


class RouteOrdering(unittest.TestCase):
    def test_priorities_override_language_head(self):
        order = s.route_engines("中文查询", None, s.ALL_ENGINES, {"anysearch": 1, "exa": 2})
        self.assertEqual(order[:2], ["anysearch", "exa"])

    def test_unlisted_follow_default_order(self):
        pr = {"keenable": 1}
        order = s.route_engines("test", None, s.ALL_ENGINES, pr)
        self.assertEqual(order[0], "keenable")
        rest = order[1:]
        default_rest = [e for e in s.DEFAULT_FALLBACK_ORDER
                        if e in s.usable_engines(s.ALL_ENGINES) and e != "keenable"]
        self.assertEqual(rest, default_rest)

    def test_time_engines_first_with_priorities(self):
        pr = {"bing": 1, "tavily": 2, "keenable": 3}
        order = s.route_engines("test", {"days": 7}, s.ALL_ENGINES, pr)
        self.assertEqual(order[:3], ["tavily", "keenable", "bing"])

    def test_no_priorities_keeps_language_routing(self):
        self.assertEqual(s.route_engines("中文", None, s.ALL_ENGINES, None)[0], "bing")
        self.assertEqual(s.route_engines("english", None, s.ALL_ENGINES, None)[0], "bing")


class ConfigToSettings(unittest.TestCase):
    def test_priority_settings_and_warnings(self):
        vals = {
            "searchPriority.deepseek": 1,
            "searchPriority.nothing": 2,
            "fetchPriority.firecrawl": 1,
            "fetchPriority.deepseek": 2,
        }
        st = s.config_to_settings(vals)
        self.assertEqual(st["searchPriority"], {"deepseek": 1.0})
        self.assertEqual(st["fetchPriority"], {"firecrawl": 1.0, "deepseek": 2.0})
        self.assertEqual(len(st["priorityWarnings"]), 1)

    def test_empty_when_absent(self):
        st = s.config_to_settings({"lang": "zh"})
        self.assertNotIn("searchPriority", st)
        self.assertNotIn("fetchPriority", st)
        self.assertNotIn("priorityWarnings", st)


if __name__ == "__main__":
    unittest.main()
