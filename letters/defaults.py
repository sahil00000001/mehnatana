"""Default letter texts, exactly as README §12.3 - §12.5. Plain text; blank line = new paragraph."""
from .models import LetterType

APPOINTMENT = """\
Ref: {{ letter.ref_no }}                                        Date: {{ letter.date }}

To,
{{ emp.title }} {{ emp.full_name }}
{{ emp.address }}

Subject: Letter of Appointment

Dear {{ emp.full_name }},

We are pleased to appoint you as {{ emp.designation }} at {{ company.trade_name }} with effect from {{ emp.doj }}, on the following terms.

{{ letter.particulars_table }}

1. Place of work: {{ company.address }}. You may be asked to work at our other premises in the same town.
{{ letter.probation_line }}
2. Working hours: not more than 8 hours a day and 48 hours a week, with one weekly holiday, as per law. Overtime, when asked, will be paid at twice the ordinary rate of wages.
3. Wages: paid every month, before the 7th of the following month, by {{ emp.payment_mode }}. Deductions for PF, ESI, professional tax and income tax will be made where the law requires.
4. Leave and holidays: as per the applicable law and our rules.
5. Notice: either side may end this employment with {{ letter.notice_days }} days' written notice, or wages in place of notice.
6. Conduct: you will follow the rules of the establishment and keep business information confidential.

Please sign the copy of this letter to show that you accept these terms.

For {{ company.legal_name }}


{{ company.signatory_name }}
{{ company.signatory_designation }}

---------------------------------------------------------------------------
ACCEPTANCE
I have read and understood the above terms and I accept them.

Signature / Thumb impression: ____________________     Date: ______________
Name: {{ emp.full_name }}
"""

RELIEVING = """\
Ref: {{ letter.ref_no }}                                        Date: {{ letter.date }}

To,
{{ emp.title }} {{ emp.full_name }}
Employee code: {{ emp.code }}

Subject: Relieving Letter

Dear {{ emp.full_name }},

{{ letter.resignation_line }}

This is to confirm that you have been relieved from your duties as {{ emp.designation }} at {{ company.trade_name }} at the close of working hours on {{ emp.dol }}.

Your period of service with us was from {{ emp.doj }} to {{ emp.dol }}.

Your full and final dues will be settled as per law.

We thank you for your service and wish you success in the future.

For {{ company.legal_name }}


{{ company.signatory_name }}
{{ company.signatory_designation }}
"""

EXPERIENCE = """\
Ref: {{ letter.ref_no }}                                        Date: {{ letter.date }}

TO WHOM IT MAY CONCERN

This is to certify that {{ emp.title }} {{ emp.full_name }} ({{ emp.relation_label }}: {{ emp.relation_name }}) worked with {{ company.legal_name }}, {{ company.city }}, as {{ emp.designation }} from {{ emp.doj }} to {{ emp.dol }}.

During this period {{ emp.his_her }} work and conduct were found to be good.

We wish {{ emp.him_her }} all the best.

For {{ company.legal_name }}


{{ company.signatory_name }}
{{ company.signatory_designation }}
"""

DEFAULT_TEXT = {
    LetterType.APPOINTMENT: APPOINTMENT,
    LetterType.RELIEVING: RELIEVING,
    LetterType.EXPERIENCE: EXPERIENCE,
}
