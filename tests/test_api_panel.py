"""面板接口与网关页面路由的集成测试。"""


class TestPanelMe:
    def test_requires_auth(self, auth_client):
        r = auth_client.get("/api/v1/panel/me")
        assert r.status_code == 401

    def test_returns_panel_data(self, auth_client, make_user):
        from app.core import quota
        user = make_user(username="panel-user", quota_override=100)
        quota.record_usage(user["id"], input_tokens=30, output_tokens=10)
        cookie = auth_client.login_cookie(user["id"])
        r = auth_client.get("/api/v1/panel/me", cookies=cookie)
        data = r.json()["data"]
        assert data["username"] == "panel-user"
        assert data["window"] == "day"
        assert data["user"]["used"] == 40
        assert data["user"]["limit"] == 100
        assert data["user"]["remaining"] == 60
        # 面板不暴露 window_start 与 input/output 明细
        assert "window_start" not in data
        assert "input" not in data["user"]

    def test_unlimited_remaining_is_null(self, auth_client, make_user):
        user = make_user()
        cookie = auth_client.login_cookie(user["id"])
        r = auth_client.get("/api/v1/panel/me", cookies=cookie)
        assert r.json()["data"]["user"]["remaining"] is None


class TestPageRoutes:
    def test_login_page_public(self, auth_client):
        r = auth_client.get("/login")
        assert r.status_code == 200

    def test_admin_page_requires_admin(self, auth_client, make_user):
        cookie = auth_client.login_cookie(make_user()["id"])
        r = auth_client.get("/admin", cookies=cookie)
        assert r.status_code == 403

    def test_admin_page_ok_for_admin(self, auth_client, admin_user):
        cookie = auth_client.login_cookie(admin_user["id"])
        r = auth_client.get("/admin", cookies=cookie)
        assert r.status_code == 200
        assert "no-store" in r.headers["cache-control"]

    def test_root_redirects_to_login_when_anonymous(self, auth_client):
        r = auth_client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"
