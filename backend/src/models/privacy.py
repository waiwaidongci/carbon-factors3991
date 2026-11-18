from sqlalchemy import Column,Integer,String,JSON,Float
from src.config.database import Base
class PrivacyBudget(Base):
 """按月共享的差分隐私总预算；budget_spent 只增不减，撤回公开也不退款。"""
 __tablename__='privacy_budgets'
 id=Column(Integer,primary_key=True)
 month_key=Column(String(40),unique=True,nullable=False)
 budget_total=Column(Float,nullable=False)
 budget_spent=Column(Float,nullable=False,default=0.0)
 updated=Column(Float)
class PrivacyPublication(Base):
 """一次对外发布。idempotency_key 唯一约束保证同一次发布不会重复扣额、并发重试只落一条。"""
 __tablename__='privacy_publications'
 id=Column(Integer,primary_key=True)
 idempotency_key=Column(String(220),unique=True,nullable=False)
 month_key=Column(String(40),nullable=False,index=True)
 run_id=Column(Integer,nullable=False)
 epsilon=Column(Float,nullable=False)
 laplace_scale=Column(Float,nullable=False)
 status=Column(String(20),nullable=False,default='published')
 payload=Column(JSON,nullable=False)
 created=Column(Float)
 revoked_at=Column(Float)
 revoked_by=Column(String(120))
