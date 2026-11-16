青年人才安居就业政策资格模拟与差异解释器
测试：python3 -m unittest discover -s tests -v
构建：python3 -m compileall -q policy_sandbox tests
验收：python3 -m policy_sandbox --database /tmp/policy_sandbox.sqlite3 run --fixture fixtures/acceptance.json --output /tmp/policy_sandbox-output.json
单测：python3 -m unittest tests.test_domain -v
