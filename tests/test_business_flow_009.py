import unittest
from policy_sandbox.rule_language.policy_runtime import PolicyRuntime
class PolicyRuntimeTests(unittest.TestCase):
 def setUp(self):self.r=PolicyRuntime()
 def test_eligible(self):self.assertEqual(self.r.evaluate("age >= 18 and degree in ['master','doctor']",{'age':25,'degree':'master'})['result'],'eligible')
 def test_unknown_fact(self):self.assertEqual(self.r.evaluate('income < 1000',{})['result'],'unknown')
 def test_unsafe(self):
  with self.assertRaisesRegex(ValueError,'unsafe_policy_expression'):self.r.evaluate('__import__("os")',{})
 def test_version_compare(self):self.assertEqual(self.r.compare('age >= 18','age >= 30',[{'id':'p','facts':{'age':25}}])[0]['to'],'ineligible')
 def test_budget_shortfall(self):self.assertEqual(self.r.budget('age >= 18',[{'id':'a','facts':{'age':20}},{'id':'b','facts':{'age':30}}],600,1000)['shortfall'],'200')
 def test_unit_mismatch(self):self.assertEqual(self.r.validate_units([('income','distance')],{'income':'CNY','distance':'km'}),['unit_mismatch:income:distance'])
