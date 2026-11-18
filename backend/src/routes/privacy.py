from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import Response
from src.config.database import database
from src.controllers.auth import current,admin,editor,unrestricted
from src.schemas.requests import PublishInput
from src.services import privacy
from src.services.audit import log
from src.services.export import privacy_csv
router=APIRouter()

@router.get('/privacy/publications')
def publications(db=Depends(database),u=Depends(current)):
 """已公开的隐私发布结果；所有角色（含受限阅读者）可读，仅返回加噪公开值。"""
 return [privacy.public_view(p) for p in db.query(privacy.PrivacyPublication).filter_by(status='published').order_by(privacy.PrivacyPublication.id.desc()).all()]

@router.get('/privacy/publications/{id}')
def publication(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(privacy.PrivacyPublication,id)
 if not obj or obj.status!='published': raise HTTPException(404,'发布不存在或已撤回')
 return privacy.public_view(obj)

@router.get('/privacy/publications/{id}/csv')
def publication_csv(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(privacy.PrivacyPublication,id)
 if not obj or obj.status!='published': raise HTTPException(404,'发布不存在或已撤回')
 return Response(privacy_csv(obj.payload),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="privacy-{id}.csv"'})

@router.get('/privacy/admin/publications')
def admin_publications(db=Depends(database),u=Depends(admin)):
 """管理员专用：包含内部报告编号，供审计；受限阅读者不可访问。"""
 return [privacy.internal_view(p) for p in db.query(privacy.PrivacyPublication).order_by(privacy.PrivacyPublication.id.desc()).all()]

@router.get('/privacy/admin/budgets')
def budgets(db=Depends(database),u=Depends(unrestricted)):
 return privacy.month_budgets(db)

@router.get('/privacy/admin/consistency')
def consistency(db=Depends(database),u=Depends(unrestricted)):
 return privacy.consistency(db)

@router.post('/privacy/publications')
def publish(body:PublishInput,db=Depends(database),u=Depends(editor)):
 if u.role=='viewer': raise HTTPException(403,'受限阅读者不可发起发布')
 obj,created=privacy.publish(db,u,body.run_id,body.month,body.epsilon,body.label)
 if created: log(db,u.email,'privacy.publish',f'{body.run_id}:{body.month}:{obj.epsilon}');db.commit()
 return privacy.internal_view(obj)

@router.post('/privacy/publications/{id}/revoke')
def revoke(id:int,db=Depends(database),u=Depends(admin)):
 obj=privacy.revoke(db,u,id)
 log(db,u.email,'privacy.revoke',id);db.commit()
 return privacy.internal_view(obj)
