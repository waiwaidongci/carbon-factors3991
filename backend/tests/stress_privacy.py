"""真实 HTTP 服务器并发压测：多路同时发布，验证不超支、幂等只扣一次、返回原噪声。

运行（无需 docker）：``python3 tests/stress_privacy.py``
使用环境 DATABASE_URL 指定的数据库；未指定则用临时 SQLite 文件。
"""
import concurrent.futures
import json
import multiprocessing
import os
import pathlib
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def setup_env():
    os.environ.setdefault('CONFIG_PATH', str(ROOT / 'project.json'))
    os.environ.setdefault('DATABASE_URL', 'sqlite:///' + str(pathlib.Path(tempfile.mkdtemp()) / 'stress.db'))
    os.environ.setdefault('JWT_SECRET', 'stress-secret')
    os.environ.setdefault('SEED_DEMO', 'false')
    os.environ.setdefault('PRIVACY_CLIP_BOUND', '1000')
    os.environ.setdefault('PRIVACY_MONTHLY_EPSILON', '1.0')


def seed_data():
    from src.config.database import Base, engine, Session
    from src.models.identity import User
    from src.models.run import Run
    from src.utils.security import password_hash, token
    from tests.conftest import POLICY
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as db:
        admin = User(email='admin@stress.local', role='admin', password=password_hash('pw'))
        db.add(admin)
        sources = [
            {'period': '2026-10', 'department': '材料试验室', 'activity': '电力', 'quantity': 100, 'unit': 'kWh'},
            {'period': '2026-10', 'department': '工艺试验室', 'activity': '柴油', 'quantity': 10, 'unit': 'L'},
            {'period': '2026-11', 'department': '材料试验室', 'activity': '电力', 'quantity': 100, 'unit': 'kWh'},
            {'period': '2026-11', 'department': '工艺试验室', 'activity': '柴油', 'quantity': 10, 'unit': 'L'},
        ]
        run_ids = []
        for _ in range(20):
            run = Run(name='r', status='completed', snapshot={'policy': POLICY, 'sources': sources, 'period': '2026-11'}, checksum='x', result={'rows': []}, approved=True)
            db.add(run)
            db.flush()
            run_ids.append(run.id)
        db.commit()
        return run_ids, token(admin)


def start_server():
    import uvicorn
    from src.main import app
    config = uvicorn.Config(app, host='127.0.0.1', port=0, log_level='error')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, port


def post(port, token, run_id, month='2026-11', epsilon=0.25):
    body = json.dumps({'run_id': run_id, 'month': month, 'epsilon': epsilon, 'label': 'stress'}).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}/privacy/publications', data=body,
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='POST')
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def budget(month='2026-11'):
    from src.config.database import Session
    from sqlalchemy import text
    with Session() as db:
        row = db.execute(text('SELECT budget_spent, budget_total FROM privacy_budgets WHERE month_key=:m'), {'m': month}).first()
        return tuple(row) if row else (0.0, 1.0)


def main():
    setup_env()
    run_ids, token = seed_data()
    server, port = start_server()
    try:
        # 1) 8 个不同发布的并发申请：预算仅够 4 个 0.25。
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda rid: post(port, token, rid), run_ids[:8]))
        codes = [c for c, _ in results]
        spent, total = budget()
        print('不同发布并发：', codes, '扣额', spent, '/', total)
        assert codes.count(200) == 4, codes
        assert codes.count(409) == 4, codes
        assert abs(spent - 1.0) < 1e-9

        # 2) 同一发布被 8 个并发重试（另一个月份，独立预算）：只扣一次、返回同一 id 与同一噪声。
        rid = run_ids[10]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: post(port, token, rid, '2026-10'), range(8)))
        ids = {body['id'] for _, body in results}
        noises = {json.dumps(body['departments'], sort_keys=True) for _, body in results}
        print('同一发布并发重试：状态', {c for c, _ in results}, '唯一 id', len(ids), '唯一噪声', len(noises))
        assert {c for c, _ in results} == {200}
        assert len(ids) == 1 and len(noises) == 1
        assert abs(budget('2026-10')[0] - 0.25) < 1e-9
        print('最终扣额 2026-11 =', budget('2026-11'), ' 2026-10 =', budget('2026-10'))
    finally:
        server.should_exit = True
        time.sleep(0.2)


if __name__ == '__main__':
    main()
