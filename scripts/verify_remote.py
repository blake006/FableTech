"""端到端验证远端仓库内容与本地一致（绕开 shell 编码层）。"""
import base64
import json
import urllib.request

TOKEN = open('.git/wb_gh_token', 'rb').read().decode().strip()
OWNER, REPO = 'blake006', 'FableTech'
API = f'https://api.github.com/repos/{OWNER}/{REPO}'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path):
    req = urllib.request.Request(API + path)
    req.add_header('Authorization', f'Bearer {TOKEN}')
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    with opener.open(req) as r:
        return json.loads(r.read().decode('utf-8'))


# 1. 取远端 tree
ref = api('/git/ref/heads/main')
tree = api(f"/git/trees/{ref['object']['sha']}?recursive=1")
remote = {t['path']: t['sha'] for t in tree['tree'] if t['type'] == 'blob'}
print(f'remote blobs: {len(remote)}')

# 2. 本地文件清单（git ls-files）
import subprocess
local_files = subprocess.run(['git', '-c', 'core.quotePath=false', 'ls-files'],
                             capture_output=True, text=True,
                             encoding='utf-8').stdout.splitlines()
print(f'local files:  {len(local_files)}')

# 3. 比对清单
only_remote = sorted(set(remote) - set(local_files))
only_local = sorted(set(local_files) - set(remote))
print(f'only on remote: {only_remote}')
print(f'only on local:  {only_local}')

# 4. 抽查内容一致性（docs/01 + camoufox_engine + cli）
import hashlib
def local_sha(path):
    content = open(path, 'rb').read()
    blob = json.dumps({'content': base64.b64encode(content).decode(),
                       'encoding': 'base64'}, ensure_ascii=False)
    # 用 git hash-object 计算 blob sha（与 GitHub 算法一致）
    import subprocess as sp
    out = sp.run(['git', 'hash-object', path], capture_output=True, text=True).stdout.strip()
    return out

mismatch = []
for path in ['docs/01-开源指纹浏览器调研.md',
             'fabletech/core/engines/camoufox_engine.py',
             'fabletech/cli.py',
             'README.md',
             'tests/test_crypto.py']:
    r = remote.get(path)
    l = local_sha(path)
    status = 'OK' if r == l else 'MISMATCH'
    if r != l:
        mismatch.append(path)
    print(f'  {status}: {path}')

print('RESULT:', 'ALL VERIFIED' if not mismatch and not only_remote and not only_local
      else f'ISSUES: mismatch={mismatch}')
