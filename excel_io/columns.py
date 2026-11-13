"""
Column lists of the three upload templates (README §6). Each column:
(key, exact header text, required, kind, English help, Hindi help).
kind: text | money | number | days | date | yesno | choice:<name>
"""

MONTHLY_COLUMNS = [
    ("emp_code", "Emp Code", True, "text", "Do not change.", "इसे न बदलें।"),
    ("name", "Employee Name", False, "text", "For reading only.", "केवल पढ़ने के लिए।"),
    ("designation", "Designation", False, "text", "For reading only.", "केवल पढ़ने के लिए।"),
    ("doj", "Date of Joining", False, "date", "From the employee record. For reading only.",
     "कर्मचारी रिकॉर्ड से। केवल पढ़ने के लिए।"),
    ("pan", "PAN", False, "text", "From the employee record. For reading only.", "कर्मचारी रिकॉर्ड से। केवल पढ़ने के लिए।"),
    ("uan", "UAN (PF No.)", False, "text", "From the employee record. For reading only.",
     "कर्मचारी रिकॉर्ड से। केवल पढ़ने के लिए।"),
    ("esic_ip_no", "ESI No.", False, "text", "ESIC IP number from the employee record. For reading only.",
     "कर्मचारी रिकॉर्ड से ESIC नंबर। केवल पढ़ने के लिए।"),
    ("days_in_month", "Days in Month", False, "number", "Days in this month. Do not change.",
     "महीने के कुल दिन। इसे न बदलें।"),
    ("working_days", "Working Days", False, "number",
     "Working days of this person this month: days employed minus the weekly offs set in Payroll rules. "
     "For reading only.",
     "इस महीने कर्मचारी के काम के दिन (साप्ताहिक छुट्टी घटाकर)। केवल पढ़ने के लिए।"),
    ("present", "Days Present", False, "days", "Days the person came to work. Half days allowed, like 25.5.",
     "काम पर आए दिन। आधा दिन भी लिख सकते हैं, जैसे 25.5।"),
    ("paid_leave", "Paid Leave Days", False, "days", "Leave with pay. Half days allowed.",
     "वेतन सहित छुट्टी के दिन। आधा दिन भी लिख सकते हैं।"),
    ("week_off_holiday", "Weekly Off + Holidays", False, "days",
     "Paid weekly offs and paid holidays. The weekly offs of this month are already filled in; add paid holidays.",
     "वेतन सहित साप्ताहिक छुट्टी और त्योहार की छुट्टी। साप्ताहिक छुट्टियाँ पहले से भरी हैं; त्योहार की छुट्टी जोड़ें।"),
    ("lop", "Absent / LOP Days", False, "days",
     "Days without pay. Leave empty and it is worked out: Days in Month minus the three columns before.",
     "बिना वेतन के दिन। खाली छोड़ें तो अपने आप गिना जाएगा।"),
    ("ot_hours", "OT Hours", False, "number", "Overtime hours this month. Paid at twice the ordinary rate.",
     "इस महीने ओवरटाइम के घंटे। दोगुनी दर से भुगतान।"),
    ("ot_amount", "OT Amount ₹ (optional)", False, "money",
     "Fill only to pay a fixed overtime amount. If filled, OT Hours are not used.",
     "तय ओवरटाइम राशि देनी हो तभी भरें। भरने पर घंटे नहीं गिने जाएंगे।"),
    ("incentive", "Incentive / Bonus ₹", False, "money", "One-time extra pay this month.",
     "इस महीने एक बार की अतिरिक्त राशि।"),
    ("increment", "Increment ₹ per month", False, "money",
     "Raise added to Basic from this month onwards. For a cut use Revise salary.",
     "इस महीने से बेसिक में बढ़ोतरी। वेतन घटाने के लिए Revise salary इस्तेमाल करें।"),
    ("advance", "Advance Deduction ₹", False, "money",
     "Advance instalment to cut. Already filled in; change it if needed.",
     "एडवांस की कटौती। पहले से भरी है; ज़रूरत हो तो बदलें।"),
    ("other_deduction", "Other Deduction ₹", False, "money", "Any other cut, like canteen. Needs a reason.",
     "कोई और कटौती, जैसे कैंटीन। कारण लिखना ज़रूरी है।"),
    ("other_reason", "Other Deduction Reason", False, "text", "Why the other deduction is made.",
     "दूसरी कटौती का कारण।"),
    ("tds_override", "TDS Override ₹ (optional)", False, "money",
     "Only if your CA gave you a TDS figure for this person this month.",
     "केवल तब भरें जब CA ने इस महीने की TDS राशि दी हो।"),
    ("remarks", "Remarks", False, "text", "Any note. It is shown in the salary register.",
     "कोई टिप्पणी। वेतन रजिस्टर में दिखेगी।"),
]
MONTHLY_PREFILLED_GREY = {"emp_code", "name", "designation", "doj", "pan", "uan", "esic_ip_no", "days_in_month",
                          "working_days"}
# Filled in from the records, never read back: a sheet without these columns still uploads.
MONTHLY_READ_ONLY = {"name", "designation", "doj", "pan", "uan", "esic_ip_no", "working_days"}


def monthly_columns(company):
    """The columns of this company's attendance sheet (the same for every company today)."""
    return list(MONTHLY_COLUMNS)

EMPLOYEE_COLUMNS = [
    ("emp_code", "Emp Code", True, "text", "A different code for each person, like E001.",
     "हर कर्मचारी का अलग कोड, जैसे E001।"),
    ("first_name", "First Name", True, "text", "First name.", "पहला नाम।"),
    ("surname", "Surname", False, "text", "Surname (optional).", "उपनाम (ज़रूरी नहीं)।"),
    ("relation_type", "Relation (Father/Mother/Spouse)", True, "choice:relation", "Choose Father, Mother or Spouse.",
     "पिता, माता या पति/पत्नी चुनें।"),
    ("relation_name", "Relation Name", True, "text", "Name of that father, mother or spouse.",
     "पिता, माता या पति/पत्नी का नाम।"),
    ("gender", "Gender (M/F/O)", True, "choice:gender", "M = male, F = female, O = other.",
     "M = पुरुष, F = महिला, O = अन्य।"),
    ("date_of_birth", "Date of Birth", True, "date", "Write like 15-06-1995.", "ऐसे लिखें: 15-06-1995।"),
    ("date_of_joining", "Date of Joining", True, "date", "First day of work, like 01-04-2022.",
     "काम का पहला दिन, जैसे 01-04-2022।"),
    ("mobile", "Mobile", False, "text", "10-digit mobile number.", "10 अंकों का मोबाइल नंबर।"),
    ("email", "Email", False, "text", "Email address (optional).", "ईमेल (ज़रूरी नहीं)।"),
    ("address", "Address", False, "text", "Home address.", "घर का पता।"),
    ("department", "Department", False, "text", "Like Store or Accounts. A new name is added automatically.",
     "जैसे स्टोर या अकाउंट्स। नया नाम अपने आप जुड़ जाएगा।"),
    ("designation", "Designation", True, "text", "Job title, like Helper or Cashier. A new name is added automatically.",
     "पद, जैसे हेल्पर या कैशियर। नया नाम अपने आप जुड़ जाएगा।"),
    ("reports_to", "Reports To (Emp Code)", False, "text",
     "Emp Code of the person's manager. Leave empty for the person at the top.",
     "मैनेजर का कर्मचारी कोड। सबसे ऊपर वाले के लिए खाली छोड़ें।"),
    ("employment_type", "Employment Type", False, "choice:employment_type",
     "Permanent, Probation, Fixed-term, Contract or Part-time.", "स्थायी, परिवीक्षा, निश्चित अवधि, अनुबंध या अंशकालिक।"),
    ("skill_category", "Skill Category", False, "choice:skill", "Unskilled, Semi-skilled, Skilled or Highly skilled.",
     "अकुशल, अर्ध-कुशल, कुशल या अति कुशल।"),
    ("nature_of_duties", "Nature of Duties", False, "text",
     "Main work in a few words (printed on the appointment letter).", "मुख्य काम कुछ शब्दों में (नियुक्ति पत्र पर छपेगा)।"),
    ("pan", "PAN", False, "text", "Like ABCDE1234F.", "जैसे ABCDE1234F।"),
    ("aadhaar", "Aadhaar (only with consent)", False, "text", "12 digits. Only if the person agrees to give it.",
     "12 अंक। केवल कर्मचारी की सहमति से।"),
    ("aadhaar_consent", "Aadhaar Consent (Yes/No)", False, "yesno", "Yes if the person agreed to give Aadhaar.",
     "आधार देने की सहमति हो तो Yes।"),
    ("uan", "UAN", False, "text", "12-digit PF UAN.", "12 अंकों का PF UAN।"),
    ("esic_ip_no", "ESIC IP Number", False, "text", "10-digit ESIC insurance number.", "10 अंकों का ESIC नंबर।"),
    ("payment_mode", "Payment Mode", False, "choice:payment_mode",
     "Bank, Cash, Cheque or UPI. Empty = Bank if bank details are filled, otherwise Cash.",
     "बैंक, नकद, चेक या UPI। खाली = बैंक विवरण हो तो बैंक, वरना नकद।"),
    ("bank_name", "Bank Name", False, "text", "Needed if Payment Mode is Bank.", "भुगतान बैंक से हो तो ज़रूरी।"),
    ("bank_account_no", "Bank Account No", False, "text", "Needed if Payment Mode is Bank.",
     "भुगतान बैंक से हो तो ज़रूरी।"),
    ("ifsc", "IFSC", False, "text", "Like SBIN0001234. Needed if Payment Mode is Bank.",
     "जैसे SBIN0001234। बैंक भुगतान के लिए ज़रूरी।"),
    ("basic", "Basic", True, "money", "Basic pay per month in rupees. Used only for new staff.",
     "हर महीने का मूल वेतन (रुपये में)। केवल नए कर्मचारियों के लिए।"),
    ("da", "DA", False, "money", "Dearness allowance per month. Empty = 0.", "महंगाई भत्ता प्रति माह। खाली = 0।"),
    ("hra", "HRA", False, "money", "House rent allowance per month. Empty = 0.", "मकान किराया भत्ता प्रति माह। खाली = 0।"),
    ("conveyance", "Conveyance", False, "money", "Travel allowance per month. Empty = 0.", "यात्रा भत्ता प्रति माह। खाली = 0।"),
    ("other_allowance", "Other Allowance", False, "money", "Any other fixed allowance per month. Empty = 0.",
     "कोई अन्य तय भत्ता प्रति माह। खाली = 0।"),
    ("pf_member", "PF Member (Yes/No)", False, "yesno", "Empty = Yes if your business has PF.",
     "खाली = आपके यहाँ PF हो तो Yes।"),
    ("eps_member", "EPS Member (Yes/No)", False, "yesno",
     "Pension. Empty = Yes if PF wages at joining were within the PF ceiling and age under 58.",
     "पेंशन। खाली = जॉइनिंग पर PF वेतन सीमा के अंदर और उम्र 58 से कम हो तो Yes।"),
    ("esi_override", "ESI (Auto/Yes/No)", False, "choice:esi", "Auto = decided by salary.", "Auto = वेतन के अनुसार तय होगा।"),
    ("is_pwd", "Person with Disability (Yes/No)", False, "yesno", "Empty = No.", "खाली = No।"),
    ("pt_exempt", "PT Exempt (Yes/No)", False, "yesno", "Yes only if exempt from professional tax. Empty = No.",
     "व्यवसाय कर से छूट हो तभी Yes। खाली = No।"),
    ("tax_regime", "Tax Regime (New/Old)", False, "choice:regime", "Empty = your default regime.",
     "खाली = आपकी सामान्य सेटिंग।"),
    ("old_regime_deductions_annual", "Old Regime Deductions per Year", False, "money",
     "Total yearly deductions (80C, 80D, HRA exemption, home loan interest). Only for Old regime.",
     "सालाना कुल छूट (80C, 80D, HRA, होम लोन ब्याज)। केवल पुरानी व्यवस्था के लिए।"),
]

OPENING_COLUMNS = [
    ("emp_code", "Emp Code", True, "text", "Do not change.", "इसे न बदलें।"),
    ("name", "Employee Name", False, "text", "For reading only.", "केवल पढ़ने के लिए।"),
    ("tax_year", "Tax Year (e.g. 2026-27)", True, "text", "Like 2026-27 (April 2026 to March 2027).",
     "जैसे 2026-27 (अप्रैल 2026 से मार्च 2027)।"),
    ("ytd_gross", "Gross Paid This Year Before Start Month ₹", False, "money",
     "Total gross salary paid from April until the month before you started using the app.",
     "अप्रैल से ऐप शुरू करने से पहले तक दिया गया कुल वेतन।"),
    ("ytd_tds", "TDS Deducted ₹", False, "money", "TDS already cut in those months.", "उन महीनों में काटा गया TDS।"),
    ("ytd_pt", "Professional Tax Deducted ₹", False, "money", "Professional tax already cut in those months.",
     "उन महीनों में काटा गया व्यवसाय कर।"),
    ("prev_employer_income", "Income From Previous Employer ₹", False, "money",
     "Salary from an earlier job in this tax year.", "इस वित्त वर्ष में पिछली नौकरी का वेतन।"),
    ("prev_employer_tds", "TDS By Previous Employer ₹", False, "money", "TDS cut by the earlier employer.",
     "पिछले नियोक्ता द्वारा काटा गया TDS।"),
]
OPENING_PREFILLED_GREY = {"emp_code", "name"}

# Drop-down lists (Excel data validation) and the values they map to.
CHOICES = {
    "relation": {"father": "Father", "mother": "Mother", "spouse": "Spouse", "husband": "Spouse", "wife": "Spouse"},
    "gender": {"m": "M", "male": "M", "f": "F", "female": "F", "o": "O", "other": "O"},
    "employment_type": {"permanent": "Permanent", "probation": "Probation", "fixed-term": "Fixed-term",
                        "fixed term": "Fixed-term", "contract": "Contract", "part-time": "Part-time",
                        "part time": "Part-time"},
    "skill": {"unskilled": "Unskilled", "semi-skilled": "Semi-skilled", "semi skilled": "Semi-skilled",
              "skilled": "Skilled", "highly skilled": "Highly skilled", "highly-skilled": "Highly skilled"},
    "payment_mode": {"bank": "Bank", "cash": "Cash", "cheque": "Cheque", "check": "Cheque", "upi": "UPI"},
    "esi": {"auto": "AUTO", "yes": "YES", "y": "YES", "no": "NO", "n": "NO"},
    "regime": {"new": "NEW", "old": "OLD"},
}
DROPDOWNS = {
    "relation": ["Father", "Mother", "Spouse"],
    "gender": ["M", "F", "O"],
    "employment_type": ["Permanent", "Probation", "Fixed-term", "Contract", "Part-time"],
    "skill": ["Unskilled", "Semi-skilled", "Skilled", "Highly skilled"],
    "payment_mode": ["Bank", "Cash", "Cheque", "UPI"],
    "esi": ["Auto", "Yes", "No"],
    "regime": ["New", "Old"],
    "yesno": ["Yes", "No"],
}


def header_of(columns, key):
    for col in columns:
        if col[0] == key:
            return col[1]
    return key
