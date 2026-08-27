"""配额窗口、限额与记账的单元测试。"""
import time
from datetime import datetime

import pytest

from app.core import quota
from app.core.db import db_op


class TestWindowStart:
    def test_day_is_local_midnight(self):
        now = time.time()
        ws = quota.current_window_start("day", now)
        dt = datetime.fromtimestamp(ws)
        assert (dt.hour, dt.minute, dt.second) == (0, 0, 0)

    def test_week_is_monday(self):
        ws = quota.current_window_start("week")
        assert datetime.fromtimestamp(ws).weekday() == 0

    def test_month_is_first(self):
        ws = quota.current_window_start("month")
        assert datetime.fromtimestamp(ws).day == 1

    def test_five_hours_aligned(self):
        now = time.time()
        ws = quota.current_window_start("5h", now)
        assert ws % (5 * 3600) == 0


class TestWindowKind:
    def test_default_is_day(self, isolated_db):
        assert quota.get_window_kind() == "day"

    def test_set_window_kind_persists(self, isolated_db):
        quota.set_window_kind("week")
        assert quota.get_window_kind() == "week"

    def test_invalid_window_raises(self, isolated_db):
        with pytest.raises(ValueError):
            quota.set_window_kind("year")

    def test_invalid_stored_falls_back_to_day(self, isolated_db):
        db_op.set_setting("quota_window", "bogus")
        assert quota.get_window_kind() == "day"


class TestLimits:
    def test_global_limit_default_zero(self, isolated_db):
        assert quota.get_global_limit() == 0

    def test_set_global_limit(self, isolated_db):
        quota.set_global_limit(1000)
        assert quota.get_global_limit() == 1000

    def test_set_global_limit_clamps_negative(self, isolated_db):
        quota.set_global_limit(-5)
        assert quota.get_global_limit() == 0

    def test_user_limit_group_fallback(self, make_user, normal_group):
        user = make_user()
        db_op.update_group(normal_group["id"], quota_limit=777)
        assert quota.get_user_limit(db_op.get_user_by_id(user["id"])) == 777

    def test_user_limit_override_wins(self, make_user, normal_group):
        db_op.update_group(normal_group["id"], quota_limit=777)
        user = make_user(quota_override=42)
        assert quota.get_user_limit(user) == 42


class TestRecordUsage:
    def test_zero_tokens_noop(self, make_user):
        user = make_user()
        quota.record_usage(user["id"], 0, 0)
        ws = quota.current_window_start("day")
        assert db_op.get_usage(user["id"], ws)["total_tokens"] == 0

    def test_records_user_and_global_pool(self, make_user):
        user = make_user()
        quota.record_usage(user["id"], input_tokens=10, output_tokens=5)
        ws = quota.current_window_start("day")
        user_row = db_op.get_usage(user["id"], ws)
        pool_row = db_op.get_usage(0, ws)
        assert user_row["total_tokens"] == 15
        assert pool_row["total_tokens"] == 15
        assert user_row["input_tokens"] == 10
        assert user_row["output_tokens"] == 5

    def test_accumulates(self, make_user):
        user = make_user()
        quota.record_usage(user["id"], input_tokens=10)
        quota.record_usage(user["id"], output_tokens=3)
        ws = quota.current_window_start("day")
        assert db_op.get_usage(user["id"], ws)["total_tokens"] == 13


class TestQuotaChecks:
    def test_unlimited_always_true(self, make_user):
        user = make_user()  # 组与覆写均无限额
        assert quota.check_user_quota(user) is True

    def test_user_quota_exceeded(self, make_user):
        user = make_user(quota_override=10)
        quota.record_usage(user["id"], input_tokens=10)
        assert quota.check_user_quota(db_op.get_user_by_id(user["id"])) is False

    def test_user_quota_remaining(self, make_user):
        user = make_user(quota_override=10)
        quota.record_usage(user["id"], input_tokens=3)
        assert quota.check_user_quota(db_op.get_user_by_id(user["id"])) is True

    def test_global_quota_exceeded(self, isolated_db, make_user):
        quota.set_global_limit(5)
        user = make_user()
        quota.record_usage(user["id"], input_tokens=5)
        assert quota.check_global_quota() is False


class TestReset:
    def test_reset_clears_current_window(self, make_user):
        user = make_user()
        quota.record_usage(user["id"], input_tokens=9)
        n = quota.reset_current_window_usage()
        assert n >= 2  # 用户行 + 全局行
        assert quota.get_user_usage(user["id"])["total_tokens"] == 0
        assert quota.get_global_usage()["total_tokens"] == 0


class TestQuotaSummary:
    def test_summary_shape(self, make_user):
        user = make_user(quota_override=100)
        quota.record_usage(user["id"], input_tokens=30, output_tokens=10)
        summary = quota.quota_summary(db_op.get_user_by_id(user["id"]))
        assert summary["window"] == "day"
        assert summary["user"]["used"] == 40
        assert summary["user"]["limit"] == 100
        assert summary["user"]["remaining"] == 60
        assert summary["pool"]["used"] == 40

    def test_unlimited_remaining_is_none(self, make_user):
        user = make_user()
        summary = quota.quota_summary(user)
        assert summary["user"]["limit"] == 0
        assert summary["user"]["remaining"] is None
