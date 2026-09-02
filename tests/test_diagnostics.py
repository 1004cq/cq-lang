import unittest

import cq


class DiagnosticTests(unittest.TestCase):
    def test_parser_error_has_source_location_and_plain_language(self):
        with self.assertRaises(cq.CQDiagnostic) as raised:
            cq.parse_source("let answer Int = 42\n", "syntax.cq")

        error = raised.exception
        self.assertEqual(error.kind, "语法错误")
        self.assertEqual((error.location.line, error.location.column), (1, 12))
        self.assertIn("syntax.cq:1:12: 语法错误", str(error))
        self.assertIn("这里需要 `=`", str(error))
        self.assertIn("1 | let answer Int = 42", str(error))
        self.assertIn("|            ^", str(error))

    def test_lexer_error_uses_same_format(self):
        with self.assertRaises(cq.CQDiagnostic) as raised:
            cq.parse_source("let answer = @\n", "lexer.cq")

        error = raised.exception
        self.assertEqual((error.location.line, error.location.column), (1, 14))
        self.assertIn("lexer.cq:1:14: 语法错误：无法识别字符 '@'", str(error))

    def test_invalid_number_is_a_syntax_error_not_a_python_traceback(self):
        with self.assertRaises(cq.CQDiagnostic) as raised:
            cq.parse_source("let answer = 1.2.3\n", "number.cq")

        self.assertIn("'1.2.3' 不是有效数字", str(raised.exception))

    def test_type_error_points_to_the_offending_expression(self):
        tree = cq.parse_source('let answer: Int = "forty-two"\n', "type.cq")

        errors = cq.typecheck(tree)

        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertEqual(error.kind, "类型错误")
        self.assertEqual((error.location.line, error.location.column), (1, 19))
        self.assertIn("type.cq:1:19: 类型错误", str(error))
        self.assertIn("变量 answer 声明为 Int，但右侧表达式是 Str", str(error))
        self.assertIn('| let answer: Int = "forty-two"', str(error))

    def test_caret_aligns_after_chinese_identifier(self):
        tree = cq.parse_source('let 名字: Int = "CQ"\n', "中文.cq")

        error = cq.typecheck(tree)[0]
        caret_line = str(error).splitlines()[-1]

        self.assertEqual((error.location.line, error.location.column), (1, 15))
        self.assertEqual(caret_line.index("^"), 20)

    def test_runtime_argument_type_error_uses_argument_location(self):
        source = 'fn id(value: Int) -> Int { value }\nprint(id("bad"))\n'
        tree = cq.parse_source(source, "call.cq")
        env = cq.Env()
        cq.builtins(env)

        with self.assertRaises(cq.CQDiagnostic) as raised:
            cq.eval_node(tree, env)

        error = raised.exception
        self.assertEqual((error.location.line, error.location.column), (2, 10))
        self.assertIn("call.cq:2:10: 类型错误：参数 value 需要 Int，但得到 Str", str(error))

    def test_runtime_return_type_error_points_to_return_expression(self):
        source = 'fn wrong() -> Int { "bad" }\nwrong()\n'
        tree = cq.parse_source(source, "return.cq")
        env = cq.Env()
        cq.builtins(env)

        with self.assertRaises(cq.CQDiagnostic) as raised:
            cq.eval_node(tree, env)

        error = raised.exception
        self.assertEqual((error.location.line, error.location.column), (1, 21))
        self.assertIn("函数返回值 需要 Int，但得到 Str", str(error))


if __name__ == "__main__":
    unittest.main()
