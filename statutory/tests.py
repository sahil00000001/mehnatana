from datetime import date
from decimal import Decimal as D

from django.test import TestCase

from .models import PTSlab, StatutoryRate, TaxConfig
from .rates import MissingRate, changed_inside, pt_slabs_on, rates_for_month, tax_config, value_on


class LookupTest(TestCase):
    def test_value_in_force_on_a_date(self):
        self.assertEqual(value_on("PF_WAGE_CEILING", date(2026, 9, 16)), D(15000))
        self.assertEqual(value_on("PF_WAGE_CEILING", date(2026, 9, 17)), D(25000))
        self.assertEqual(value_on("ESI_ON_CODE_WAGES", date(2025, 11, 20)), D(0))
        self.assertEqual(value_on("ESI_ON_CODE_WAGES", date(2025, 11, 21)), D(1))

    def test_month_uses_last_day(self):
        self.assertEqual(rates_for_month(date(2026, 8, 1)).rates.pf_ceiling, D(15000))
        september = rates_for_month(date(2026, 9, 1))
        self.assertEqual(september.rates.pf_ceiling, D(25000))
        self.assertIn("RATE_CHANGED_IN_MONTH", september.warnings)
        self.assertEqual(rates_for_month(date(2026, 10, 1)).warnings, [])

    def test_change_on_first_day_is_not_inside(self):
        StatutoryRate.objects.create(key="EDLI_RATE", value=D("0.40"), effective_from=date(2027, 1, 1))
        self.assertEqual(changed_inside(date(2027, 1, 1), date(2027, 1, 31)), [])
        self.assertEqual(rates_for_month(date(2027, 1, 1)).rates.edli_rate, D("0.4"))

    def test_new_pt_slab_set_replaces_old_for_that_state(self):
        PTSlab.objects.create(state="KA", gender="ANY", from_amount=30000, amount=250, february_amount=250,
                              effective_from=date(2027, 4, 1))
        self.assertEqual(len(pt_slabs_on(date(2027, 3, 31), "KA")), 1)
        self.assertEqual(pt_slabs_on(date(2027, 4, 30), "KA")[0][4], D(250))
        self.assertEqual(len(pt_slabs_on(date(2027, 4, 30), "MH")), 5)

    def test_tax_config_falls_back_to_last_year(self):
        new, old, used = tax_config(2027)
        self.assertEqual(used, 2026)
        self.assertEqual(new["std_deduction"], D(75000))
        self.assertIn("TAX_SLABS_NOT_UPDATED", rates_for_month(date(2027, 4, 1)).warnings)

    def test_missing_tax_config(self):
        TaxConfig.objects.all().delete()
        with self.assertRaises(MissingRate):
            rates_for_month(date(2026, 11, 1))

    def test_snapshot_is_json_friendly(self):
        import json

        snap = rates_for_month(date(2026, 11, 1)).snapshot
        json.dumps(snap)
        self.assertEqual(snap["values"]["EPS_RATE"], "8.33")
