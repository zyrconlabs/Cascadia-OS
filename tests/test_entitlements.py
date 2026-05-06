"""tests/test_entitlements.py — Canonical entitlements vocabulary tests."""
from __future__ import annotations

import unittest

from cascadia.shared.entitlements import (
    get_risk_level,
    list_capabilities,
    requires_approval,
    requires_sentinel,
    validate_capability,
)


class TestGetRiskLevelExactMatch(unittest.TestCase):

    def test_email_send_medium(self):
        self.assertEqual(get_risk_level('email.send'), 'medium')

    def test_shell_exec_critical(self):
        self.assertEqual(get_risk_level('shell.exec'), 'critical')

    def test_file_read_low(self):
        self.assertEqual(get_risk_level('file.read'), 'low')

    def test_billing_write_high(self):
        self.assertEqual(get_risk_level('billing.write'), 'high')


class TestGetRiskLevelVerbFallback(unittest.TestCase):

    def test_salesforce_delete_record_high(self):
        # "salesforce" not in registry; verb_root "delete" → high
        self.assertEqual(get_risk_level('salesforce.delete_record'), 'high')

    def test_hubspot_create_contact_medium(self):
        # verb_root "create" → medium
        self.assertEqual(get_risk_level('hubspot.create_contact'), 'medium')

    def test_gdrive_list_files_low(self):
        # verb_root "list" → low
        self.assertEqual(get_risk_level('gdrive.list_files'), 'low')

    def test_custom_exec_script_critical(self):
        # verb_root "exec" → critical
        self.assertEqual(get_risk_level('custom.exec_script'), 'critical')


class TestGetRiskLevelDefault(unittest.TestCase):

    def test_totally_unknown_medium(self):
        # No registry match, no verb match → safe default medium
        self.assertEqual(get_risk_level('totally.unknown.thing'), 'medium')


class TestRequiresApproval(unittest.TestCase):

    def test_shell_exec_requires_approval(self):
        self.assertTrue(requires_approval('shell.exec'))

    def test_billing_write_requires_approval(self):
        self.assertTrue(requires_approval('billing.write'))

    def test_email_send_no_approval(self):
        self.assertFalse(requires_approval('email.send'))

    def test_file_read_no_approval(self):
        self.assertFalse(requires_approval('file.read'))


class TestRequiresSentinel(unittest.TestCase):

    def test_payment_create_sentinel(self):
        self.assertTrue(requires_sentinel('payment.create'))

    def test_vault_write_sentinel(self):
        self.assertTrue(requires_sentinel('vault.write'))

    def test_crm_read_no_sentinel(self):
        self.assertFalse(requires_sentinel('crm.read'))


class TestValidateCapability(unittest.TestCase):

    def test_email_send_valid(self):
        self.assertTrue(validate_capability('email.send'))

    def test_wildcard_segment_valid(self):
        self.assertTrue(validate_capability('crm.*.read'))

    def test_file_write_valid(self):
        self.assertTrue(validate_capability('file.write'))

    def test_uppercase_invalid(self):
        self.assertFalse(validate_capability('UPPERCASE.action'))

    def test_no_dots_invalid(self):
        self.assertFalse(validate_capability('no-dots'))

    def test_empty_invalid(self):
        self.assertFalse(validate_capability(''))


class TestListCapabilities(unittest.TestCase):

    def test_list_all_nonempty(self):
        caps = list_capabilities()
        self.assertIsInstance(caps, list)
        self.assertGreater(len(caps), 0)

    def test_list_critical_contains_shell_exec(self):
        self.assertIn('shell.exec', list_capabilities('critical'))

    def test_list_low_contains_file_read(self):
        self.assertIn('file.read', list_capabilities('low'))


if __name__ == '__main__':
    unittest.main()
