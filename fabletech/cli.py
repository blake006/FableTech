"""FableTech 命令行接口：create-profile / launch / run-recipe /
export-cookies / import-cookies / proxy-test。

主密码来源（按优先级）：--password 参数 > 环境变量 FABLETECH_MASTER_PASSWORD
> 交互式输入（getpass，不回显）。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .config import is_portable, paths
from .core.cookies import CookieVault
from .core.crypto import Vault, VaultError
from .core.engines import EngineUnavailableError, engine_status
from .core.fingerprint import generate_fingerprint
from .core.profiles import ProfileService
from .core.proxy import parse_proxy_bulk, query_geo, suggest_locale
from .core.proxy import test_proxy as run_proxy_test
from .core.scheduler import Scheduler
from .core.storage import Storage


def _get_password(args, prompt: str) -> str:
    if getattr(args, "password", None):
        return args.password
    env = os.environ.get("FABLETECH_MASTER_PASSWORD")
    if env:
        return env
    return getpass.getpass(prompt)


def _unlock(args) -> tuple[Storage, Vault, ProfileService]:
    """解锁并装配服务。返回 (storage, vault, profiles)。"""
    p = paths()
    storage = Storage(p.db)
    if not Vault.is_initialized(p.db):
        pw = _get_password(args, "首次使用，请设置主密码（≥8 位）：")
        if len(pw) < 8:
            print("错误：主密码至少 8 位", file=sys.stderr)
            raise SystemExit(2)
        vault = Vault.initialize(p.db, pw)
        print("已创建主密码，加密仓初始化完成。")
    else:
        pw = _get_password(args, "主密码：")
        try:
            vault = Vault.unlock(p.db, pw)
        except VaultError as e:
            print(f"解锁失败：{e}", file=sys.stderr)
            raise SystemExit(2)
    profiles = ProfileService(storage, vault)
    return storage, vault, profiles


# ---------- 子命令 ----------

def cmd_create_profile(args) -> int:
    _, vault, profiles = _unlock(args)
    p = profiles.create(
        name=args.name, engine=args.engine,
        platform_tags=args.tags or "", group_name=args.group or "",
        notes=args.notes or "")
    fp = generate_fingerprint(p["seed"])
    print(f"档案已创建：id={p['id']} 名称={p['name']} 引擎={p['engine']} "
          f"seed={p['seed']}")
    print(f"指纹 UA：{fp['userAgent'][:80]}…")
    return 0


def cmd_launch(args) -> int:
    _, vault, profiles = _unlock(args)
    try:
        profiles.launch(args.profile_id, headless=args.headless)
    except EngineUnavailableError as e:
        print(f"引擎不可用：{e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"启动失败：{e}", file=sys.stderr)
        return 3
    print(f"档案 {args.profile_id} 已启动。关闭窗口或 Ctrl+C 结束。")
    try:
        while profiles.is_running(args.profile_id):
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        profiles.close(args.profile_id)
    return 0


def cmd_run_recipe(args) -> int:
    storage, vault, profiles = _unlock(args)
    from .core.automations import RECIPES
    if args.recipe not in RECIPES:
        print(f"未知配方。可用：{'、'.join(RECIPES)}", file=sys.stderr)
        return 2
    params = json.loads(args.params) if args.params else {}
    scheduler = Scheduler(storage, launch_fn=profiles.launch,
                          vault=vault, close_fn=profiles.close)
    task = scheduler.submit_nowait(args.recipe, args.profile_id, params)
    print(f"任务已创建：{task['id']}（配方 {args.recipe}）")
    # 前台等待完成（含 waiting_human：CLI 下人工按回车继续）
    import time
    while True:
        t = storage.get_task(task["id"])
        if t["status"] == "waiting_human":
            print("\n【需要人工介入】请在浏览器中完成人工步骤，完成后按回车继续…")
            input()
            scheduler.resume(task["id"])
        elif t["status"] in ("done", "failed"):
            break
        time.sleep(2)
    t = storage.get_task(task["id"])
    print(f"任务状态：{t['status']}" +
          (f"，错误：{t['last_error']}" if t.get("last_error") else ""))
    return 0 if t["status"] == "done" else 4


def cmd_export_cookies(args) -> int:
    _, vault, _ = _unlock(args)
    cv = CookieVault(vault)
    fmt = args.format
    out = Path(args.output)
    if out.suffix.lower() not in (".txt", ".json"):
        print("错误：输出文件扩展名必须是 .txt 或 .json", file=sys.stderr)
        return 2
    out = out.resolve()
    if fmt == "netscape" or out.suffix == ".txt":
        text = cv.export_netscape(args.profile_id)
        if text is None:
            print("该档案暂无 Cookie 仓（空仓不报错，已跳过导出）")
            return 0
        out.write_text(text, encoding="utf-8")
    else:
        data = cv.export_json(args.profile_id)
        if data is None:
            print("该档案暂无 Cookie 仓（空仓不报错，已跳过导出）")
            return 0
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"已导出：{out}")
    return 0


def cmd_import_cookies(args) -> int:
    _, vault, _ = _unlock(args)
    cv = CookieVault(vault)
    src = Path(args.input).resolve()
    if not src.exists():
        print(f"文件不存在：{src}", file=sys.stderr)
        return 2
    if src.suffix.lower() == ".txt":
        n = cv.import_netscape(args.profile_id,
                               src.read_text(encoding="utf-8"))
    elif src.suffix.lower() == ".json":
        n = cv.import_json(args.profile_id,
                           json.loads(src.read_text(encoding="utf-8")))
    else:
        print("错误：仅支持 .txt（Netscape）或 .json 文件", file=sys.stderr)
        return 2
    print(f"已导入 {n} 条 Cookie 并加密存档")
    return 0


def _relay_test_cli(key: str, proxy) -> None:
    """中转型代理 CLI 测试：启动本地中转 → 实测链路 → 出口地理 → 释放。"""
    from .core.relay import relay_manager
    from .core.proxy import test_relay_proxy, suggest_locale
    print(f"{proxy.masked_url()} → 正在启动本地中转…")
    try:
        host, port = relay_manager().endpoint_for(key, proxy)
        ok, info = test_relay_proxy(host, port)
    except Exception as e:
        print(f"{proxy.masked_url()} → 不可用（{e}）")
        relay_manager().release(key)
        return
    print(f"{proxy.masked_url()} → {'可用' if ok else '不可用'}"
          + (f"（{info}）" if ok else f"（{info}）"))
    relay_manager().release(key)


def cmd_selfcheck(args) -> int:
    """指纹自检：启动档案浏览器采集实况 + 检测站点评分。"""
    _, vault, profiles = _unlock(args)
    from .core.fingerprint import engine_config, generate_fingerprint
    from .core.selfcheck import format_report, run_selfcheck
    try:
        profile = profiles.get(args.profile_id)
    except KeyError as e:
        print(str(e), file=sys.stderr)
        return 2
    fp = generate_fingerprint(profile["seed"])
    cfg = engine_config(fp, profile["engine"])
    headless = not getattr(args, "headed", False)
    report = run_selfcheck(args.profile_id, profiles, fp, cfg,
                           headless=headless)
    print(format_report(report))
    return 0 if report.get("ok") else 2


def cmd_proxy_test(args) -> int:
    if args.url:
        ok_list, bad = parse_proxy_bulk(args.url)
        if bad:
            for lineno, text, reason in bad:
                print(f"跳过第 {lineno} 行：{reason}", file=sys.stderr)
        for p in ok_list:
            if p.is_relay:
                _relay_test_cli(f"cli-{abs(hash(p.url())) % 10**8}", p)
                continue
            ok, latency, msg = run_proxy_test(p)
            print(f"{p.masked_url()} → {'可用' if ok else '不可用'}"
                  + (f"（{latency}ms）" if ok else f"（{msg}）"))
            if ok:
                geo = query_geo(p.host)
                if geo.get("countryCode"):
                    print(f"  出口：{geo.get('country')} {geo.get('city')} "
                          f"ISP={geo.get('isp', '—')} "
                          f"建议 locale={suggest_locale(geo['countryCode'])}")
        return 0 if ok_list else 2
    if args.proxy_id:
        _, vault, _ = _unlock(args)
        storage = Storage(paths().db)
        row = storage.get_proxy(args.proxy_id)
        if not row:
            print("代理不存在", file=sys.stderr)
            return 2
        from .core.proxy import ProxyInfo
        username = password = extra = None
        if row.get("username_enc"):
            username = vault.decrypt_str(row["username_enc"].hex())
        if row.get("password_enc"):
            password = vault.decrypt_str(row["password_enc"].hex())
        if row.get("extra_enc"):
            try:
                extra = json.loads(vault.decrypt_str(row["extra_enc"].hex()))
            except Exception:
                extra = None
        if row["type"] in ("hysteria2", "anytls") or extra:
            p = ProxyInfo.from_extra(row["type"], row["host"], row["port"],
                                     username, password, extra)
            _relay_test_cli(args.proxy_id, p)
            return 0
        p = ProxyInfo(row["type"], row["host"], row["port"], username, password)
        ok, latency, msg = run_proxy_test(p)
        print(f"{p.masked_url()} → {'可用' if ok else '不可用'}"
              + (f"（{latency}ms）" if ok else f"（{msg}）"))
        if ok:
            geo = query_geo(p.host)
            if geo.get("countryCode"):
                storage.update_proxy(args.proxy_id, country=geo.get("country", ""),
                                     city=geo.get("city", ""))
                print(f"  已更新地理：{geo.get('country')} {geo.get('city')}")
        return 0 if ok else 3
    print("错误：请提供 --url 或 --proxy-id", file=sys.stderr)
    return 2


def cmd_engines(args) -> int:
    for name, ok, hint in engine_status():
        print(f"{name}: {'可用' if ok else '不可用'}"
              + ("" if ok else f" — {hint}"))
    return 0


# ---------- 入口 ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fabletech",
        description="FableTech Browser — 自托管指纹浏览器 CLI")
    parser.add_argument("--password",
                        help="主密码（也可用环境变量 FABLETECH_MASTER_PASSWORD）")
    sub = parser.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("create-profile", help="创建档案（生成持久化指纹种子）")
    sp.add_argument("--name", required=True, help="档案名称")
    sp.add_argument("--engine", default="camoufox",
                    choices=["camoufox", "cloak", "chromium"], help="浏览器引擎")
    sp.add_argument("--tags", help="平台标签（逗号分隔，如 x,tiktok）")
    sp.add_argument("--group", help="分组名")
    sp.add_argument("--notes", help="备注")
    sp.set_defaults(func=cmd_create_profile)

    sp = sub.add_parser("launch", help="启动档案浏览器（前台运行）")
    sp.add_argument("profile_id", help="档案 ID")
    sp.add_argument("--headless", action="store_true", help="无头模式")
    sp.set_defaults(func=cmd_launch)

    sp = sub.add_parser("run-recipe", help="对档案执行配方")
    sp.add_argument("recipe", help="配方名（outlook_register/x_post/tiktok_post/health_check）")
    sp.add_argument("profile_id", help="档案 ID")
    sp.add_argument("--params", help="配方参数 JSON")
    sp.set_defaults(func=cmd_run_recipe)

    sp = sub.add_parser("export-cookies", help="导出档案 Cookie（解密后写出）")
    sp.add_argument("profile_id")
    sp.add_argument("output", help="输出路径（.txt Netscape 或 .json）")
    sp.add_argument("--format", choices=["netscape", "json"], default="netscape")
    sp.set_defaults(func=cmd_export_cookies)

    sp = sub.add_parser("import-cookies", help="导入 Cookie 并加密存档")
    sp.add_argument("profile_id")
    sp.add_argument("input", help="输入路径（.txt Netscape 或 .json）")
    sp.set_defaults(func=cmd_import_cookies)

    sp = sub.add_parser("proxy-test", help="代理连通测试 + 出口地理查询")
    sp.add_argument("--url", help="代理 URL（协议://user:pass@host:port）")
    sp.add_argument("--proxy-id", help="库中代理 ID")
    sp.set_defaults(func=cmd_proxy_test)

    sp = sub.add_parser("selfcheck", help="指纹自检（采集实况+检测站点截图）")
    sp.add_argument("profile_id", help="档案 ID")
    sp.add_argument("--headed", action="store_true",
                    help="有头模式自检（默认无头）")
    sp.set_defaults(func=cmd_selfcheck)
    sp = sub.add_parser("engines", help="检测引擎可用性")
    sp.set_defaults(func=cmd_engines)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台中文输出兼容（UTF-8）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已取消")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
