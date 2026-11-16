import decimal
import unittest
from policy_sandbox.appraisal.engine import PolicyEngine

class DomainTests(unittest.TestCase):
    def setUp(self): self.engine=PolicyEngine()

    def person(self,**changes):
        value={'id':'person-1','facts':{'age':25,'degree':'master','employed':True}};value.update(changes);return value
    def test_eligible(self):self.assertTrue(self.engine.evaluate(self.person(),{'expression':"age >= 18 and degree in ['master','doctor']",'benefit':'1000'}).accepted)
    def test_ineligible(self):self.assertFalse(self.engine.evaluate(self.person(),{'expression':'age < 18'}).accepted)
    def test_missing_fact(self):self.assertIn('missing_fact:income',self.engine.evaluate(self.person(),{'expression':'income < 1000'}).reasons)
    def test_unsafe_expression(self):self.assertIn('unsafe_policy_expression',self.engine.evaluate(self.person(),{'expression':'__import__("os")'}).reasons)
    def test_benefit_value(self):self.assertEqual(self.engine.evaluate(self.person(),{'expression':'employed','benefit':'250'}).value,decimal.Decimal('250'))
    def test_compare_policy(self):self.assertEqual(self.engine.compare(self.engine.rank([self.person()],{'expression':'age >= 18'}),self.engine.rank([self.person()],{'expression':'age < 18'}))['change_count'],1)

