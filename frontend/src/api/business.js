import {request} from './client.js';
const INTERNAL_KEYS=['sources','periods','policies','runs','schedules','deliveries','audits'];
// 受限阅读者不请求原始底稿/报告/交付等内部入口，只取公开隐私发布结果。
export const loadAll=async role=>{
  const keys=role==='viewer'?[]:INTERNAL_KEYS;
  const extras=role==='viewer'?[]:['privacy/admin/budgets'];
  const [publications,...rest]=await Promise.all([request('/privacy/publications'),...keys.map(k=>request('/'+k)),...extras.map(k=>request('/'+k))]);
  const data=Object.fromEntries(keys.map((k,i)=>[k,rest[i]]));
  const budgets=extras.length?rest[keys.length]:[];
  return {sources:[],periods:[],policies:[],runs:[],schedules:[],deliveries:[],audits:[],...data,privacy:{publications,budgets}};
};
export const saveSource=(data,id)=>request('/sources'+(id?'/'+id:''),id?'PUT':'POST',{data});
export const savePolicy=data=>request('/policies','POST',{data});
export const submitRun=data=>request('/runs','POST',data);
export const approveRun=(id,note)=>request('/runs/'+id+'/approve','POST',{note});
export const publishPrivacy=data=>request('/privacy/publications','POST',data);
export const revokePrivacy=id=>request('/privacy/publications/'+id+'/revoke','POST',{});
