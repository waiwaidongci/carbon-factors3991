import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
os.environ['CONFIG_PATH'] = str(ROOT / 'project.json')
os.environ['DATABASE_URL'] = 'sqlite:///' + str(pathlib.Path(tempfile.mkdtemp()) / 'test.db')
os.environ['JWT_SECRET'] = 'test-secret'
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
os.environ['SEED_DEMO'] = 'false'
os.environ['PRIVACY_CLIP_BOUND'] = '1000'
os.environ['PRIVACY_MONTHLY_EPSILON'] = '1.0'
os.environ['PRIVACY_EPSILON'] = '0.25'

import pytest
from fastapi.testclient import TestClient

from src.config.database import Base, engine, Session
from src.main import app
from src.models.identity import User
from src.models.source import Policy
from src.models.run import Run
from src.utils.security import password_hash, token


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_user(role: str) -> User:
    with Session() as db:
        user = User(email=f'{role}@test.local', role=role, password=password_hash('pw'))
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def auth(role: str) -> dict:
    user = make_user(role)
    return {'Authorization': 'Bearer ' + token(user)}


POLICY = {
    'version': '因子-v1',
    'scope': '合成测试口径',
    'factors': {
        '电力': {'unit': 'kWh', 'kg_per_unit': 0.57},
        '柴油': {'unit': 'L', 'kg_per_unit': 2.68},
        '通勤': {'unit': 'km', 'kg_per_unit': 0.12},
    },
}


def make_run(period='2026-11', approved=True, status='completed', sources=None) -> int:
    if sources is None:
        sources = [
            {'period': period, 'department': '材料试验室', 'activity': '电力', 'quantity': 100, 'unit': 'kWh'},
            {'period': period, 'department': '工艺试验室', 'activity': '柴油', 'quantity': 10, 'unit': 'L'},
        ]
    snapshot = {'policy': POLICY, 'sources': sources, 'period': period}
    with Session() as db:
        obj = Run(name='冻结报告', status=status, snapshot=snapshot, checksum='x', result={'rows': []}, approved=approved)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj.id
