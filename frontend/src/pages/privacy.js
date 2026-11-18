import {heading} from '../components/common/header.js';
import {esc,fmt,time} from '../utils/format.js';

function publicationCard(p){
  const cells=p.departments.map(c=>`<tr><td>${esc(c.department)}</td><td>${fmt(c.noisy_kg)}</td></tr>`).join('');
  return `<article class="publication ${esc(p.status)}">
    <header><div><p class="eyebrow">PUBLIC RELEASE / ${esc(p.month)}</p><h3>${esc(p.label||p.month+' 部门排放')}</h3></div><span class="status">${p.status==='published'?'已公开':'已撤回'}</span></header>
    <table><thead><tr><th>部门（公开全集）</th><th>排放 kgCO₂e（加噪公开值）</th></tr></thead><tbody>${cells}
      <tr><td><b>合计</b></td><td><b>${fmt(p.noisy_total_kg)}</b></td></tr></tbody></table>
    <p class="method">${esc(p.method)}</p>
    <p class="privacy-meta">ε=${fmt(p.epsilon)} · 噪声尺度 B/ε=${fmt(p.laplace_scale)} · 截断上限 ${fmt(p.clip_upper_bound_kg)} kg · 发布 ${time(p.created)}</p>
    <p class="warning">${esc(p.note)}</p>
    <div class="report-actions"><button class="privacy-export" data-id="${p.id}">下载公开 CSV</button>${p.revoke?`<button class="privacy-revoke" data-id="${p.id}">撤回公开</button>`:''}</div>
  </article>`;
}

export function privacy(c,d,selected,session){
  const role=session?.role;
  const pubs=(d.privacy?.publications||[]);
  const canPublish=role==='admin'||role==='analyst';
  const canRevoke=role==='admin';
  const runs=(d.runs||[]).filter(r=>r.status==='completed');
  const months=[...new Set([...(d.periods||[]).map(p=>p.name),...(d.runs||[]).flatMap(r=>(r.snapshot?.sources||[]).map(s=>s.period))])].filter(Boolean).sort();
  const form=canPublish?`<form id="privacy-form" class="inline-form">
    <label>冻结报告<select name="run_id">${runs.map(r=>`<option value="${r.id}">${esc(r.name)}（#${r.id}）</option>`).join('')||'<option value="">暂无已完成报告</option>'}</select></label>
    <label>发布月份<select name="month">${months.map(m=>`<option>${esc(m)}</option>`).join('')||'<option value="">暂无月份</option>'}</select></label>
    <label>隐私预算 ε（本月共享）<input name="epsilon" type="number" step="any" min="0.000001" value="${esc(c.privacy?.default_epsilon??0.25)}"></label>
    <label>公开标题<input name="label" value="月度部门排放"></label>
    <button ${runs.length?'':'disabled'}>发布加噪结果</button>
  </form>`:'';
  const intro=canPublish
    ?'按公开固定部门全集汇总冻结报告，每条排放截断到公开上限 B 后，各部门独立加尺度 B/ε 的拉普拉斯噪声。同月共用总 ε 预算；同一发布重试返回原噪声且不重复扣额。'
    :'此处仅展示已公开的加噪部门排放结果。原始底稿、报告快照与交付入口不在受限阅读范围内。';
  const budgets=(d.privacy?.budgets||[]);
  const budgetLine=canPublish&&budgets.length?`<p class="privacy-meta">本月预算：${budgets.map(b=>`${esc(b.month)} 已用 ${fmt(b.budget_spent)}/${fmt(b.budget_total)}（余 ${fmt(b.remaining)}）`).join(' · ')}</p>`:'';
  const body=pubs.length?pubs.map(p=>publicationCard({...p,revoke:canRevoke})).join(''):'<p class="empty">暂无已公开结果。</p>';
  return heading('PRIVACY RELEASE / 07','月度部门排放隐私发布',intro)+form+budgetLine+`<div class="publication-list">${body}</div>`;
}
