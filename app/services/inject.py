# app/services/inject.py
"""
向 DSH webui 的 HTML 注入「用户信息面板」外联脚本与样式标签，
以及 crypto.randomUUID 的就地 polyfill。

使用外联 /static 资源而非内联脚本：上游若下发 CSP（script-src 'self'），
同源外联脚本可执行，内联脚本会被拦截；同时面板逻辑有独立文件可维护。

polyfill 例外：它必须先于 DSH 前端任何 defer/module 脚本执行，
必须内联、且越靠前越好（DSH 在 <head> 起始就内联跑了 module loader）。
非安全上下文（http://IP 访问）下 window.crypto.randomUUID 不存在，
DSH 前端一调即抛 "crypto.randomUUID is not a function"，此处就地补齐。
"""

# 就地补齐 crypto.randomUUID / crypto.getRandomValues。
# - randomUUID：非安全上下文下整个 crypto 对象仍在，但缺此方法，直接挂上。
# - 优先用 getRandomValues 取真随机源；它也不可用时退回 Math.random（仅够生成
#   不重复 ID，安全性低于加密随机，但对会话/请求 ID 足够）。
# 用 try/defineProperty 包住，避免 crypto 只读或字段不可写时抛错。
_POLYFILL_SNIPPET = """<script data-nekoseek-polyfill>
(function () {
  try {
    var c = window.crypto = window.crypto || {};
    if (!c.getRandomValues) {
      c.getRandomValues = function (arr) {
        for (var i = 0; i < arr.length; i++) {
          arr[i] = Math.floor(Math.random() * 256);
        }
        return arr;
      };
    }
    if (typeof c.randomUUID !== "function") {
      var hex = [];
      for (var i = 0; i < 256; i++) hex[i] = (i + 0x100).toString(16).slice(1);
      c.randomUUID = function () {
        var b = c.getRandomValues(new Uint8Array(16));
        b[6] = (b[6] & 0x0f) | 0x40; // version 4
        b[8] = (b[8] & 0x3f) | 0x80; // variant 10xx
        return (
          hex[b[0]] + hex[b[1]] + hex[b[2]] + hex[b[3]] + "-" +
          hex[b[4]] + hex[b[5]] + "-" +
          hex[b[6]] + hex[b[7]] + "-" +
          hex[b[8]] + hex[b[9]] + "-" +
          hex[b[10]] + hex[b[11]] + hex[b[12]] + hex[b[13]] + hex[b[14]] + hex[b[15]]
        );
      };
    }
  } catch (e) { /* 忽略：字段不可写时放弃 polyfill */ }
})();
</script>
<!-- /nekoseek-polyfill -->
"""

PANEL_TAGS = (
    "<!-- nekoseek-panel -->\n"
    '<link rel="stylesheet" href="/static/css/ebui-panel.css">\n'
    '<script src="/static/js/ebui-panel.js" defer></script>\n'
)


def inject_panel_tags(html: str) -> str:
    """
    注入 polyfill 与面板标签。

    - polyfill 内联在 <head> 起始处，先于 DSH 的 module loader / __DSH_BOOT__ /
      defer/module 脚本执行。
    - 面板标签放在 </head> 前（外联 defer，顺序不敏感）。
    已注入过则原样返回（幂等，以面板标记为准）。
    """
    if "nekoseek-panel" in html:
        return html
    lower = html.lower()
    # 1) polyfill 紧随 <head> 之后（含 <head ...> 带属性的情况）
    head_open = lower.find("<head")
    if head_open != -1:
        gt = lower.find(">", head_open)
        if gt != -1:
            html = html[: gt + 1] + _POLYFILL_SNIPPET + html[gt + 1 :]
            lower = html.lower()
    # 2) 面板标签在 </head> 前；若无 </head> 则追加到文末
    idx = lower.rfind("</head>")
    if idx != -1:
        return html[:idx] + PANEL_TAGS + html[idx:]
    return html + PANEL_TAGS
