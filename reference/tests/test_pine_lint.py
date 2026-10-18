import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

import pine_lint  # noqa: E402

HEAD = '//@version=6\nindicator("x", max_lines_count = 500, max_labels_count = 500, max_boxes_count = 500)\n'


def lint_text(tmp_path, body):
    p = tmp_path / "t.pine"
    p.write_text(HEAD + body, encoding="utf-8")
    return pine_lint.lint(str(p))


def test_project_files_clean():
    for f in ("tw_speedometer_pro.pine", "tw_blind_zone_pro.pine"):
        assert pine_lint.lint(os.path.join(ROOT, "pine", f)) == []


def test_ta_in_if(tmp_path):
    assert lint_text(tmp_path, "if close > open\n    x = ta.ema(close, 5)\n")


def test_ta_after_ternary_and_or(tmp_path):
    assert lint_text(tmp_path, "x = close > open ? ta.ema(close, 5) : 0.0\n")
    assert lint_text(tmp_path, "b = close > open and ta.crossover(close, open)\n")


def test_ta_function_called_conditionally(tmp_path):
    body = "f(x) =>\n    ta.ema(x, 5)\nif close > open\n    y = f(close)\n"
    assert lint_text(tmp_path, body)


def test_ta_in_function_top_level_ok(tmp_path):
    assert lint_text(tmp_path, "f(x) =>\n    ta.ema(x, 5)\ny = f(close)\n") == []


def test_input_time_timestamp(tmp_path):
    assert lint_text(tmp_path, 'int t = input.time(timestamp("2026-09-15"), "t")\n')
    assert lint_text(tmp_path, 'int t = input.time(1789430400000, "t")\n') == []


def test_security_rules(tmp_path):
    assert lint_text(tmp_path, 'x = request.security(syminfo.tickerid, "D", close)\n')
    body = "".join(f'x{i} = request.security(syminfo.tickerid, "D", close, lookahead = barmerge.lookahead_off)\n' for i in range(4))
    assert lint_text(tmp_path, body)


def test_table_rules(tmp_path):
    assert lint_text(tmp_path, "t = table.new(position.top_right, 1, 1)\n")
    assert lint_text(tmp_path, 'var table t = table.new(position.top_right, 1, 1)\ntable.cell(t, 0, 0, "a")\n')
    ok = 'var table t = table.new(position.top_right, 1, 1)\nif barstate.islast\n    table.cell(t, 0, 0, "a")\n'
    assert lint_text(tmp_path, ok) == []
