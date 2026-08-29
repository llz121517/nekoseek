"""归属映射（attribution）的单元测试。"""
import time

import pytest

from app.core import attribution


@pytest.fixture(autouse=True)
def _clean_owners():
    """每个用例前后清空进程内映射，避免用例间串扰。"""
    attribution._owners.clear()
    yield
    attribution._owners.clear()


class TestRecordAndResolve:
    def test_resolve_returns_recorded_user(self):
        attribution.record_prompt_session("s1", 42)
        assert attribution.resolve_owner("s1") == 42

    def test_unknown_session_returns_none(self):
        assert attribution.resolve_owner("nope") is None

    def test_re_record_overwrites_owner(self):
        attribution.record_prompt_session("s1", 1)
        attribution.record_prompt_session("s1", 2)
        assert attribution.resolve_owner("s1") == 2

    def test_transfer_disabled_keeps_first_owner(self, monkeypatch):
        # TRANSFER_ON_PROMPT=False：先入为主，插话不转移归属
        monkeypatch.setattr(attribution, "TRANSFER_ON_PROMPT", False)
        attribution.record_prompt_session("s1", 1)
        attribution.record_prompt_session("s1", 2)
        assert attribution.resolve_owner("s1") == 1  # 仍是首建者

    def test_transfer_disabled_allows_expired_rebind(self, monkeypatch):
        # 过期条目视为无归属，即使先入为主也可重建
        monkeypatch.setattr(attribution, "TRANSFER_ON_PROMPT", False)
        attribution.record_prompt_session("s1", 1)
        future = time.time() + attribution._OWNER_TTL_SEC + 1
        monkeypatch.setattr(time, "time", lambda: future)
        attribution.record_prompt_session("s1", 2)
        assert attribution.resolve_owner("s1") == 2

    def test_empty_args_ignored(self):
        attribution.record_prompt_session("", 1)
        attribution.record_prompt_session("s1", 0)
        assert attribution.resolve_owner("") is None
        assert attribution.resolve_owner("s1") is None


class TestExpiry:
    def test_expired_entry_returns_none_and_is_removed(self, monkeypatch):
        attribution.record_prompt_session("s1", 7)
        # 快进到 TTL 之后
        future = time.time() + attribution._OWNER_TTL_SEC + 1
        monkeypatch.setattr(time, "time", lambda: future)
        assert attribution.resolve_owner("s1") is None
        assert "s1" not in attribution._owners
