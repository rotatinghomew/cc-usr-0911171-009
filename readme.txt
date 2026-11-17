青年人才安居就业政策资格模拟与差异解释器
测试：python3 -m unittest discover -s tests -v
构建：python3 -m compileall -q policy_sandbox tests
验收：python3 -m policy_sandbox --database /tmp/policy_sandbox.sqlite3 run --fixture fixtures/acceptance.json --output /tmp/policy_sandbox-output.json
单测：python3 -m unittest tests.test_domain -v

成对政策分析（只读，不写入数据库、不改变当前审批结果）：
python3 -m policy_sandbox --database /tmp/pair_sandbox.sqlite3 pair-analysis --fixture fixtures/pair_analysis.json --output /tmp/pair-output.json
单测：python3 -m unittest tests.test_pair_analysis_evaluator tests.test_pair_analysis_analyzer tests.test_pair_analysis_service tests.test_pair_analysis_conservation -v

成对分析在同一事实快照（snapshot_fingerprint 固定）上分别按旧版、新版生效规则解释：
- movements：资格转入 gained / 转出 lost / 保持合格 / 保持不合格 / unknown / invalid 六类，人员互斥且全覆盖
- condition_contributions：按条件（clause）列出各自的转入、转出、unknown 人群与金额变化
- budget：两版各自的合格人数、所需资金、预算缺口；unknown 与 invalid 不进入分母也不计金额
- person_comparisons / persons：保留每人逐条事实值与 fact_sources 可解释来源
- reconciliation：人数与金额可从个人明细重新汇总并校验守恒
缺字段返回 unknown，绝不并入不合格或分母；带单位数值经 UnitRegistry 校验，单位不一致判 invalid，不直接比较。
