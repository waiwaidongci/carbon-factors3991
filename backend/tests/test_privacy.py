"""隐私发布的端到端与并发/崩溃/幂等验证。"""
import math
import threading

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.config.database import Session, engine
from src.main import app
from src.services import privacy
from src.models.identity import User
from tests.conftest import auth as auth_factory, make_run as make_run_factory, POLICY


def publish(client, headers, run_id, month='2026-11', epsilon=0.25, label='', **extra):
    body = {'run_id': run_id, 'month': month, 'epsilon': epsilon, 'label': label, **extra}
    return client.post('/privacy/publications', json=body, headers=headers)


def budget_row(month):
    with Session() as db:
        return db.execute(text('SELECT budget_spent, budget_total FROM privacy_budgets WHERE month_key=:m'), {'m': month}).first()


def test_public_payload_has_full_department_universe_and_bounded_noise(client):
    admin = auth_factory('admin')
    run_id = make_run_factory()
    r = publish(client, admin, run_id, epsilon=0.25)
    assert r.status_code == 200, r.text
    body = r.json()
    names = [c['department'] for c in body['departments']]
    assert names == ['材料试验室', '工艺试验室', '光学试验室']
    assert body['laplace_scale'] == 4000.0  # B=1000, ε=0.25
    # 噪声为常见量级：断言不是天文数字即可（随机性测试不做精确相等）。
    for cell in body['departments']:
        assert abs(cell['noisy_kg']) < 1e6
    assert body['clip_upper_bound_kg'] == 1000.0


def test_clipping_caps_single_record_influence(client):
    sources = [{'period': '2026-11', 'department': '材料试验室', 'activity': '电力', 'quantity': 10000, 'unit': 'kWh'}]
    snapshot = {'policy': POLICY, 'sources': sources}
    # 原始 10000*0.57=5700 kg，截断到公开上限 1000。
    totals, matched = privacy._department_totals(snapshot, '2026-11')
    assert matched == 1
    assert totals['材料试验室'] == 1000
    assert totals['工艺试验室'] == 0 and totals['光学试验室'] == 0
    # 部门敏感度即 B，故整份发布的噪声尺度 B/ε。
    payload = privacy.build_payload(snapshot, '2026-11', 1.0)
    assert payload['laplace_scale'] == 1000.0


def test_department_outside_public_universe_is_rejected(client):
    admin = auth_factory('admin')
    sources = [{'period': '2026-11', 'department': '未公开部门', 'activity': '电力', 'quantity': 1, 'unit': 'kWh'}]
    run_id = make_run_factory(sources=sources)
    r = publish(client, admin, run_id, epsilon=0.25)
    assert r.status_code == 422
    assert budget_row('2026-11') is None


def test_monthly_budget_shared_across_versions_and_reports(client):
    admin = auth_factory('admin')
    mk = make_run_factory
    r1 = mk(); r2 = mk()
    a = publish(client, admin, r1, epsilon=0.25)
    b = publish(client, admin, r2, epsilon=0.25)
    assert a.status_code == 200 and b.status_code == 200
    spent, total = budget_row('2026-11')
    assert math.isclose(spent, 0.5) and total == 1.0
    # 同一月份、不同报告版本继续累计扣额。
    assert publish(client, admin, r1, epsilon=0.2).status_code == 200
    assert publish(client, admin, r2, epsilon=0.2).status_code == 200
    spent, _ = budget_row('2026-11')
    assert math.isclose(spent, 0.9)
    # 超出剩余额度必须 409，且不改变已扣额（用全新 ε 避免命中幂等）。
    r3 = mk()
    before = budget_row('2026-11')[0]
    over = publish(client, admin, r3, epsilon=0.15)
    assert over.status_code == 409
    assert math.isclose(budget_row('2026-11')[0], before)


def test_over_budget_rejected_without_charge(client):
    admin = auth_factory('admin')
    run_id = make_run_factory()
    assert publish(client, admin, run_id, epsilon=0.8).status_code == 200
    before = budget_row('2026-11')[0]
    r = publish(client, admin, run_id, epsilon=0.5)
    assert r.status_code == 409
    assert math.isclose(budget_row('2026-11')[0], before)


def test_retry_same_publication_returns_original_noise_and_no_double_charge(client):
    admin = auth_factory('admin')
    run_id = make_run_factory()
    a = publish(client, admin, run_id, epsilon=0.25)
    spent_once = budget_row('2026-11')[0]
    for _ in range(5):
        b = publish(client, admin, run_id, epsilon=0.25)
        assert b.status_code == 200
        assert b.json()['id'] == a.json()['id']
        assert b.json()['departments'] == a.json()['departments']
    assert math.isclose(budget_row('2026-11')[0], spent_once)
    assert math.isclose(spent_once, 0.25)


def test_same_report_different_month_or_epsilon_is_a_new_charge(client):
    admin = auth_factory('admin')
    make_run = make_run_factory
    # 同一份「全部」快照，同时含两个月的数据，跨月发布各自扣该月预算。
    sources = [
        {'period': '2026-10', 'department': '材料试验室', 'activity': '电力', 'quantity': 100, 'unit': 'kWh'},
        {'period': '2026-11', 'department': '工艺试验室', 'activity': '柴油', 'quantity': 10, 'unit': 'L'},
    ]
    run_id = make_run(period='全部', sources=sources)
    # 同一份快照跨两个月。
    assert publish(client, admin, run_id, month='2026-10', epsilon=0.25).status_code == 200
    assert publish(client, admin, run_id, month='2026-11', epsilon=0.25).status_code == 200
    assert math.isclose(budget_row('2026-10')[0], 0.25)
    assert math.isclose(budget_row('2026-11')[0], 0.25)
    # 同月不同 ε 是新发布，单独扣额。
    assert publish(client, admin, run_id, month='2026-11', epsilon=0.1).status_code == 200
    assert math.isclose(budget_row('2026-11')[0], 0.35)


def test_concurrent_requests_never_overspend(client):
    admin = auth_factory('admin')
    mk = make_run_factory
    runs = [mk() for _ in range(12)]
    results = []
    barrier = threading.Barrier(len(runs))

    def worker(run_id):
        barrier.wait()
        results.append(publish(client, admin, run_id, epsilon=0.25).status_code)

    threads = [threading.Thread(target=worker, args=(rid,)) for rid in runs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    spent, total = budget_row('2026-11')
    assert spent <= total + 1e-9
    assert results.count(200) == 4  # 1.0 / 0.25
    assert results.count(409) == 8
    assert math.isclose(spent, 1.0)


def test_concurrent_identical_publish_charges_once(client):
    admin = auth_factory('admin')
    run_id = make_run_factory()
    results = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        r = publish(client, admin, run_id, epsilon=0.25)
        results.append((r.status_code, r.json().get('id')))
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert {code for code, _ in results} == {200}
    assert len({pid for _, pid in results}) == 1
    assert math.isclose(budget_row('2026-11')[0], 0.25)


def test_crash_after_charge_rolls_back_both_budget_and_publication(client):
    admin = auth_factory('admin')
    run_id = make_run_factory()
    # 直接走服务层做故障注入（HTTP 层不暴露该开关）。
    with Session() as db:
        user = db.query(User).filter_by(role='admin').first()
        try:
            privacy.publish(db, user, run_id, '2026-11', 0.25, crash_stage='after_charge')
            assert False, '应注入失败'
        except RuntimeError as exc:
            assert '注入故障' in str(exc)
    # 扣额与发布同成同败：均不存在。
    assert budget_row('2026-11') is None
    with Session() as db:
        assert db.execute(text('SELECT COUNT(*) FROM privacy_publications')).scalar() == 0
    # 崩溃后重试正常成功且只扣一次。
    r = publish(client, admin, run_id, epsilon=0.25)
    assert r.status_code == 200
    assert math.isclose(budget_row('2026-11')[0], 0.25)


def test_crash_after_commit_is_recoverable_idempotently(client):
    admin = auth_factory('admin')
    run_id = make_run_factory()
    from src.models.identity import User
    with Session() as db:
        user = db.query(User).filter_by(role='admin').first()
        try:
            privacy.publish(db, user, run_id, '2026-11', 0.25, crash_stage='after_commit')
            assert False
        except RuntimeError:
            pass
    # 事务已提交，重试返回同一条结果，不重复扣额。
    assert math.isclose(budget_row('2026-11')[0], 0.25)
    r = publish(client, admin, run_id, epsilon=0.25)
    assert r.status_code == 200
    assert math.isclose(budget_row('2026-11')[0], 0.25)


def test_consistency_endpoint_reports_match(client):
    admin = auth_factory('admin')
    mk = make_run_factory
    for month in ('2026-10', '2026-11'):
        publish(client, admin, mk(), month=month, epsilon=0.25)
        publish(client, admin, mk(), month=month, epsilon=0.1)
    r = client.get('/privacy/admin/consistency', headers=admin)
    assert r.status_code == 200
    assert r.json()['consistent'] is True


def test_revoke_keeps_budget_spent(client):
    admin = auth_factory('admin')
    run_id = make_run_factory()
    pid = publish(client, admin, run_id, epsilon=0.25).json()['id']
    assert client.post(f'/privacy/publications/{pid}/revoke', headers=admin).status_code == 200
    assert math.isclose(budget_row('2026-11')[0], 0.25)
    # 公开列表与详情不再暴露已撤回结果。
    assert client.get('/privacy/publications', headers=admin).json() == []
    assert client.get(f'/privacy/publications/{pid}', headers=admin).status_code == 404
    # 撤回后重新「同一发布」仍返回原记录，不重新扣额。
    again = publish(client, admin, run_id, epsilon=0.25)
    assert again.json()['id'] == pid
    assert math.isclose(budget_row('2026-11')[0], 0.25)


def test_viewer_can_only_see_published_results(client):
    admin = auth_factory('admin')
    viewer = auth_factory('viewer')
    analyst = auth_factory('analyst')
    run_id = make_run_factory()
    pid = publish(client, analyst, run_id, epsilon=0.25).json()['id']
    # 受限阅读者可见公开结果。
    assert client.get('/privacy/publications', headers=viewer).status_code == 200
    pub = client.get(f'/privacy/publications/{pid}', headers=viewer).json()
    assert 'run_id' not in pub and 'payload' not in pub
    assert client.get(f'/privacy/publications/{pid}/csv', headers=viewer).status_code == 200
    # 原始底稿、报告、快照、交付入口全部拒绝。
    for path in ('/sources', '/periods', '/policies', '/runs', f'/runs/{run_id}', f'/runs/{run_id}/csv',
                 '/schedules', '/deliveries', '/deliveries/1/csv', '/audits'):
        r = client.get(path, headers=viewer)
        assert r.status_code == 403, (path, r.status_code)
    # 受限阅读者不可发布、不可撤回、不可看内部映射或预算。
    assert publish(client, viewer, run_id, epsilon=0.25).status_code == 403
    assert client.post(f'/privacy/publications/{pid}/revoke', headers=viewer).status_code == 403
    assert client.get('/privacy/admin/publications', headers=viewer).status_code == 403
    assert client.get('/privacy/admin/budgets', headers=viewer).status_code == 403
    assert client.get('/privacy/admin/consistency', headers=viewer).status_code == 403


def test_published_response_does_not_leak_raw_values(client):
    admin = auth_factory('admin')
    run_id = make_run_factory()
    body = publish(client, admin, run_id, epsilon=0.25).json()
    dumped = str(body)
    assert 'snapshot' not in body and 'result' not in body and 'checksum' not in body
    assert 'run_id' in body  # 管理员可见，仅用于审计
    viewer = auth_factory('viewer')
    public = client.get(f"/privacy/publications/{body['id']}", headers=viewer).json()
    assert 'run_id' not in public
    assert 'snapshot' not in public and 'result' not in public


def test_unfinished_runs_cannot_publish(client):
    admin = auth_factory('admin')
    mk = make_run_factory
    assert publish(client, admin, mk(status='queued', approved=False), epsilon=0.25).status_code == 409
    assert publish(client, admin, mk(status='running', approved=False), epsilon=0.25).status_code == 409
    assert publish(client, admin, 99999, epsilon=0.25).status_code == 404
    # 已冻结完成但未复核的报告仍可发布（发布只依赖冻结快照）。
    assert publish(client, admin, mk(approved=False), epsilon=0.25).status_code == 200


def test_month_without_records_rejected(client):
    admin = auth_factory('admin')
    run_id = make_run_factory(period='2026-11')
    r = publish(client, admin, run_id, month='2027-01', epsilon=0.25)
    assert r.status_code == 422
    assert budget_row('2027-01') is None


def test_laplace_noise_is_symmetric_scale(client):
    values = [privacy.sample_laplace(1000.0) for _ in range(2000)]
    mean = sum(values) / len(values)
    assert abs(mean) < 200  # 期望 0；样本均值不应系统性偏移。
    assert max(values) > 1000 and min(values) < -1000
