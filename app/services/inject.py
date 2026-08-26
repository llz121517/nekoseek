# app/services/inject.py
"""
向 DSH webui 的 HTML 注入「用户信息面板」外联脚本与样式标签。

使用外联 /static 资源而非内联脚本：上游若下发 CSP（script-src 'self'），
同源外联脚本可执行，内联脚本会被拦截；同时面板逻辑有独立文件可维护。
"""

PANEL_TAGS = (
    "<!-- nekoseek-panel -->\n"
    '<link rel="stylesheet" href="/static/css/ebui-panel.css">\n'
    '<script src="/static/js/ebui-panel.js" defer></script>\n'
)


def inject_panel_tags(html: str) -> str:
    """
    在 HTML 的 </head> 前注入面板标签；若无 </head> 则追加到文末。
    已注入过则原样返回（幂等）。
    """
    if "nekoseek-panel" in html:
        return html
    idx = html.lower().rfind("</head>")
    if idx != -1:
        return html[:idx] + PANEL_TAGS + html[idx:]
    return html + PANEL_TAGS
