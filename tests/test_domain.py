import unittest
from workbench.domain import *

class DomainBoundaryTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertTrue(in_range('PSA0100','PSA',100,200))
        self.assertTrue(in_range('PSA0200','PSA',100,200))
        self.assertFalse(in_range('PSA0201','PSA',100,200))
        self.assertFalse(in_range('SN0100','PSA',100,200))
        self.assertEqual(history([{'id':'b','date':'2026-01-01'},{'id':'a','date':'2026-01-01'}],'2026-01-01')[0]['id'],'a')
