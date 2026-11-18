import os,json
from pathlib import Path
CONFIG=json.loads(Path(os.getenv('CONFIG_PATH','/app/project.json')).read_text())
DATABASE_URL=os.environ['DATABASE_URL']
REDIS_URL=os.getenv('REDIS_URL','redis://redis:6379/0')
SECRET=os.environ['JWT_SECRET']
# 隐私发布参数全部来自公开固定配置；环境变量仅用于本地测试与部署覆盖。
PRIVACY=dict(CONFIG.get('privacy') or {})
PRIVACY['clip_upper_bound_kg']=float(os.getenv('PRIVACY_CLIP_BOUND',PRIVACY.get('clip_upper_bound_kg',1000.0)))
PRIVACY['monthly_epsilon']=float(os.getenv('PRIVACY_MONTHLY_EPSILON',PRIVACY.get('monthly_epsilon',1.0)))
PRIVACY['epsilon_per_publication']=float(os.getenv('PRIVACY_EPSILON',PRIVACY.get('default_epsilon',0.25)))
