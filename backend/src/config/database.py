from sqlalchemy import create_engine,event
from sqlalchemy.orm import sessionmaker,DeclarativeBase
from src.config.settings import DATABASE_URL
class Base(DeclarativeBase): pass
_connect_args={'check_same_thread':False} if DATABASE_URL.startswith('sqlite') else {}
engine=create_engine(DATABASE_URL,pool_pre_ping=True,connect_args=_connect_args)
if DATABASE_URL.startswith('sqlite'):
 # 本机测试使用文件型 SQLite：WAL + busy_timeout 让并发发布按写锁串行化，而不是立即报锁错。
 @event.listens_for(engine,'connect')
 def _sqlite_pragmas(conn,_record):
  cur=conn.cursor();cur.execute('PRAGMA journal_mode=WAL');cur.execute('PRAGMA busy_timeout=30000');cur.close()
Session=sessionmaker(bind=engine,expire_on_commit=False)
def database():
 with Session() as db: yield db
