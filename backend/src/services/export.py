import csv,io,json
from fastapi import HTTPException
def csv_text(result):
 rows=result.get('rows',[])
 if not rows: raise HTTPException(409,'尚无结果')
 stream=io.StringIO();keys=list(dict.fromkeys(k for row in rows for k in row));w=csv.DictWriter(stream,fieldnames=keys);w.writeheader()
 for row in rows: w.writerow({k:(json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v) for k,v in row.items()})
 return '\ufeff'+stream.getvalue()
def privacy_csv(payload):
 stream=io.StringIO();w=csv.writer(stream)
 w.writerow(['月份','部门','排放kgCO2e（加噪公开值）','噪声尺度','单条截断上限kgCO2e','ε'])
 for cell in payload['departments']:
  w.writerow([payload['month'],cell['department'],cell['noisy_kg'],payload['laplace_scale'],payload['clip_upper_bound_kg'],payload['epsilon']])
 w.writerow([payload['month'],'合计',payload['noisy_total_kg'],payload['laplace_scale'],payload['clip_upper_bound_kg'],payload['epsilon']])
 return '\ufeff'+stream.getvalue()
