"""读取 FableTech 主窗口实际尺寸（验证布局修复）。

用 ctypes 调 user32（避开 PowerShell Add-Type 限制）：
800x800 Logo 未降采样时窗口会被撑到 800+ 高/宽，正常应为 ~1180x760。
"""
import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
GetWindowTextW = user32.GetWindowTextW
GetWindowRect = user32.GetWindowRect
GetWindowThreadProcessId = user32.GetWindowThreadProcessId

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def main() -> int:
    import subprocess
    # 找出 FableTech 进程 PID（tasklist 输出解析）
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FableTech.exe",
                          "/FO", "CSV"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    pids = set()
    for line in out.splitlines()[1:]:
        parts = line.split('","')
        if len(parts) >= 2 and parts[0].strip('"') == "FableTech.exe":
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    print("FableTech PIDs:", sorted(pids))

    found = []

    def cb(hwnd, lparam):
        pid = wt.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            buf = ctypes.create_unicode_buffer(512)
            GetWindowTextW(hwnd, buf, 512)
            title = buf.value
            if title:
                r = RECT()
                GetWindowRect(hwnd, ctypes.byref(r))
                found.append((title, r.right - r.left, r.bottom - r.top))
        return True

    EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        print("未找到窗口（可能无交互桌面会话）")
        return 1
    ok = True
    for title, w, h in found:
        print(f"窗口: {title!r}  尺寸: {w}x{h}")
        # 正常窗口应为 ~1180x760；800x800 Logo 故障时会异常巨大
        if w > 2000 or h > 1200:
            print(f"  ✗ 尺寸异常（布局可能仍被撑爆）")
            ok = False
    print("RESULT:", "布局正常 ✓" if ok else "布局异常 ✗")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
