"""Original, fictional public-review samples. Python 3.12+, standard library only."""
import argparse
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import json
from pathlib import Path
import sqlite3


def month(value):
    parsed = date.fromisoformat(value + '-01')
    if parsed.strftime('%Y-%m') != value:
        raise ValueError('month must be YYYY-MM')
    return value


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError('amount must be a nonnegative integer')
    return value


def monthly(rows, basis_month):
    """Use the explicitly captured transfer month, never today's date."""
    month(basis_month)
    seen, total, count = set(), 0, 0
    for row in rows:
        if row['id'] in seen:
            raise ValueError('duplicate source id')
        seen.add(row['id'])
        month(row['basis_month'])
        integer(row['amount'])
        if row['basis_month'] == basis_month:
            total += row['amount']
            count += 1
    return {'basis_month': basis_month, 'count': count, 'total': total}


def reconcile(expected, actual):
    """One-to-one IDs; multiple independent differences remain visible."""
    def index(rows):
        out = {}
        for row in rows:
            if not row.get('id') or row['id'] in out:
                raise ValueError('missing or duplicate invoice id')
            out[row['id']] = row
        return out
    left, right = index(expected), index(actual)
    results = []
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        issues = []
        if a is None or b is None:
            issues.append('missing_record')
        else:
            for field in ('category', 'date', 'amount'):
                if a.get(field) is None or b.get(field) is None:
                    issues.append('missing_field:' + field)
                elif field == 'date':
                    try:
                        da, db = date.fromisoformat(a[field]), date.fromisoformat(b[field])
                        if da != db:
                            issues.append('date_mismatch')
                    except (ValueError, TypeError):
                        issues.append('invalid_date')
                elif field == 'amount':
                    integer(a[field]); integer(b[field])
                    if a[field] != b[field]:
                        issues.append('amount_mismatch')
                elif a[field] != b[field]:
                    issues.append('category_mismatch')
        results.append({'id': key, 'issues': issues or ['matched']})
    return results


class Creator:
    """The request ledger and created record commit atomically in one DB."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute('CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)')
        self.db.commit()

    def create(self, key, payload, fail_before_commit=False):
        if not isinstance(key, str) or not key.strip():
            raise ValueError('request key required')
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            prior = self.db.execute('SELECT id,payload FROM records WHERE request_key=?', (key,)).fetchone()
            if prior:
                if prior[1] != encoded:
                    raise ValueError('request key reused with different payload')
                return {'id': prior[0], 'replayed': True}
            cursor = self.db.execute('INSERT INTO records(request_key,payload) VALUES (?,?)', (key, encoded))
            if fail_before_commit:
                raise RuntimeError('simulated interruption before commit')
            return {'id': cursor.lastrowid, 'replayed': False}

    def close(self):
        self.db.close()


class Review:
    """In-memory fictional OCR review; audit events are illustrative, not tamper-proof."""
    def __init__(self, fields):
        self._fields = deepcopy(dict(fields))
        self._version = 0
        self._approved = False
        self._events = [{'action': 'ocr_import', 'version': 0, 'fields': deepcopy(self._fields)}]

    @property
    def fields(self):
        return deepcopy(self._fields)

    @property
    def version(self):
        return self._version

    @property
    def approved(self):
        return self._approved

    @property
    def events(self):
        return deepcopy(self._events)

    def correct(self, field, value, actor):
        if not actor or field not in self._fields:
            raise ValueError('known field and actor required')
        old = deepcopy(self._fields[field])
        self._fields[field] = deepcopy(value)
        self._version += 1
        self._approved = False
        self._events.append({'action': 'correction', 'actor': actor, 'field': field, 'before': old, 'after': deepcopy(value), 'version': self._version})

    def approve(self, version, actor):
        if version != self.version or not actor:
            raise ValueError('stale version or missing actor')
        integer(self._fields['amount'])
        date.fromisoformat(self._fields['date'])
        if not self._fields.get('invoice_id'):
            raise ValueError('invoice id required')
        if self.approved:
            return False
        self._approved = True
        self._events.append({'action': 'approval', 'actor': actor, 'version': version})
        return True


def rounding(lines, rate='0.10', mode='floor'):
    """Synthetic arithmetic comparison only; no tax-compliance decision."""
    modes = {'floor': ROUND_DOWN, 'half_up': ROUND_HALF_UP}
    if mode not in modes or not lines:
        raise ValueError('nonempty lines and supported rounding mode required')
    if not isinstance(rate, (str, Decimal)):
        raise ValueError('rate must be a decimal string or Decimal; float is unsupported')
    r = Decimal(rate)
    if not r.is_finite() or r < 0 or r > 1:
        raise ValueError('rate must be finite and between zero and one')
    amounts = [Decimal(integer(v)) for v in lines]
    quantize = lambda value: int(value.quantize(Decimal('1'), rounding=modes[mode]))
    per_line = sum(quantize(v * r) for v in amounts)
    per_invoice = quantize(sum(amounts) * r)
    return {'rate': str(r), 'mode': mode, 'per_line': per_line, 'per_invoice': per_invoice, 'difference': per_invoice - per_line}


def run(name, fixture, db_path):
    if name == 'monthly':
        return monthly(fixture['transfers'], fixture['basis_month'])
    if name == 'reconcile':
        return reconcile(fixture['expected'], fixture['actual'])
    if name == 'idempotency':
        service = Creator(db_path)
        try:
            return [service.create('fictional-click-001', {'invoice': 'DEMO-001'}), service.create('fictional-click-001', {'invoice': 'DEMO-001'})]
        finally:
            service.close()
    if name == 'ocr':
        review = Review(fixture['ocr'])
        review.correct('amount', 1200, 'fictional-reviewer')
        review.approve(review.version, 'fictional-approver')
        return {'fields': review.fields, 'approved': review.approved, 'events': review.events}
    return rounding(fixture['rounding_lines'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('demo', choices=['monthly', 'reconcile', 'idempotency', 'ocr', 'rounding', 'all'])
    parser.add_argument('--db', default=':memory:', help='SQLite path; use a file to demonstrate restart replay')
    args = parser.parse_args()
    fixture = json.loads(Path(__file__).with_name('fixtures.json').read_text(encoding='utf-8'))
    names = ['monthly', 'reconcile', 'idempotency', 'ocr', 'rounding'] if args.demo == 'all' else [args.demo]
    print(json.dumps({name: run(name, fixture, args.db) for name in names}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
