# 棠原试验室碳因子结算册

合成用电/燃料/出行活动→因子核算与差异说明→站内周期结算；范围声明与缺因子拒绝

## 业务场景与工作流
模拟客户：棠原研究协作组，12位业务与分析人员。每月研究明细先从`carbon-factors-202611.csv`录入底稿，经口径冻结、Python核算、负责人复核后向业务阅读者交付站内CSV。原有流程依靠人工表格抄算，痛点为口径版本难追溯、重复计算易错、复核与交付脱节。

模块：业务总览、业务底稿、新建口径版本、异步核算与复核、定时交付、站内收件箱、审计、隐私发布。底稿可新增/修改/删除；口径仅新增版本；报告提交时冻结来源与版本并保存SHA256。定时交付引用已复核报告快照，worker到期写入数据库，业务阅读者下载。

## 隐私发布（月度部门排放）
外部协作方需要月度部门排放，但不能接触原底稿。`隐私发布`页按**公开且固定的部门全集**汇总冻结报告快照，每条排放先截断到公开上限 B，再对**每个部门独立**加尺度 `B/ε` 的拉普拉斯噪声；不同部门的记录集合互不相交，故整份发布满足 ε-差分隐私。

- 冻结：只读已完成的 `Run.snapshot`，底稿后续修改不影响已冻结报告。
- 截断：单条记录影响部门合计不超过 B，故部门合计敏感度为 B。
- 预算：同一月份的所有报告版本共用一个月度总 ε，`budget_spent` 只增不减。
- 幂等：幂等键 `sha256(run_id|month|ε)` 唯一；同一发布重试**返回原噪声**、不重复扣额；不同发布（不同报告/月份/ε）各自累计。
- 并发：`budget_spent + ε <= budget_total` 的条件更新在行锁内串行，余额不足整笔回滚，绝不超支。
- 崩溃可恢复：扣额与发布在同一事务提交；崩溃后重试若已提交则返回原结果，未提交则无任何部分扣额。
- 同成同败：结果与扣额要么都可见，要么都不存在。
- 撤回：置 `revoked` 后从公开列表隐藏，但**已扣预算不退回**；同一发布键重试仍返回原记录、不再扣额。
- 脱敏：对外只返回加噪公开值和参数（B、ε、噪声尺度）；不返回原值、快照、检查或 `run_id`。受限阅读者（viewer）在页面上只能看到已公开结果，`/sources`、`/runs`、`/deliveries`、`/audits`、内部映射与预算接口一律 403。

参数来自公开配置 `project.json` 的 `privacy` 字段（`department_universe` / `clip_upper_bound_kg` / `monthly_epsilon` / `default_epsilon`），部署可用环境变量 `PRIVACY_CLIP_BOUND`、`PRIVACY_MONTHLY_EPSILON`、`PRIVACY_EPSILON` 覆盖。实现在 `backend/src/services/privacy.py`，路由在 `routes/privacy.py`，表结构见 `migrations/002_privacy.md`。

## 算法口径
单位转换、因子版本冻结、用量×因子与部门透视。kWh/MWh单位换算手算；缺单位因子拒绝；因子更新旧报告不变。

活动量 × 因子 · 单位与范围随报告保存。输入：业务底稿与口径JSON；输出：summary、透视rows、method、warnings。算法实现在`backend/src/services/calculation.py`，输入校验在`validation.py`。固定seed=42，240条合成数据；未对接真实业务系统。修改源数据和新建因子/口径不会改变已提交报告。

## 一天的验收剧本
1. 09:00 admin查看240条seed底稿与具名业务期间。
2. 09:10 analyst添加`carbon-factors-202611.csv`的一条具名业务记录。
3. 09:20 admin在口径版本页登记新版本；旧版本保留。
4. 09:30 analyst命名“十一月核算”并选择期间/版本排队。
5. 09:31 Redis worker真实执行Python计算，展示汇总/透视与口径边界。
6. 09:40 admin核对数值后填写至少4字复核说明。
7. 09:45 admin建立60秒周期计划，worker到期生成站内持久化交付。
8. 09:46 viewer在站内收件箱下载交付CSV；审计页查看操作者与时间。
9. 09:50 analyst在`隐私发布`页对已完成报告选定月份发布，指定 ε；页面展示公开部门全集与加噪结果。
10. 09:51 重新提交同一发布返回原噪声且不重复扣额；同月再发不同版本继续累计，直至月度总 ε 用尽后返回 409。
11. 09:52 viewer登录仅能看到`隐私发布`页的公开结果；原始底稿、报告与交付入口均不可见。

## 快速启动
```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
前端 http://localhost:18254 ，后端 http://localhost:19254/docs ，数据库宿主端口17754。中文父目录可用。恰好5服务frontend/backend/worker/db/redis；均有真实healthcheck。停止并清空演示数据：`docker compose down -v --remove-orphans`。

|角色|邮箱|密码|权限|
|---|---|---|---|
|admin|admin@demo.local|Demo2026!|全部底稿、口径版本、核算、复核、调度、发布与撤回隐私结果|
|analyst|analyst@demo.local|Demo2026!|底稿增改删、核算、导出、发布隐私结果；无复核/调度/口径写权限|
|viewer|viewer@demo.local|Demo2026!|受限阅读者；仅可查看与下载已公开的隐私发布结果|

## 工程结构与开发
Python3.11/FastAPI/SQLAlchemy/Redis，PostgreSQL16；前端原生HTML/CSS/JS ES modules，Nginx代理`/api/`剥前缀至后端。`frontend/src`按api/stores/pages/components/hooks/utils/router/constants/types拆分；`backend/src`按models/schemas/services/controllers/routes/middlewares/config/utils/constants拆分；`backend/scripts/seed_demo.py`自然键幂等且不覆盖用户修改。SQLAlchemy启动创建初始结构，迁移说明在backend/migrations。

后端开发：在backend安装requirements，设置DATABASE_URL/REDIS_URL/JWT_SECRET，`uvicorn src.main:app --reload`；前端可用Nginx或带同等代理的静态开发服务器。前后端全部业务计算在Python。

测试：`pip install -r requirements-dev.txt`，在`backend`下运行 `pytest -q`（隐私发布：`pytest tests/test_privacy.py`）；真实 HTTP 并发压测：`python3 tests/stress_privacy.py`。测试默认用临时 SQLite，不需要 docker/Postgres。

环境变量：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、JWT_SECRET（部署替换）、DATABASE_URL、REDIS_URL、SEED_DEMO，以及可选的隐私参数 PRIVACY_CLIP_BOUND / PRIVACY_MONTHLY_EPSILON / PRIVACY_EPSILON。`.env`不入Git。数据库/Redis/共享产物为命名卷。共享角色枚举admin/analyst/viewer与状态queued/running/completed/failed分别定义于backend/src/constants/enums.py和frontend/src/constants/enums.js。两个中间件：统一可恢复校验错误、每IP每分钟360请求限流。

## 已知边界
定时分发为站内收件箱；一名worker轮询执行，无外部邮件。调度发送已复核固定报告快照，不自动重算新数据。时间显示Asia/Shanghai。报告导出适合小型数据；底稿全量加载240行演示数据。Google Fonts不可用时回退serif。后端API以专属字段对象收发，页面通过业务输入控件编辑，不要求用户写JSON。新增来源期间自动可选；专属字段和算法严格校验。JWT演示有效期2小时。

MIT License。

## 实际实体映射
来源记录（Source，专属业务字段校验）、口径版本（Policy）、核算报告（Run）、交付计划（Schedule）、交付（Delivery）均经数据库→模型→请求schema→service→route/controller→frontend API→store→page。Period为已有来源期间字典，在任务期间选择器使用；不单独作为复杂度计数。底稿JSON为小型多字段单记录存储，不虚构未落库的子实体。

实际表：期间字典 activity_periods；专属来源 activities；冻结口径 emission_factors；异步报告 carbon_runs；隐私发布 privacy_publications；月度隐私预算 privacy_budgets。隐私发布（PrivacyPublication、PrivacyBudget）同经数据库→模型→service（`services/privacy.py`）→route→frontend API→store→page，只从冻结快照聚合，不引入额外虚构实体。
