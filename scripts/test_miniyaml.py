#!/usr/bin/env python3
"""miniyaml 的离线测试。

手写解析器一定会漏格式。这里把已知的形状全部钉住，
包括曾经踩过的行内数组与嵌套对象数组。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from miniyaml import parse_yaml, scalar, strip_comment


class TestComment(unittest.TestCase):
    def test_trailing_comment(self):
        self.assertEqual(strip_comment("a: 1  # 注释"), "a: 1")

    def test_hash_in_quotes_kept(self):
        self.assertEqual(strip_comment("a: 'x # y'"), "a: 'x # y'")

    def test_full_line_comment(self):
        self.assertEqual(strip_comment("  # 全是注释"), "")


class TestScalar(unittest.TestCase):
    def test_int(self):
        self.assertEqual(scalar("22"), 22)

    def test_float(self):
        self.assertEqual(scalar("1.5"), 1.5)

    def test_bool(self):
        self.assertIs(scalar("true"), True)
        self.assertIs(scalar("no"), False)

    def test_null(self):
        self.assertIsNone(scalar("~"))

    def test_inline_list(self):
        self.assertEqual(scalar("[22, 443, 3000]"), [22, 443, 3000])

    def test_empty_inline_list(self):
        self.assertEqual(scalar("[]"), [])

    def test_quoted(self):
        self.assertEqual(scalar('"hello"'), "hello")

    def test_plain_string(self):
        self.assertEqual(scalar("192.168.1.1"), "192.168.1.1")


class TestParse(unittest.TestCase):
    def test_flat(self):
        self.assertEqual(parse_yaml("a: 1\nb: x\n"), {"a": 1, "b": "x"})

    def test_nested(self):
        got = parse_yaml("top:\n  leaf: 1\n")
        self.assertEqual(got, {"top": {"leaf": 1}})

    def test_object_list(self):
        got = parse_yaml("nodes:\n  - name: a\n    port: 22\n  - name: b\n    port: 80\n")
        self.assertEqual(
            got["nodes"],
            [{"name": "a", "port": 22}, {"name": "b", "port": 80}],
        )

    def test_inline_list_value(self):
        got = parse_yaml("n:\n  ports: [22, 443]\n")
        self.assertEqual(got["n"]["ports"], [22, 443])

    def test_empty(self):
        self.assertEqual(parse_yaml(""), {})

    def test_comment_only(self):
        self.assertEqual(parse_yaml("# 只有注释\n"), {})

    def test_blank_lines_ignored(self):
        self.assertEqual(parse_yaml("a: 1\n\n\nb: 2\n"), {"a": 1, "b": 2})

    def test_missing_file_raises(self):
        from miniyaml import load_yaml_file

        with self.assertRaises(FileNotFoundError):
            load_yaml_file("/nonexistent/x.yaml")


if __name__ == "__main__":
    unittest.main(verbosity=1)
