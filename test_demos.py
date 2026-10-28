import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from demos import Creator, Review, monthly, reconcile, rounding


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.f = json.loads(Path(__file__).with_name('fixtures.json').read_text(encoding='utf-8'))

    def test_month_fixed_across_transfer_boundary_and_retry(self):
        expected = {'basis_month': '2026-09', 'count': 2, 'total': 2000}
        for _ in range(2):
            self.assertEqual(monthly(self.f['transfers'], '2026-09'), expected)

    def test_month_invalid_and_duplicate(self):
        for value in ('2026-13', '2026-9', 'today'):
            with self.assertRaises(ValueError):
                monthly([], value)
        with self.assertRaises(ValueError):
            monthly(self.f['transfers'] * 2, '2026-09')

    def test_reconcile_classification_and_retry(self):
        result = reconcile(self.f['expected'], self.f['actual'])
        self.assertEqual(result[0]['issues'], ['category_mismatch', 'date_mismatch'])
        self.assertEqual(result[1]['issues'], ['missing_field:date'])
        self.assertEqual(result[2]['issues'], ['missing_record'])
        self.assertEqual(result, reconcile(self.f['expected'], self.f['actual']))

    def test_reconcile_matched_invalid_and_ambiguous(self):
        self.assertEqual(reconcile(self.f['expected'], self.f['expected'])[0]['issues'], ['matched'])
        bad = [dict(self.f['expected'][0], date='bad')]
        self.assertEqual(reconcile(self.f['expected'][:1], bad)[0]['issues'], ['invalid_date'])
        with self.assertRaises(ValueError):
            reconcile(self.f['expected'] * 2, [])

    def test_create_replay_restart_and_conflict(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = str(Path(folder) / 'test.sqlite3')
            service = Creator(path)
            first = service.create('key', {'amount': 100})
            service.close()
            service = Creator(path)
            self.assertEqual(service.create('key', {'amount': 100}), {'id': first['id'], 'replayed': True})
            with self.assertRaises(ValueError):
                service.create('key', {'amount': 101})
            with self.assertRaises(ValueError):
                service.create('', {})
            service.close()

    def test_create_rollback_then_retry(self):
        service = Creator(':memory:')
        try:
            with self.assertRaises(RuntimeError):
                service.create('key', {}, fail_before_commit=True)
            self.assertFalse(service.create('key', {})['replayed'])
            self.assertEqual(service.db.execute('SELECT COUNT(*) FROM records').fetchone()[0], 1)
        finally:
            service.close()

    def test_concurrent_double_click(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = str(Path(folder) / 'concurrent.sqlite3')
            Creator(path).close()
            def click(_):
                service = Creator(path)
                try:
                    return service.create('same-key', {'amount': 100})
                finally:
                    service.close()
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(click, range(8)))
            self.assertEqual(len({r['id'] for r in results}), 1)
            self.assertEqual(sum(not r['replayed'] for r in results), 1)

    def test_ocr_correct_approve_retry_and_invalidate(self):
        review = Review(self.f['ocr'])
        with self.assertRaises(ValueError):
            review.approve(0, 'fictional-person')
        review.correct('amount', 1200, 'fictional-person')
        with self.assertRaises(ValueError):
            review.approve(0, 'fictional-person')
        self.assertTrue(review.approve(1, 'fictional-person'))
        self.assertFalse(review.approve(1, 'fictional-person'))
        self.assertEqual(review.events[1]['before'], 'I200')
        review.correct('amount', 1300, 'fictional-person')
        self.assertFalse(review.approved)
        self.assertEqual(len(review.events), 4)

    def test_rounding_comparison_repeatable(self):
        self.assertEqual(rounding([105, 105, 105])['per_line'], 30)
        self.assertEqual(rounding([105, 105, 105])['per_invoice'], 31)
        self.assertEqual(rounding([105, 105, 105], mode='half_up')['difference'], -1)
        self.assertEqual(rounding([0]), rounding([0]))

    def test_rate_float_rejected_and_decimal_exact(self):
        for rate in (0.29, 1, True):
            with self.assertRaises(ValueError):
                rounding([100], rate=rate)
        for rate in ('0.29', Decimal('0.29')):
            self.assertEqual(rounding([100], rate=rate)['per_invoice'], 29)

    def test_ocr_public_copies_cannot_change_approved_state(self):
        source = dict(self.f['ocr'], amount=1200, metadata={'tags': ['fake']})
        review = Review(source)
        source['metadata']['tags'].append('outside')
        review.approve(0, 'fictional-person')
        fields = review.fields
        fields['amount'] = 999
        fields['metadata']['tags'].append('changed')
        events = review.events
        events[0]['fields']['amount'] = 999
        self.assertEqual(review.fields['amount'], 1200)
        self.assertEqual(review.fields['metadata']['tags'], ['fake'])
        self.assertEqual(review.events[0]['fields']['amount'], 1200)
        self.assertTrue(review.approved)
        self.assertEqual(review.version, 0)
        for attr, value in (('fields', {}), ('events', []), ('approved', False), ('version', 99)):
            with self.assertRaises(AttributeError):
                setattr(review, attr, value)
        review.correct('amount', 999, 'fictional-person')
        self.assertFalse(review.approved)
        self.assertEqual(review.version, 1)
        self.assertEqual(review.events[-1]['after'], 999)

    def test_rounding_rejects_invalid(self):
        for lines in ([], [-1], [True], [1.5]):
            with self.assertRaises(ValueError):
                rounding(lines)
        for rate in ('NaN', '-1', '2'):
            with self.assertRaises(ValueError):
                rounding([1], rate=rate)


if __name__ == '__main__':
    unittest.main(verbosity=2)
