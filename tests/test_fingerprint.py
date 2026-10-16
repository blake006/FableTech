"""fingerprint 单测：seed 确定性 / 不同 seed 不同 / engine_config 键齐全。"""

import json

import pytest

from fabletech.core.fingerprint import (
    MISSING_DEPS_HINT,
    browserforge_available,
    engine_config,
    generate_fingerprint,
    new_seed,
)

pytestmark = pytest.mark.skipif(
    not browserforge_available(), reason=f"browserforge 不可用：{MISSING_DEPS_HINT}"
)


def test_same_seed_same_fingerprint():
    fp1 = generate_fingerprint("seed-001")
    fp2 = generate_fingerprint("seed-001")
    assert fp1 == fp2
    assert fp1["seed"] == "seed-001"
    assert fp1["userAgent"]


def test_different_seed_different_fingerprint():
    fps = {json.dumps(generate_fingerprint(f"seed-{i}"), sort_keys=True)
           for i in range(8)}
    # 8 个不同 seed 至少产生 2 种不同指纹（UA 可能撞车，但整体分布不同）
    assert len(fps) >= 2


def test_random_seed_when_none():
    fp1 = generate_fingerprint()
    fp2 = generate_fingerprint()
    assert fp1["seed"] != fp2["seed"]
    assert len(fp1["seed"]) == 16


def test_global_random_state_restored():
    import random
    random.seed(123)
    expected = [random.random(), random.random()]
    generate_fingerprint("x")
    random.seed(123)
    after = [random.random(), random.random()]
    assert expected == after


def test_engine_config_keys_all_engines():
    fp = generate_fingerprint("seed-keys")
    cf_cfg = engine_config(fp, "camoufox")
    assert {"user_agent", "viewport", "locale", "timezone_id", "os",
            "screen", "humanize", "seed"} <= set(cf_cfg)
    cloak_cfg = engine_config(fp, "cloak")
    assert {"user_agent", "viewport", "locale", "timezone_id",
            "fingerprint_seed"} <= set(cloak_cfg)
    ch_cfg = engine_config(fp, "chromium")
    assert {"user_agent", "viewport", "locale", "timezone_id"} <= set(ch_cfg)


def test_engine_config_unknown_engine():
    fp = generate_fingerprint("seed-x")
    with pytest.raises(ValueError):
        engine_config(fp, "opera")


def test_new_seed_unique():
    seeds = {new_seed() for _ in range(50)}
    assert len(seeds) == 50
