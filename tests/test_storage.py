"""storage 单测：profiles/proxies/cookies/tasks/logs 五表 CRUD + 级联清理。"""

import pytest

from fabletech.config import set_data_dir_override
from fabletech.core.storage import Storage


@pytest.fixture()
def st(tmp_path):
    set_data_dir_override(tmp_path)
    yield Storage(tmp_path / "test.db")
    from fabletech.config import clear_data_dir_override
    clear_data_dir_override()


def test_id_format(st):
    p = st.create_profile("档案A", "camoufox", "seed-abc")
    assert len(p["id"]) == 12
    assert p["status"] == "active"


def test_profile_crud(st):
    p = st.create_profile("档案A", "camoufox", "s1", platform_tags="x,tiktok",
                          group_name="组1", notes="备注")
    assert st.get_profile(p["id"])["name"] == "档案A"
    st.update_profile(p["id"], name="改名", status="banned")
    got = st.get_profile(p["id"])
    assert got["name"] == "改名" and got["status"] == "banned"
    assert len(st.list_profiles()) == 1
    assert len(st.list_profiles(status="banned")) == 1
    assert len(st.list_profiles(status="active")) == 0
    st.delete_profile(p["id"])
    assert st.get_profile(p["id"]) is None


def test_profile_update_rejects_unknown_field(st):
    p = st.create_profile("A", "camoufox", "s")
    with pytest.raises(ValueError):
        st.update_profile(p["id"], hacker="1")


def test_proxy_crud_and_unbind_on_delete(st):
    pr = st.create_proxy("socks5", "1.2.3.4", 1080,
                         username_enc=b"u", password_enc=b"p", tags="美国")
    p = st.create_profile("A", "camoufox", "s", proxy_id=pr["id"])
    assert st.get_proxy(pr["id"])["port"] == 1080
    st.update_proxy(pr["id"], latency_ms=120, country="US", city="LA")
    assert st.get_proxy(pr["id"])["latency_ms"] == 120
    st.delete_proxy(pr["id"])
    assert st.get_proxy(pr["id"]) is None
    # 档案不应被级联删除，而是解绑
    assert st.get_profile(p["id"]) is not None
    assert st.get_profile(p["id"])["proxy_id"] is None


def test_cookies_upsert_and_cascade(st):
    p = st.create_profile("A", "camoufox", "s")
    st.save_cookies(p["id"], "camoufox", b"\x00\x01enc")
    st.save_cookies(p["id"], "camoufox", b"\x00\x02enc2")  # upsert
    assert st.get_cookies(p["id"])["blob"] == b"\x00\x02enc2"
    st.delete_profile(p["id"])
    assert st.get_cookies(p["id"]) is None


def test_task_crud(st):
    p = st.create_profile("A", "camoufox", "s")
    t = st.create_task("outlook_register", p["id"],
                       params={"name": "张三"}, schedule="once")
    assert t["status"] == "pending"
    assert st.get_task(t["id"])["recipe"] == "outlook_register"
    st.update_task(t["id"], status="done")
    assert st.get_task(t["id"])["status"] == "done"
    assert len(st.list_tasks(status="done")) == 1
    st.delete_task(t["id"])
    assert st.get_task(t["id"]) is None


def test_logs_crud_and_prune(st):
    p = st.create_profile("A", "camoufox", "s")
    for i in range(5):
        st.add_log("INFO", "cli", f"消息{i}", profile_id=p["id"])
    logs = st.list_logs(limit=10)
    assert len(logs) == 5
    assert logs[0]["message"] == "消息4"  # 倒序
    assert len(st.list_logs(profile_id=p["id"])) == 5
    assert len(st.list_logs(level="ERROR")) == 0
    assert st.prune_logs(keep=2) == 3
    assert len(st.list_logs()) == 2


def test_settings(st):
    assert st.get_setting("k") is None
    st.set_setting("k", "v")
    assert st.get_setting("k") == "v"
    st.set_setting("k", "v2")
    assert st.get_setting("k") == "v2"


def test_injection_safe_values(st):
    """恶意字符串作为值也应安全（参数化）。"""
    evil = "x'); DROP TABLE profiles;--"
    p = st.create_profile(evil, "camoufox", "s")
    assert st.get_profile(p["id"])["name"] == evil
    assert len(st.list_profiles()) == 1


def test_proxy_extra_enc_roundtrip(st):
    """hy2/anytls 扩展参数加密列：写入 → 读取 → 更新。"""
    pr = st.create_proxy("hysteria2", "h.com", 443,
                         extra_enc=b'{"sni":"r.com","insecure":true}')
    got = st.get_proxy(pr["id"])
    assert got["extra_enc"] == b'{"sni":"r.com","insecure":true}'
    st.update_proxy(pr["id"], extra_enc=b'{"sni":"r2.com"}')
    assert st.get_proxy(pr["id"])["extra_enc"] == b'{"sni":"r2.com"}'


def test_proxy_migration_adds_extra_column(tmp_path):
    """旧库（无 extra_enc 列）打开后自动迁移补列。"""
    import sqlite3
    set_data_dir_override(tmp_path)
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    # 构造 v1.0 旧 schema（无 extra_enc）
    conn.executescript(
        "CREATE TABLE proxies(id TEXT PRIMARY KEY, type TEXT NOT NULL,"
        " host TEXT NOT NULL, port INTEGER NOT NULL, username_enc BLOB,"
        " password_enc BLOB, tags TEXT DEFAULT '', last_check_at TEXT,"
        " latency_ms INTEGER, country TEXT, city TEXT,"
        " enabled INTEGER DEFAULT 1);"
        "INSERT INTO proxies(id,type,host,port) VALUES('old1','socks5','1.2.3.4',1080);")
    conn.commit()
    conn.close()
    # 用新 Storage 打开 → 触发迁移
    st = Storage(db)
    cols = {r[1] for r in
            sqlite3.connect(db).execute("PRAGMA table_info(proxies)").fetchall()}
    assert "extra_enc" in cols
    # 旧数据完好，新列可写
    assert st.get_proxy("old1")["host"] == "1.2.3.4"
    pr = st.create_proxy("anytls", "a.com", 8443, extra_enc=b'{"sni":"x"}')
    assert st.get_proxy(pr["id"])["extra_enc"] == b'{"sni":"x"}'
    from fabletech.config import clear_data_dir_override
    clear_data_dir_override()
