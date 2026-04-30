from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from cascadia.billing.subscription_manager import SubscriptionManager


class SubscriptionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.mgr = SubscriptionManager(db_path=Path(self._tmp.name))

    def tearDown(self) -> None:
        import os
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_upsert_creates_new_customer(self) -> None:
        self.mgr.upsert_customer('cus_001', 'alice@test.com', 'pro')
        customer = self.mgr.get_customer('cus_001')
        self.assertIsNotNone(customer)
        self.assertEqual(customer['email'], 'alice@test.com')
        self.assertEqual(customer['tier'], 'pro')
        self.assertEqual(customer['status'], 'active')

    def test_upsert_updates_existing_customer(self) -> None:
        self.mgr.upsert_customer('cus_002', 'bob@test.com', 'pro')
        self.mgr.upsert_customer('cus_002', 'bob@test.com', 'business_growth')
        customer = self.mgr.get_customer('cus_002')
        self.assertEqual(customer['tier'], 'business_growth')
        self.assertIsNotNone(customer['renewed_at'])

    def test_get_tier_returns_lite_for_unknown(self) -> None:
        tier = self.mgr.get_tier('cus_unknown')
        self.assertEqual(tier, 'lite')

    def test_downgrade_to_lite_sets_cancelled(self) -> None:
        self.mgr.upsert_customer('cus_003', 'carol@test.com', 'business_starter')
        self.mgr.downgrade_to_lite('cus_003')
        customer = self.mgr.get_customer('cus_003')
        self.assertEqual(customer['tier'], 'lite')
        self.assertEqual(customer['status'], 'cancelled')
        self.assertIsNotNone(customer['cancelled_at'])

    def test_update_tier_changes_tier(self) -> None:
        self.mgr.upsert_customer('cus_004', 'dave@test.com', 'pro')
        self.mgr.update_tier('cus_004', 'enterprise')
        self.assertEqual(self.mgr.get_tier('cus_004'), 'enterprise')

    def test_get_stats_returns_correct_counts(self) -> None:
        self.mgr.upsert_customer('cus_a', 'a@test.com', 'pro')
        self.mgr.upsert_customer('cus_b', 'b@test.com', 'pro')
        self.mgr.upsert_customer('cus_c', 'c@test.com', 'enterprise')
        stats = self.mgr.get_stats()
        self.assertEqual(stats['total_active'], 3)
        self.assertEqual(stats['by_tier']['pro'], 2)
        self.assertEqual(stats['by_tier']['enterprise'], 1)

    def test_get_customer_by_email(self) -> None:
        self.mgr.upsert_customer('cus_005', 'eve@test.com', 'business_max')
        customer = self.mgr.get_customer_by_email('eve@test.com')
        self.assertIsNotNone(customer)
        self.assertEqual(customer['stripe_customer_id'], 'cus_005')

    def test_list_customers_filters_by_tier(self) -> None:
        self.mgr.upsert_customer('cus_x', 'x@test.com', 'pro')
        self.mgr.upsert_customer('cus_y', 'y@test.com', 'lite')
        self.mgr.upsert_customer('cus_z', 'z@test.com', 'pro')
        pro_customers = self.mgr.list_customers(tier='pro')
        self.assertEqual(len(pro_customers), 2)
        all_customers = self.mgr.list_customers()
        self.assertEqual(len(all_customers), 3)


if __name__ == '__main__':
    unittest.main()
