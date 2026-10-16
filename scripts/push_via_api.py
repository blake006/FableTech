"""通过 GitHub REST API (git data) 推送本地 commit 到远端。

用法：python scripts/push_via_api.py <commit_sha_1> [<commit_sha_2> ...]
按顺序逐个推送（每个 commit 只含其相对父提交的变更文件）。
"""
import base64
import json
import subprocess
import sys
import urllib.request

TOKEN = open('.git/wb_gh_token', 'rb').read().decode().strip()
OWNER, REPO, BRANCH = 'blake006', 'FableTech', 'main'
API = f'https://api.github.com/repos/{OWNER}/{REPO}'

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(method, path, body=None):
    url = API + path if path.startswith('/') else path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', f'Bearer {TOKEN}')
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    with opener.open(req) as r:
        return json.loads(r.read().decode())


def git(*args):
    # core.quotePath=false：非 ASCII 路径原样输出（否则 git 会输出
    # "docs/01-\345\274\200..." 这种带引号的八进制转义字面量）
    return subprocess.run(['git', '-c', 'core.quotePath=false'] + list(args),
                          capture_output=True, text=True,
                          encoding='utf-8').stdout


def commit_files(sha):
    # 根 commit 无父提交，diff-tree 需 --root；统一用 show 更稳
    out = git('show', '--pretty=format:', '--name-only', sha)
    return [l for l in out.splitlines() if l]


def commit_message(sha):
    return git('log', '-1', '--format=%B', sha).strip()


def ref_exists():
    """远端分支是否存在（空仓库返回 409 'Git Repository is empty'）。"""
    try:
        api('GET', f'/git/ref/heads/{BRANCH}')
        return True
    except urllib.error.HTTPError as e:
        if e.code in (404, 409):
            return False
        raise


def push_commit(sha, parent_remote_sha, force=False):
    files = commit_files(sha)
    print(f'  pushing {sha[:7]} ({len(files)} files): {commit_message(sha).splitlines()[0][:60]}')
    tree_entries = []
    for path in files:
        content = git('show', f'{sha}:{path}')
        blob = api('POST', '/git/blobs', {
            'content': base64.b64encode(content.encode('utf-8')).decode(),
            'encoding': 'base64',
        })
        tree_entries.append({
            'path': path,
            'mode': '100644',
            'type': 'blob',
            'sha': blob['sha'],
        })
    tree_body = {'tree': tree_entries}
    if parent_remote_sha:
        parent_commit = api('GET', f'/git/commits/{parent_remote_sha}')
        tree_body['base_tree'] = parent_commit['tree']['sha']
    tree = api('POST', '/git/trees', tree_body)
    commit_body = {'message': commit_message(sha), 'tree': tree['sha']}
    if parent_remote_sha:
        commit_body['parents'] = [parent_remote_sha]
    commit = api('POST', '/git/commits', commit_body)
    # 创建或更新分支引用
    if ref_exists():
        api('PATCH', f'/git/refs/heads/{BRANCH}',
            {'sha': commit['sha'], 'force': force})
    else:
        api('POST', '/git/refs', {'ref': f'refs/heads/{BRANCH}',
                                  'sha': commit['sha']})
    return commit['sha']


def main():
    shas = sys.argv[1:]
    force = '--force' in shas
    shas = [s for s in shas if s != '--force']
    parent = None
    if ref_exists():
        if force:
            # 强制重建：从根提交（最早的 init commit）开始，丢弃错误历史
            commits = api('GET', f'/commits?sha={BRANCH}&per_page=100')
            parent = commits[-1]['sha']  # 列表按时间倒序，最后一个最早
            print(f'FORCE rebuild from root {parent[:7]}')
        else:
            ref = api('GET', f'/git/ref/heads/{BRANCH}')
            parent = ref['object']['sha']
            print(f'remote {BRANCH} exists at {parent[:7]}, pushing incrementally')
    else:
        print(f'remote {BRANCH} does not exist (empty repo), creating')
    for sha in shas:
        parent = push_commit(sha, parent, force=force)
    # 验证
    ref = api('GET', f'/git/ref/heads/{BRANCH}')
    print(f"remote {BRANCH} now at {ref['object']['sha']}")
    print('DONE')


if __name__ == '__main__':
    main()
