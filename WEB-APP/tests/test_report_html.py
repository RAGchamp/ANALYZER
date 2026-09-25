from datetime import datetime

import report_html


def test_report_filename():
    name = report_html.report_filename(
        "Bharat-Forge-IR-2026-conv-single-page.pdf", 3, datetime(2026, 9, 24, 18, 5, 9))
    assert name == "Bharat-Forge-IR-2026-conv-single-page-3-20260924-180509.html"


def test_tables_styled_and_numbers_right_aligned():
    html = report_html._style_tables(
        "<table><tbody><tr><td>Tax</td><td>5,777.74</td><td>(95.34)</td>"
        "<td><strong>34.66%</strong></td><td>–</td></tr></tbody></table>")
    assert 'class="table table-sm table-bordered align-middle"' in html
    assert "<td>Tax</td>" in html
    assert html.count('<td class="num">') == 4


def test_unit_after_parenthesis_is_numeric():
    html = report_html._style_tables("<table><tr><td>(2.60) pp</td><td>n.a.</td></tr></table>")
    assert html.count('<td class="num">') == 2


def test_list_directly_after_paragraph_renders_as_list():
    import app
    html = app.render_markdown("**Ties out:**\n- FY26: 1\n- FY25: 2\n\nText\n1. one\n2. two")
    assert html.count("<li>") == 4
    assert "<ul>" in html and "<ol>" in html
