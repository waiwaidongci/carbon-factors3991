# 002 隐私发布

新增两张表，由 SQLAlchemy 启动自动建表；已有库升级前请先备份。

## privacy_budgets（按月的共享隐私总预算）

| 列 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| month_key | varchar(40) UNIQUE | 发布月份，如 `2026-11` |
| budget_total | float | 该月总 ε 预算（公开配置 `privacy.monthly_epsilon`） |
| budget_spent | float | 已消耗 ε，只增不减；撤回不退款 |
| updated | float | 最近更新时间戳 |

`month_key` 唯一约束是并发不超支的基础：`budget_spent + ε <= budget_total` 的条件
UPDATE 在行锁内串行执行，余额不足时整笔事务回滚。

## privacy_publications（一次对外发布）

| 列 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| idempotency_key | varchar(220) UNIQUE | `sha256(run_id\|month\|ε)`，保证同一发布只扣一次额 |
| month_key | varchar(40) INDEX | 归属月份 |
| run_id | int | 产生该发布的冻结报告（内部审计用，不对外暴露） |
| epsilon | float | 本次消耗的 ε |
| laplace_scale | float | B/ε |
| status | varchar(20) | `published` / `revoked` |
| payload | JSON | 已加噪的公开载荷（含一次采样的噪声，重试直接复用） |
| created / revoked_at / revoked_by | | 时间与撤回人 |

`idempotency_key` 唯一约束保证并发重复请求只有一个事务能提交；落败事务（连同
其扣额）整体回滚，随后读出已提交的同一行返回原始噪声。

两个写入（扣预算 + 插发布）在同一事务内提交：崩溃时要么都已落库，要么都没有。
