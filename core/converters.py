from datetime import date


class MonthConverter:
    """/salary/2026-11/ <-> date(2026, 11, 1)"""

    regex = r"[0-9]{4}-(?:0[1-9]|1[0-2])"

    def to_python(self, value):
        year, month = value.split("-")
        return date(int(year), int(month), 1)

    def to_url(self, value):
        if isinstance(value, date):
            return f"{value.year:04d}-{value.month:02d}"
        return str(value)


class TaxYearConverter:
    """/reports/tds/2026-27/ <-> 2026 (tax year April 2026 - March 2027)"""

    regex = r"[0-9]{4}-[0-9]{2}"

    def to_python(self, value):
        start, end = value.split("-")
        if (int(start) + 1) % 100 != int(end):
            raise ValueError("not a tax year")
        return int(start)

    def to_url(self, value):
        value = int(value)
        return f"{value}-{(value + 1) % 100:02d}"
