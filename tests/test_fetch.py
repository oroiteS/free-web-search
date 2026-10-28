"""Offline CLI contract tests: python3 -m unittest discover -s tests -p 'test_fetch.py'.

The CLIs are copied into a disposable directory; no repository/user configuration,
credentials or proxy settings are inherited. All network calls are intercepted.
Provider schemas: https://api-docs.deepseek.com/api/fetch,
https://docs.tavily.com/documentation/api-reference/endpoint/extract,
https://docs.keenable.ai/api-reference/fetch,
https://docs.firecrawl.dev/api-reference/endpoint/scrape,
https://docs.parallel.ai/extract/extract-quickstart,
https://github.com/anysearch-ai/anysearch-skill/blob/main/scripts/anysearch_cli.py,
Doubao Fetch: https://open.feedcoopapi.com/search_api/fetch (火山引擎豆包搜索文档).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://example.com/article?x=1'
KEY = 'offline-test-key'
TITLE = 'Fixture title'
CONTENT = '# Fixture\nOffline content'
ENGINES = ('deepseek', 'tavily', 'keenable', 'firecrawl', 'parallel', 'anysearch', 'doubao')
# 个别引擎的环境变量名不符合 <ENGINE>_API_KEY 规则
ENV_KEYS = {'doubao': 'DOUBAO_SEARCH_API_KEY'}
FIXTURES = {
    'deepseek': {'title': TITLE, 'content': CONTENT},
    'tavily': {'results': [{'url': URL, 'raw_content': CONTENT}], 'failed_results': []},
    'keenable': {'url': URL, 'title': TITLE, 'content': CONTENT},
    'firecrawl': {'success': True, 'data': {'markdown': CONTENT, 'metadata': {'title': TITLE}}},
    'parallel': {'results': [{'url': URL, 'title': TITLE, 'full_content': CONTENT}], 'errors': []},
    'anysearch': {'code': 0, 'data': {'url': URL, 'title': TITLE, 'content': CONTENT}},
    'doubao': {'ResponseMetadata': {'RequestId': 'req'}, 'Result': {'Data': {
        'StatusCode': 200, 'Url': URL, 'Title': TITLE, 'ContentText': CONTENT}, 'LogId': 'log'}},
}
ENDPOINTS = {
    'deepseek': 'https://api.deepseek.com/v1/tools/fetch',
    'tavily': 'https://api.tavily.com/extract',
    'keenable': 'https://api.keenable.ai/v1/fetch',
    'firecrawl': 'https://api.firecrawl.dev/v2/scrape',
    'parallel': 'https://api.parallel.ai/v1/extract',
    'anysearch': 'https://api.anysearch.com/v1/extract',
    'doubao': 'https://open.feedcoopapi.com/search_api/fetch',
}

# Patching OpenerDirector.open covers the existing Python HTTP adapter, including
# its non-2xx path. Socket denial is a fail-closed guard for other code paths.
PY_PRELOAD = r'''
import io, json, os, socket, urllib.error, urllib.request

def blocked(*args, **kwargs):
    raise RuntimeError('offline test: socket access blocked')
socket.socket.connect = blocked
socket.create_connection = blocked

def open_mock(self, req, *args, **kwargs):
    entry = {'url': req.full_url, 'method': req.get_method(),
             'headers': dict(req.header_items()),
             'body': req.data.decode('utf-8') if req.data else None}
    with open(os.environ['MOCK_LOG'], 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')
    status = int(os.environ['MOCK_STATUS'])
    payload = os.environ['MOCK_RESPONSE'].encode('utf-8')
    if status >= 400:
        raise urllib.error.HTTPError(req.full_url, status, 'mock', {}, io.BytesIO(payload))
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    response = Response(payload)
    response.status = status
    return response
urllib.request.OpenerDirector.open = open_mock
'''

# Node's preload stubs global fetch before ESM evaluation; explicit socket and
# HTTP guards prevent proxy or alternate HTTP transports escaping the test.
JS_PRELOAD = r'''
const fs = require('node:fs');
const blocked = () => { throw new Error('offline test: network access blocked'); };
const net = require('node:net');
net.connect = net.createConnection = blocked;
net.Socket.prototype.connect = blocked;
require('node:tls').connect = blocked;
for (const name of ['node:http', 'node:https']) {
  const m = require(name); m.request = m.get = blocked;
}
globalThis.fetch = async (url, opts = {}) => {
  fs.appendFileSync(process.env.MOCK_LOG, JSON.stringify({url: String(url),
    method: opts.method || 'GET', headers: opts.headers || {}, body: opts.body || null}) + '\n');
  const status = Number(process.env.MOCK_STATUS);
  return {ok: status >= 200 && status < 300, status, text: async () => process.env.MOCK_RESPONSE};
};
'''


class FetchCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        scripts = self.dir / 'scripts'
        scripts.mkdir()
        for name in ('search.py', 'search.mjs'):
            shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
        (self.dir / 'sitecustomize.py').write_text(PY_PRELOAD, encoding='utf-8')
        (self.dir / 'preload.cjs').write_text(JS_PRELOAD, encoding='utf-8')
        self.log = self.dir / 'requests.jsonl'

    def invoke(self, runtime, engine, *, response=None, status=200, with_key=True, args=None, raw_response=None):
        self.log.write_text('', encoding='utf-8')
        env = {'PATH': os.defpath, 'HOME': str(self.dir), 'USERPROFILE': str(self.dir),
               'XDG_CONFIG_HOME': str(self.dir / 'xdg'), 'PYTHONPATH': str(self.dir),
               'MOCK_LOG': str(self.log), 'MOCK_STATUS': str(status),
               'MOCK_RESPONSE': raw_response if raw_response is not None else json.dumps(
                   FIXTURES[engine] if response is None else response)}
        if with_key:
            env[ENV_KEYS.get(engine, engine.upper() + '_API_KEY')] = KEY
        argv = ['--fetch', URL] if args is None else args
        if args is None:
            argv += ['--fetch-engine', engine]
        if runtime == 'python':
            command = [sys.executable, str(self.dir / 'scripts/search.py')]
        else:
            node = shutil.which('node')
            if not node:
                self.skipTest('Node.js not installed')
            command = [node, '--require', str(self.dir / 'preload.cjs'), str(self.dir / 'scripts/search.mjs')]
        proc = subprocess.run(command + argv, cwd=self.dir, env=env, text=True,
                              capture_output=True, timeout=15)
        requests = [json.loads(line) for line in self.log.read_text().splitlines()]
        return proc, requests

    def assert_request(self, engine, requests, keyed=True):
        self.assertEqual(len(requests), 1, requests)  # no retries or fallback
        req = requests[0]
        parts = urlsplit(req['url'])
        self.assertEqual(f'{parts.scheme}://{parts.netloc}{parts.path}',
                         ENDPOINTS[engine] if keyed or engine != 'keenable'
                         else ENDPOINTS[engine] + '/public')
        headers = {k.lower(): v for k, v in req['headers'].items()}
        if engine == 'keenable':
            self.assertEqual(req['method'], 'GET')
            query = parse_qs(parts.query)
            self.assertEqual(query.get('url'), [URL])
            self.assertEqual(query.get('live'), ['true'])
            if keyed:
                self.assertEqual(headers.get('x-api-key'), KEY)
            else:
                self.assertTrue(headers.get('x-keenable-title'))
                self.assertNotIn('x-api-key', headers)
            return
        self.assertEqual(req['method'], 'POST')
        self.assertIn('application/json', headers.get('content-type', ''))
        body = json.loads(req['body'])
        if engine in ('tavily', 'parallel'):
            self.assertIn(body.get('urls'), ([URL], URL) if engine == 'tavily' else ([URL],))
        elif engine == 'doubao':
            self.assertEqual(body.get('Url'), URL)  # 豆包字段名是大写 Url
        else:
            self.assertEqual(body.get('url'), URL)
        if engine == 'parallel':
            self.assertIs(body.get('advanced_settings', {}).get('full_content'), True)
            self.assertEqual(headers.get('x-api-key'), KEY)
        elif engine == 'firecrawl':
            self.assertIn('markdown', body.get('formats', []))
        if engine in ('deepseek', 'tavily', 'firecrawl', 'doubao') or (engine == 'anysearch' and keyed):
            self.assertEqual(headers.get('authorization'), 'Bearer ' + KEY)
        elif engine == 'anysearch':
            self.assertNotIn('authorization', headers)

    def assert_success(self, proc, engine):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result['engine'], engine + '-fetch')
        self.assertEqual(result['content'], CONTENT)
        self.assertEqual(result['title'], '' if engine == 'tavily' else TITLE)

    def test_keyed_provider_protocols(self):
        for runtime in ('python', 'node'):
            for engine in ENGINES:
                with self.subTest(runtime=runtime, engine=engine):
                    proc, requests = self.invoke(runtime, engine)
                    self.assert_success(proc, engine)
                    self.assert_request(engine, requests)

    def test_default_is_deepseek(self):
        for runtime in ('python', 'node'):
            with self.subTest(runtime=runtime):
                proc, requests = self.invoke(runtime, 'deepseek', args=['--fetch', URL])
                self.assert_success(proc, 'deepseek')
                self.assert_request('deepseek', requests)

    def test_supported_anonymous_endpoints(self):
        for runtime in ('python', 'node'):
            for engine in ('keenable', 'anysearch'):
                with self.subTest(runtime=runtime, engine=engine):
                    proc, requests = self.invoke(runtime, engine, with_key=False)
                    self.assert_success(proc, engine)
                    self.assert_request(engine, requests, keyed=False)

    def test_http_error_never_falls_back(self):
        for runtime in ('python', 'node'):
            for engine in ENGINES:
                with self.subTest(runtime=runtime, engine=engine):
                    proc, requests = self.invoke(runtime, engine, status=429, response={'error': 'rate limited'})
                    self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assert_request(engine, requests)

    def test_per_url_and_application_failures(self):
        failures = {
            'tavily': {'results': [], 'failed_results': [{'url': URL, 'error': 'unavailable'}]},
            'parallel': {'results': [], 'errors': [{'url': URL, 'error': 'unavailable'}]},
            'firecrawl': {'success': False, 'error': 'unavailable'},
            'anysearch': {'code': 1001, 'message': 'unavailable'},
            # 接口级错误（ResponseMetadata.Error），Result 为 null
            'doubao': {'ResponseMetadata': {'Error': {'CodeN': 10406, 'Code': '10406',
                                                      'Message': 'Free quota exhausted'}},
                       'Result': None},
        }
        for runtime in ('python', 'node'):
            for engine, fixture in failures.items():
                with self.subTest(runtime=runtime, engine=engine):
                    proc, requests = self.invoke(runtime, engine, response=fixture)
                    self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assert_request(engine, requests)

    def test_doubao_error_layers(self):
        """豆包的两层错误语义：目标页状态在 Result.Data.StatusCode，正文可能为空。"""
        target_404 = {'ResponseMetadata': {'RequestId': 'req'}, 'Result': {'Data': {
            'StatusCode': 404, 'Url': URL, 'Title': None, 'PublishTime': None,
            'ContentText': None}, 'LogId': 'log'}}
        empty_content = {'ResponseMetadata': {'RequestId': 'req'}, 'Result': {'Data': {
            'StatusCode': 200, 'Url': URL, 'Title': TITLE, 'ContentText': ''}, 'LogId': 'log'}}
        for runtime in ('python', 'node'):
            for name, fixture in (('target_404', target_404), ('empty_content', empty_content)):
                with self.subTest(runtime=runtime, case=name):
                    proc, requests = self.invoke(runtime, 'doubao', response=fixture)
                    self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assert_request('doubao', requests)

    def test_invalid_json_is_failure_without_fallback(self):
        for runtime in ('python', 'node'):
            for engine in ENGINES:
                with self.subTest(runtime=runtime, engine=engine):
                    proc, requests = self.invoke(runtime, engine, raw_response='{invalid')
                    self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assert_request(engine, requests)

    def test_missing_required_key_rejected_offline(self):
        for runtime in ('python', 'node'):
            for engine in ('deepseek', 'tavily', 'firecrawl', 'parallel', 'doubao'):
                with self.subTest(runtime=runtime, engine=engine):
                    proc, requests = self.invoke(runtime, engine, with_key=False)
                    self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(requests, [])

    def test_fetch_priority_config_selects_default_backend(self):
        for runtime in ('python', 'node'):
            with self.subTest(runtime=runtime):
                cfg_dir = self.dir / '.config' / 'free-web-search'
                cfg_dir.mkdir(parents=True, exist_ok=True)
                (cfg_dir / 'config.yaml').write_text(
                    'fetchPriority:\n  firecrawl: 1\n  deepseek: 2\n', encoding='utf-8')
                proc, requests = self.invoke(runtime, 'firecrawl', args=['--fetch', URL])
                self.assert_success(proc, 'firecrawl')
                self.assert_request('firecrawl', requests)

    def test_bad_engine_rejected_without_network(self):
        for runtime in ('python', 'node'):
            with self.subTest(runtime=runtime):
                proc, requests = self.invoke(runtime, 'deepseek', args=['--fetch', URL, '--fetch-engine', 'invalid'])
                self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(requests, [])


if __name__ == '__main__':
    unittest.main()
