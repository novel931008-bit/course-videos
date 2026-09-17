"""本機課程編輯器。

雙擊專案根目錄的「編輯課程.bat」啟動，會在瀏覽器開啟編輯頁面。
伺服器只綁定 127.0.0.1，同一網路的其他裝置無法連線修改。
"""
import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / 'docs'
DATA = SITE / 'courses.json'
EDITOR_HTML = Path(__file__).resolve().parent / 'editor.html'

APP_ID = 'course-editor'
TOKEN = secrets.token_urlsafe(24)
TZ = timezone(timedelta(hours=8))
FIELDS = ('date', 'subject', 'unit', 'teacher', 'url')
DEFAULT_TITLE = '課程影片總覽'
YT_ID = re.compile(
    r'(?:youtu\.be/|youtube(?:-nocookie)?\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/|v/))([\w-]{11})')
PUBLISH_LOCK = threading.Lock()


# ---------------- 資料 ----------------
def load_data():
    try:
        data = json.loads(DATA.read_text(encoding='utf-8'))
    except FileNotFoundError:
        data = {}
    data.setdefault('title', DEFAULT_TITLE)
    data.setdefault('description', '')
    data.setdefault('courses', [])
    return data


def is_valid_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').strftime('%Y-%m-%d') == s
    except ValueError:
        return False


def normalize(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('courses'), list):
        raise ValueError('資料格式錯誤')
    rows, errors = [], []
    for n, c in enumerate(payload['courses'], 1):
        if not isinstance(c, dict):
            raise ValueError(f'第 {n} 列格式錯誤')
        row = {k: str(c.get(k) or '').strip() for k in FIELDS}
        if not is_valid_date(row['date']):
            errors.append(f'第 {n} 列：日期不正確')
        if not row['subject']:
            errors.append(f'第 {n} 列：請填寫科別')
        if not row['unit']:
            errors.append(f'第 {n} 列：請填寫單元')
        if row['url'] and not re.match(r'^https?://\S+$', row['url'], re.I):
            errors.append(f'第 {n} 列：影片連結必須是 http:// 或 https:// 開頭的網址')
        rows.append(row)
    if errors:
        raise ValueError('\n'.join(errors))
    rows.sort(key=lambda r: r['date'])  # 穩定排序：同一天維持原本順序
    return {
        'title': str(payload.get('title') or '').strip() or DEFAULT_TITLE,
        'description': str(payload.get('description') or '').strip(),
        'updatedAt': datetime.now(TZ).isoformat(timespec='seconds'),
        'courses': rows,
    }


def write_data(data):
    tmp = DATA.with_name(DATA.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    os.replace(tmp, DATA)


def oembed(video_url):
    m = YT_ID.search(video_url)
    if not m:
        return {'ok': False, 'error': '不是 YouTube 連結'}
    api = 'https://www.youtube.com/oembed?format=json&url=' + urllib.parse.quote(
        f'https://www.youtube.com/watch?v={m.group(1)}', safe='')
    req = urllib.request.Request(api, headers={'User-Agent': 'Mozilla/5.0 (course-editor)'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            info = json.load(r)
    except urllib.error.HTTPError as e:
        reason = {401: '影片是「私人」，其他人無法觀看', 403: '影片是「私人」，其他人無法觀看',
                  404: '找不到這支影片'}.get(e.code, f'YouTube 回應 {e.code}')
        return {'ok': False, 'error': reason}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return {'ok': False, 'error': f'無法連線到 YouTube（{e}）'}
    return {'ok': True, 'id': m.group(1), 'title': info.get('title', ''), 'author': info.get('author_name', '')}


# ---------------- git ----------------
def git(*args, timeout=60):
    try:
        p = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, '找不到 git，請先安裝 Git for Windows。'
    except subprocess.TimeoutExpired:
        return 124, f'git {args[0]} 逾時（超過 {timeout} 秒）。'


def pages_url(remote):
    cname = SITE / 'CNAME'
    if cname.exists():
        domain = cname.read_text(encoding='utf-8').strip()
        if domain:
            return f'https://{domain}/'
    m = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$', remote)
    if not m:
        return ''
    owner, repo = m.group(1).lower(), m.group(2)
    if repo.lower() == f'{owner}.github.io':
        return f'https://{owner}.github.io/'
    return f'https://{owner}.github.io/{repo}/'


def git_status():
    info = {'repo': False, 'remote': '', 'siteUrl': '', 'pending': False}
    code, top = git('rev-parse', '--show-toplevel')
    if code != 0 or Path(top).resolve() != ROOT:
        return info
    info['repo'] = True
    code, remote = git('remote', 'get-url', 'origin')
    if code == 0:
        info['remote'] = remote
        info['siteUrl'] = pages_url(remote)
    code, changes = git('status', '--porcelain')
    dirty = code == 0 and bool(changes)
    code, ahead = git('rev-list', '--count', '@{u}..HEAD')
    unpushed = ahead.strip() != '0' if code == 0 else bool(info['remote'])
    info['pending'] = dirty or unpushed
    return info


def publish():
    st = git_status()
    if not st['repo'] or not st['remote']:
        return False, '這個資料夾還沒有連結到 GitHub，請先完成 README 裡的「第一次設定」。'
    log = []

    def run(*args, timeout=60):
        code, out = git(*args, timeout=timeout)
        log.append(f'$ git {" ".join(args)}\n{out}'.rstrip())
        return code == 0

    ok = run('add', '-A')
    if ok and git('diff', '--cached', '--quiet')[0] != 0:
        ok = run('commit', '-m', f'更新課程資料 {datetime.now(TZ):%Y-%m-%d %H:%M}')
    if ok:
        ok = run('push', '-u', 'origin', 'HEAD', timeout=300)
    return ok, '\n\n'.join(log)


def sync_latest():
    """啟動時若本機沒有未發布的變更，先從 GitHub 拉最新版本（換電腦編輯時才不會蓋掉）。"""
    st = git_status()
    if not st['remote'] or st['pending'] or git('rev-parse', '--abbrev-ref', '@{u}')[0] != 0:
        return
    print('正在從 GitHub 取得最新資料…')
    code, out = git('pull', '--ff-only', timeout=30)
    if code != 0:
        print('（無法同步，先使用本機資料）\n' + out)


# ---------------- HTTP ----------------
class Handler(SimpleHTTPRequestHandler):
    server_version = 'CourseEditor/1.0'
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        '.html': 'text/html; charset=utf-8',
        '.js': 'text/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8',
        '.json': 'application/json; charset=utf-8',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def send_body(self, body, ctype, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=HTTPStatus.OK):
        self.send_body(json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8', status)

    def allowed(self, need_token):
        port = self.server.server_port
        host_ok = (self.headers.get('Host') or '').lower() in {f'127.0.0.1:{port}', f'localhost:{port}'}
        return host_ok and (not need_token or secrets.compare_digest(self.headers.get('X-Editor-Token') or '', TOKEN))

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        if not self.allowed(need_token=path.startswith('/api/') and path != '/api/ping'):
            return self.send_error(HTTPStatus.FORBIDDEN)
        if path in ('/', '/index.html'):
            html = EDITOR_HTML.read_text(encoding='utf-8').replace('__EDITOR_TOKEN__', TOKEN)
            return self.send_body(html.encode('utf-8'), 'text/html; charset=utf-8')
        if path == '/api/ping':
            return self.send_json({'app': APP_ID})
        if path == '/api/data':
            return self.send_json({'data': load_data(), 'git': git_status()})
        if path == '/api/oembed':
            url = urllib.parse.parse_qs(parts.query).get('url', [''])[0]
            return self.send_json(oembed(url))
        if path == '/site':
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header('Location', '/site/')
            return self.end_headers()
        if path.startswith('/site/'):
            self.path = self.path[len('/site'):]
            return super().do_GET()
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_HEAD(self):
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    def do_POST(self):
        if not self.allowed(need_token=True):
            return self.send_error(HTTPStatus.FORBIDDEN)
        path = urllib.parse.urlsplit(self.path).path
        try:
            length = int(self.headers.get('Content-Length') or 0)
            if length > 5_000_000:
                raise ValueError('資料太大')
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError as e:
            return self.send_json({'ok': False, 'error': f'無法解析請求：{e}'}, HTTPStatus.BAD_REQUEST)

        if path == '/api/save':
            try:
                data = normalize(body)
            except ValueError as e:
                return self.send_json({'ok': False, 'error': str(e)}, HTTPStatus.BAD_REQUEST)
            write_data(data)
            return self.send_json({'ok': True, 'data': data, 'git': git_status()})

        if path == '/api/publish':
            if not PUBLISH_LOCK.acquire(blocking=False):
                return self.send_json({'ok': False, 'log': '已經在發布中，請稍候。'}, HTTPStatus.CONFLICT)
            try:
                ok, log = publish()
            finally:
                PUBLISH_LOCK.release()
            return self.send_json({'ok': ok, 'log': log, 'git': git_status()})

        if path == '/api/shutdown':
            self.send_json({'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self.send_error(HTTPStatus.NOT_FOUND)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 的 SO_REUSEADDR 會讓兩個程式搶同一個埠，改用獨佔模式
    allow_reuse_address = sys.platform != 'win32'

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def running_editor(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=1) as r:
            return json.load(r).get('app') == APP_ID
    except (OSError, ValueError):
        return False


def main():
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='本機課程編輯器')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true', help='不要自動開啟瀏覽器')
    ap.add_argument('--no-sync', action='store_true', help='啟動時不從 GitHub 同步')
    args = ap.parse_args()

    if running_editor(args.port):
        url = f'http://127.0.0.1:{args.port}/'
        print(f'編輯器已經在執行中：{url}')
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    if not args.no_sync:
        sync_latest()

    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = Server(('127.0.0.1', port), Handler)
            break
        except OSError:
            continue
    if server is None:
        print('找不到可用的連接埠，無法啟動編輯器。')
        return 1

    url = f'http://127.0.0.1:{server.server_port}/'
    print('=' * 52)
    print(f'  課程編輯器已啟動：{url}')
    print('  編輯完成後，在頁面按「結束編輯器」或直接關閉此視窗。')
    print('=' * 52, flush=True)
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print('編輯器已關閉。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
