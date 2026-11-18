"""月度部门排放的隐私发布。

设计要点（与需求逐条对应）：

* 聚合只读冻结报告快照（``Run.snapshot``），不读当前底稿。
* 部门维度使用公开固定全集：即使某部门没有记录也输出一条（值围绕 0 的噪声），
  因此「部门是否出现」不泄露信息。
* 每条记录的排放先按公开上限 B 截断，再按部门求和；单条记录变化对某个部门
  合计的影响不超过 B，故该部门合计的全局敏感度为 B。
* 每个部门独立加 Laplace(0, B/ε) 噪声。不同部门的记录集合互不相交（并行组合），
  整份发布的隐私预算仍为 ε。
* 预算按「月」共享：同一月份的不同报告版本都从该月总预算中扣减，只增不减。
* 幂等键由 (run_id, month, ε) 规范化后确定；同一发布重试返回当时持久化的原噪声，
  不重复扣额；不同发布各自累计。
* 扣额与发布结果写在同一个数据库事务里：要么都成立，要么都回滚。崩溃后重试时
  若事务已提交则返回原结果，未提交则不会有任何部分扣额。
* 撤回只改变可见状态，不改动已扣预算（不退款）。
"""
from __future__ import annotations

import hashlib
import math
import secrets
import time
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError

from src.config.settings import PRIVACY
from src.models.privacy import PrivacyBudget, PrivacyPublication
from src.models.run import Run
from src.services.calculation import emissions

_rng = secrets.SystemRandom()


def sample_laplace(scale: float) -> float:
    """返回 Laplace(0, scale) 噪声。使用系统熵源，避免可预测噪声。"""
    u = _rng.random() - 0.5
    return -scale * math.copysign(1.0, u) * math.log(1.0 - 2.0 * abs(u))


def department_universe() -> list[str]:
    return list(PRIVACY.get('department_universe') or [])


def clip_bound() -> float:
    return float(PRIVACY['clip_upper_bound_kg'])


def monthly_budget() -> float:
    return float(PRIVACY['monthly_epsilon'])


def default_epsilon() -> float:
    return float(PRIVACY['epsilon_per_publication'])


def _epsilon_text(epsilon: float) -> str:
    return format(float(epsilon), '.12g')


def idempotency_key(run_id: int, month: str, epsilon: float) -> str:
    raw = f'{int(run_id)}|{month.strip()}|{_epsilon_text(epsilon)}'
    return hashlib.sha256(raw.encode()).hexdigest()


def _validate_epsilon(epsilon: float) -> float:
    if not isinstance(epsilon, (int, float)) or isinstance(epsilon, bool) or not math.isfinite(epsilon):
        raise HTTPException(422, '隐私预算 ε 必须是有限数值')
    epsilon = float(epsilon)
    if epsilon <= 0:
        raise HTTPException(422, '隐私预算 ε 必须大于 0')
    return epsilon


def _department_totals(snapshot: dict, month: str) -> tuple[dict[str, Decimal], int]:
    policy = snapshot.get('policy') or {}
    factors = policy.get('factors') or {}
    universe = department_universe()
    totals = {name: Decimal(0) for name in universe}
    bound = Decimal(str(clip_bound()))
    matched = 0
    for source in snapshot.get('sources') or []:
        if source.get('period') != month:
            continue
        matched += 1
        department = source.get('department')
        if department not in totals:
            # 公开部门全集是固定的：出现全集外部门说明数据口径不一致，
            # 静默丢弃会让公开合计错误，因此拒绝发布而不是猜测。
            raise HTTPException(422, f'月份 {month} 含公开部门全集之外的部门：{department}')
        _, kg = emissions(source['activity'], source['quantity'], source['unit'], factors)
        if kg < 0:
            kg = Decimal(0)
        if kg > bound:
            kg = bound
        totals[department] += kg
    return totals, matched


def build_payload(snapshot: dict, month: str, epsilon: float, label: str = '') -> dict:
    """根据冻结快照生成一次发布的可公开载荷（含一次性采样的噪声）。"""
    if not department_universe():
        raise HTTPException(500, '隐私发布未配置公开部门全集')
    totals, matched = _department_totals(snapshot, month)
    if matched == 0:
        raise HTTPException(422, '该月份在冻结快照中没有底稿，无法发布')
    bound = clip_bound()
    scale = bound / epsilon
    departments = []
    for name in department_universe():
        noisy = float(totals[name]) + sample_laplace(scale)
        departments.append({'department': name, 'noisy_kg': round(noisy, 3)})
    noisy_total = round(sum(cell['noisy_kg'] for cell in departments), 3)
    return {
        'month': month,
        'label': label,
        'epsilon': epsilon,
        'laplace_scale': scale,
        'clip_upper_bound_kg': bound,
        'department_universe': department_universe(),
        'departments': departments,
        'noisy_total_kg': noisy_total,
        'method': (
            '冻结快照按公开固定部门全集聚合；每条记录排放先截断到公开上限 '
            f'{bound:g} kgCO2e，再对各部门合计独立加尺度 B/ε 的拉普拉斯噪声。'
            '部门记录互不相交（并行组合），整份发布满足 ε-差分隐私。'
        ),
        'note': '结果为加噪后的公开发布值，可能为负数；为保持无偏不做截断。',
        'generated': time.time(),
    }


def _ensure_budget(db, month: str, total: float) -> None:
    """保证该月预算行存在。第一条语句即写入，用于尽早取得写锁。"""
    dialect = db.get_bind().dialect.name
    values = {'month_key': month, 'budget_total': total, 'budget_spent': 0.0, 'updated': time.time()}
    if dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
        stmt = dialect_insert(PrivacyBudget).values(**values).on_conflict_do_nothing(index_elements=['month_key'])
        db.execute(stmt)
    elif dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
        stmt = dialect_insert(PrivacyBudget).values(**values).on_conflict_do_nothing(index_elements=['month_key'])
        db.execute(stmt)
    else:  # pragma: no cover - 其他方言走唯一约束兜底
        if not db.query(PrivacyBudget).filter_by(month_key=month).first():
            db.add(PrivacyBudget(**values))
            db.flush()


def _charge(db, month: str, epsilon: float) -> bool:
    """原子条件扣额：只有剩余额度足够时才成功，避免并发超支。"""
    stmt = (
        update(PrivacyBudget)
        .where(
            PrivacyBudget.month_key == month,
            PrivacyBudget.budget_spent + epsilon <= PrivacyBudget.budget_total,
        )
        .values(budget_spent=PrivacyBudget.budget_spent + epsilon, updated=time.time())
    )
    return db.execute(stmt).rowcount == 1


def publish(db, user, run_id: int, month: str, epsilon=None, label: str = '', crash_stage: str | None = None):
    """发布一次月度部门排放。

    返回 ``(publication, created)``。``crash_stage`` 仅供故障注入测试使用：
    ``after_charge`` 在扣额后、提交前抛错；``after_commit`` 在提交后抛错，
    模拟响应丢失。
    """
    month = (month or '').strip()
    if not month:
        raise HTTPException(422, '必须指定发布月份')
    epsilon = _validate_epsilon(default_epsilon() if epsilon is None else epsilon)
    total = monthly_budget()
    if epsilon > total:
        raise HTTPException(409, '单次发布 ε 超过该月总预算')
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, '报告不存在')
    if run.status != 'completed':
        raise HTTPException(409, '仅可发布已完成的冻结报告')

    key = idempotency_key(run_id, month, epsilon)
    existing = db.query(PrivacyPublication).filter_by(idempotency_key=key).first()
    if existing:
        return existing, False

    payload = build_payload(run.snapshot, month, epsilon, label)

    # 结束前面的只读事务，使下面的 INSERT 成为新写事务的首条语句——
    # 这既让 SQLite 立即取得写锁，也避免读事务升级写事务时的快照冲突。
    db.commit()

    last_error = None
    for attempt in range(8):
        try:
            _ensure_budget(db, month, total)
            if not _charge(db, month, epsilon):
                db.rollback()
                # 额度不足也可能只是并发下同一发布被抢先提交并耗尽了本月额度：
                # 此时必须返回原结果而不是报预算不足。
                winner = db.query(PrivacyPublication).filter_by(idempotency_key=key).first()
                if winner:
                    return winner, False
                raise HTTPException(409, '该月隐私预算不足，无法再次发布')
            if crash_stage == 'after_charge':
                raise RuntimeError('注入故障：扣额后、提交前崩溃')
            publication = PrivacyPublication(
                idempotency_key=key,
                month_key=month,
                run_id=run_id,
                epsilon=epsilon,
                laplace_scale=payload['laplace_scale'],
                status='published',
                payload=payload,
                created=time.time(),
            )
            db.add(publication)
            db.commit()
            if crash_stage == 'after_commit':
                raise RuntimeError('注入故障：提交后、响应前崩溃')
            return publication, True
        except HTTPException:
            raise
        except IntegrityError as exc:
            # 并发下同一发布被另一个请求抢先提交：本事务（含扣额）整体回滚，返回原结果。
            db.rollback()
            winner = db.query(PrivacyPublication).filter_by(idempotency_key=key).first()
            if winner:
                return winner, False
            last_error = exc
        except OperationalError as exc:  # SQLite/Postgres 写锁竞争，退避重试
            db.rollback()
            last_error = exc
            time.sleep(0.02 * (attempt + 1))
        except Exception:
            # 任何其他异常（含故障注入）：显式回滚，保证扣额与发布同成同败。
            db.rollback()
            raise
    raise HTTPException(503, '发布事务繁忙，请稍后重试') from last_error


def revoke(db, user, publication_id: int):
    publication = db.get(PrivacyPublication, publication_id)
    if not publication:
        raise HTTPException(404, '发布不存在')
    if publication.status != 'revoked':
        publication.status = 'revoked'
        publication.revoked_at = time.time()
        publication.revoked_by = user.email
        db.commit()
    # 撤回不触碰 PrivacyBudget：已消耗的预算不退回。
    return publication


def month_budgets(db) -> list[dict]:
    return [
        {
            'month': b.month_key,
            'budget_total': b.budget_total,
            'budget_spent': b.budget_spent,
            'remaining': round(b.budget_total - b.budget_spent, 12),
        }
        for b in db.query(PrivacyBudget).order_by(PrivacyBudget.month_key).all()
    ]


def public_view(publication: PrivacyPublication) -> dict:
    payload = publication.payload
    return {
        'id': publication.id,
        'month': payload['month'],
        'label': payload.get('label', ''),
        'status': publication.status,
        'epsilon': publication.epsilon,
        'laplace_scale': publication.laplace_scale,
        'clip_upper_bound_kg': payload['clip_upper_bound_kg'],
        'departments': payload['departments'],
        'noisy_total_kg': payload['noisy_total_kg'],
        'method': payload['method'],
        'note': payload['note'],
        'created': publication.created,
        'revoked_at': publication.revoked_at,
    }


def internal_view(publication: PrivacyPublication) -> dict:
    view = public_view(publication)
    view['run_id'] = publication.run_id
    return view


def consistency(db) -> dict:
    """校验「已扣预算 = 历史发布 ε 之和」。事务保证下二者应始终一致。"""
    publications = db.query(PrivacyPublication).all()
    budgets = {b.month_key: b for b in db.query(PrivacyBudget).all()}
    expected: dict[str, float] = {}
    for publication in publications:
        expected[publication.month_key] = expected.get(publication.month_key, 0.0) + publication.epsilon
    months = sorted(set(expected) | set(budgets))
    details = []
    ok = True
    for month in months:
        spent = budgets[month].budget_spent if month in budgets else 0.0
        want = round(expected.get(month, 0.0), 12)
        match = abs(spent - want) < 1e-9
        ok = ok and match
        details.append({'month': month, 'budget_spent': spent, 'sum_publication_epsilon': want, 'consistent': match})
    return {'consistent': ok, 'months': details}
