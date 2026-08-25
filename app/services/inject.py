# app/services/inject.py
"""
向 DSH webui 的 HTML 注入「租户配额进度条」脚本
"""

QUOTA_JS = r"""
<script id="nekoseek-quota">
(function () {
  function bar(label, used, limit, remaining) {
    var pct = 0;
    if (limit > 0) { pct = Math.min(100, Math.round(used / limit * 100)); }
    var r = (remaining === null || remaining === undefined) ? '不限' : String(remaining);
    return (
      '<div style="font:12px/1.6 sans-serif;color:#cbd5e1;">' +
        '<span>' + label + '</span> ' +
        '<span style="color:#94a3b8;">' + used + ' / ' + (limit > 0 ? limit : '∞') + '</span>' +
      '</div>' +
      '<div style="height:6px;background:#1e293b;border-radius:3px;overflow:hidden;margin:2px 0 8px;">' +
        '<div style="height:100%;width:' + pct + '%;background:' + (pct >= 90 ? '#ef4444' : '#38bdf8') + ';"></div>' +
      '</div>'
    );
  }

  function render() {
    fetch('/api/v1/quota', {credentials: 'same-origin'})
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
      .then(function (d) {
        var el = document.getElementById('nekoseek-quota-box');
        if (!el) {
          el = document.createElement('div');
          el.id = 'nekoseek-quota-box';
          el.style.cssText = 'position:fixed;bottom:12px;right:12px;z-index:99999;' +
            'background:rgba(15,23,42,.92);padding:10px 14px;border-radius:10px;width:240px;' +
            'box-shadow:0 4px 16px rgba(0,0,0,.4);';
          document.body.appendChild(el);
        }
        var p = d.pool || {};
        var u = d.user || {};
        el.innerHTML =
          bar('全局池', p.used, p.limit, p.remaining) +
          bar('个人配额', u.used, u.limit, u.remaining);
      })
      .catch(function () { /* 未登录或接口不可用，静默 */ });
  }

  // 轮询
  setInterval(render, 15000);
  document.addEventListener('DOMContentLoaded', render);
  render();
})();
</script>
"""


def inject_quota_js(html: str) -> str:
    """
    在 HTML 的 </head> 前注入配额脚本；若无 </head> 则追加到文末。
    """
    if QUOTA_JS.strip() in html:
        return html
    if "</head>" in html.lower():
        idx = html.lower().rfind("</head>")
        return html[:idx] + QUOTA_JS + html[idx:]
    return html + QUOTA_JS
