"""面板注入（inject_panel_tags）的单元测试。"""
from app.services.inject import inject_panel_tags, PANEL_TAGS


class TestInjectPanelTags:
    def test_injects_before_head_close(self):
        html = "<html><head><title>x</title></head><body></body></html>"
        out = inject_panel_tags(html)
        assert "nekoseek-panel" in out
        # 注入点在原 </head> 之前
        assert out.index("nekoseek-panel") < out.index("</head>")

    def test_appends_when_no_head(self):
        html = "<html><body>no head</body></html>"
        out = inject_panel_tags(html)
        assert out.endswith(PANEL_TAGS)

    def test_idempotent(self):
        html = "<html><head></head><body></body></html>"
        once = inject_panel_tags(html)
        twice = inject_panel_tags(once)
        assert once == twice
        assert twice.count("nekoseek-panel") == 1

    def test_includes_static_assets(self):
        out = inject_panel_tags("<html><head></head></html>")
        assert "/static/css/ebui-panel.css" in out
        assert "/static/js/ebui-panel.js" in out
