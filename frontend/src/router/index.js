export const ALL_PAGES=[['overview','业务总览'],['sources','业务底稿'],['policies','口径版本'],['runs','核算与复核'],['deliveries','定时交付'],['audit','过程留痕'],['privacy','隐私发布']];
// 受限阅读者（viewer）只能看到公开的隐私发布页；页面入口本身也不暴露内部数据入口。
export const pagesFor=role=>role==='viewer'?[['privacy','隐私发布']]:ALL_PAGES;
export const currentPage=()=>location.hash.slice(1)||'overview';
