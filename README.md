# Mehnatana (मेहनताना) — payroll and staff register for small Indian businesses

> **Working name.** *Mehnatana* is the Hindi/Urdu word for the wages a person earns for their labour.
> Tagline: **"Har mehnat ka pakka hisaab."** To rename the product, change `APP_NAME` in `config/settings.py`; the name must not be hard-coded anywhere else.

---

## How to use this file (instructions for Claude Code)

1. Read the whole file before writing any code.
2. Build in the order given in **§19 Build order**. Run the tests after every step.
3. The money math is fixed by **Appendix A (reference engine)** and the **golden numbers in §17**. Port Appendix A into `payroll/engine.py` without changing any result. If any sentence in this file seems to disagree with Appendix A, Appendix A wins; leave a `# NOTE:` comment explaining the conflict.
4. Statutory rates and slabs must be read from database tables (seeded in **§9**). Never hard-code a rate in a view, form or template.
5. Keep it boring on purpose: server-rendered Django pages, one plain CSS file, a few lines of vanilla JavaScript. **No** React/Vue/Angular, no SPA, no Tailwind/Bootstrap, no icon fonts, no animations, no webfonts.
6. When something is unclear, pick the simplest behaviour that keeps data safe, and write the decision in `DECISIONS.md`.
7. Every page must work with JavaScript turned off (JS may only add convenience).

---

## 0. Quick facts

| | |
|---|---|
| **Who uses it** | Owners of very small shops and businesses in India (5–200 staff) who are not comfortable with technology. One login per business. |
| **Who runs it** | The platform owner (you), who onboards each business and keeps statutory rates up to date. |
| **What it does** | Employee register with hierarchy → monthly attendance sheet in Excel → salary with PF, ESI, professional tax, TDS, advances, increments → payslips, salary register, cost dashboard, PF ECR file, ESI file → appointment, relieving and experience letters. |
| **Look and feel** | Deliberately old-fashioned: looks like a neat ledger (*bahi-khata*). Big text, bordered tables, buttons with words, no animations. |
| **Stack** | Python 3.12+, Django 5.2 LTS, PostgreSQL 16 (SQLite for local dev), openpyxl, Gunicorn, WhiteNoise, Caddy (HTTPS). No other frameworks. |
| **Law as of** | 4 November 2026 (see §21). All rates live in tables with effective dates. |

---

## 1. Product in one page

### 1.1 The monthly routine the product is built around

```
 Owner opens "Monthly Salary" → picks November 2026
   ① Download the attendance sheet (already filled with all employee names)
   ② Fill days present / leave / OT / advance to cut / increment in Excel
   ③ Upload it → the system checks every row, shows mistakes in plain words
   ④ Look at the salary table (earnings, PF, ESI, PT, TDS, net pay, cost) → Finalise
   ⑤ Print payslips, salary register, bank sheet; download PF and ESI files
```

### 1.2 Principles (apply them everywhere)

1. **Obvious over clever.** Every action is a button with words. One main action per page. Every page starts with a one-line instruction ("Fill this form and press Save").
2. **Excel in, paper out.** Owners already keep registers in Excel; we accept Excel and produce A4 pages that print cleanly.
3. **Every upload has a "Download sample file" link right next to it.**
4. **Nothing gets lost.** No hard deletes of employees, salary months, payslips or letters. Finalised months are locked. Every change is in the audit log.
5. **Law is data.** PF/ESI/PT/tax figures sit in tables with "effective from" dates, so a new notification needs a new row, not new code.
6. **Money is exact.** Python `Decimal` only (never `float`), whole rupees, rounding rules in §8.10.
7. **Plain language.** Messages talk like a helpful accountant: "Row 7: Ramesh Kumar — present days (31) are more than days in November (30)."

### 1.3 In scope for version 1
Companies and logins; employee register; departments, designations and reporting hierarchy; salary structure and revisions; opening balances for businesses starting mid-year; advances; monthly Excel upload; salary calculation (PF, ESI, PT, TDS, OT, increments, advances, other deductions); review, finalise, reopen; payslips (Form V), salary register, bank sheet, cash list; dashboard with **In-hand / Gross / Total cost** toggle; PF and ESI totals; PF ECR text file; ESI upload sheet; appointment, relieving and experience letters; platform admin; audit log.

### 1.4 Not in version 1 (see §20)
Roles and multiple users per company, employee self-service, Hindi translation strings (but the code must be translation-ready), leave balances, full-and-final settlement with gratuity, statutory bonus, Labour Welfare Fund, Form 16/24Q, arrears, biometric import, PDF download on the server, SMS/WhatsApp.

---

## 2. Users and login

| User | How they log in | What they can do |
|---|---|---|
| **Platform admin** (Django superuser) | `/admin/` | Create a company and its login, reset passwords, deactivate a company, edit statutory tables (§9), read the audit log. |
| **Company owner** | `/login/` with username (mobile number or email) + password | Everything inside **their own company only**. No roles in v1. |

- The `User` model has `company` (nullable only for superusers) and `role` (choices: `OWNER`; default `OWNER`) so roles can be added later without a migration headache.
- First login forces a password change (`must_change_password=True` when created by admin).
- **Forgot password** page: "Please call or WhatsApp your Mehnatana administrator at {SUPPORT_PHONE}." If email is configured, also offer Django's password-reset email.
- Lock a username for 15 minutes after 5 wrong passwords (counters live on the User row: `failed_logins`, `locked_until`).
- Session ends after 8 hours of inactivity.

### 2.1 Multi-company isolation (critical)
- Every company-owned model inherits `CompanyOwned` (`company` FK, `created_at`, `updated_at`, `created_by`).
- `TenantMiddleware` sets `request.company = request.user.company` for logged-in owners; superusers are redirected to `/admin/`.
- Every view uses a `CompanyScopedMixin` whose `get_queryset()` filters by `request.company`. Every single-object fetch is `get_object_or_404(Model, pk=pk, company=request.company)`.
- Form choice fields (e.g., *Reports to*, *Department*) must only list the same company's rows.
- Uploaded files and logos are **never** served from a public URL. They are returned through views that check the company.
- Tests in §17.3 must prove that company A gets **404** on every URL pointing to company B's data.

---

## 3. Architecture

### 3.1 Picture

```
     Shop owner (PC or phone browser)                  Platform admin (you)
                 │ HTTPS                                        │ HTTPS
                 ▼                                              ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │ Caddy  — automatic HTTPS, max upload 10 MB, reverse proxy to :8000     │
   └───────────────────────────────────┬───────────────────────────────────┘
                                       ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │ Django 5.2 app under Gunicorn (static files via WhiteNoise)            │
   │                                                                       │
   │  accounts   login, lockout, password ── TenantMiddleware (company)     │
   │  companies  profile, payroll settings                                 │
   │  employees  register, hierarchy, salary revisions, opening balances   │
   │  advances   advances given, recoveries                                │
   │  statutory  PF/ESI rates, PT slabs, tax slabs, due dates (dated rows) │
   │  payroll    month runs, lines, engine.py (pure math), service.py      │
   │  excel_io   sample/pre-filled sheets + upload parsers + error marking │
   │  reports    dashboard, register, bank sheet, ECR, ESI file, exports   │
   │  letters    appointment / relieving / experience templates + history  │
   │  core       base models, audit log, ₹ formatting, amount in words     │
   └───────────────┬───────────────────────────────────┬───────────────────┘
                   ▼                                   ▼
        PostgreSQL 16 (every row has company_id)   media/ folder (logos, uploaded
                   │                                sheets) — private, served by views
                   ▼
        nightly pg_dump + media backup → off-server copy
```

### 3.2 Why this stack
Server-rendered Django gives login, forms, migrations, an admin panel and protection against common web attacks out of the box, and produces plain HTML that works on old phones and slow internet. PostgreSQL is reliable for money data. openpyxl reads and writes `.xlsx` without Excel installed. Printing uses the browser's own *Print → Save as PDF*, so no PDF libraries with system dependencies are needed.

### 3.3 Folder layout

```
mehnatana/
├─ manage.py
├─ requirements.txt          Django>=5.2,<5.3  psycopg[binary]>=3.2  openpyxl>=3.1
│                            gunicorn>=23  whitenoise>=6.7  python-dotenv>=1.0
├─ .env.example
├─ Dockerfile, docker-compose.yml, Caddyfile
├─ DECISIONS.md               decisions taken while building
├─ config/                    settings.py, urls.py, wsgi.py
├─ core/                      CompanyOwned, TenantMiddleware, CompanyScopedMixin, AuditLog,
│                             formatting.py (Indian number format, amount in words), templatetags
├─ accounts/                  custom User, login/logout/change password, lockout
├─ companies/                 Company, settings forms
├─ employees/                 Department, Designation, Employee, SalaryRevision, OpeningBalance
├─ advances/                  Advance, AdvanceRecovery
├─ statutory/                 StatutoryRate, PTSlab, TaxConfig, DueDate, rates.py (lookup)
├─ payroll/                   PayrollRun, PayrollLine, engine.py, service.py, views
├─ excel_io/                  builders.py (templates), parsers.py, errors.py
├─ reports/                   dashboard, registers, ECR, ESI file, exports
├─ letters/                   LetterTemplate, IssuedLetter, defaults.py, render.py
├─ templates/                 base.html, print_base.html, one folder per app
└─ static/                    css/site.css, css/print.css, js/site.js
```

### 3.4 Settings to read from environment
`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `POSTGRES_DB/USER/PASSWORD/HOST/PORT` (if absent → SQLite file for dev), `SUPPORT_PHONE`, `SUPPORT_EMAIL`, `EMAIL_*` (optional), `TIME_ZONE=Asia/Kolkata`, `LANGUAGE_CODE=en-in`, `APP_NAME=Mehnatana`.

---

## 4. Data model

Conventions: money = `DecimalField(max_digits=12, decimal_places=2)` holding whole rupees; days = `DecimalField(max_digits=4, decimal_places=1)` (half days allowed); every company-owned table inherits `CompanyOwned`; every table has `created_at`/`updated_at`.

### 4.1 Company
| Field | Notes |
|---|---|
| `code` | Short unique code, 3–6 capital letters (e.g., `SRKS`). Used in letter numbers and file names. |
| `legal_name`, `trade_name` | Trade name is what appears on top of payslips/letters. |
| `address_line1`, `address_line2`, `city`, `state`, `pincode` | `state` is a choice of the 36 States/UTs (ISO-like codes `KA`, `MH`, `DL`…). |
| `phone`, `email`, `logo` | Logo optional, PNG/JPG ≤ 500 KB. |
| `pan`, `tan`, `gstin` | Optional, validated if filled. |
| `pf_establishment_code`, `esic_employer_code`, `lin`, `pt_registration_no` | `lin` = Labour Identification Number (printed on appointment letters). |
| `signatory_name`, `signatory_designation` | Printed under signatures. |
| `payroll_start_month` | First month this company will process in the app (1st of month). |
| `is_active` | Inactive companies cannot log in. |
| **Payroll settings** | |
| `salary_divisor` | `CALENDAR` (default, days in the month) / `FIXED_30` / `FIXED_26`. |
| `pf_enabled`, `pf_employee_rate` | Rate 12.00 (default) or 10.00 (only for establishments notified for 10%). |
| `pf_restrict_to_ceiling` | Default **True**: PF on wages up to the ceiling only. |
| `esi_enabled`, `pt_enabled`, `tds_enabled` | Owner switches; help text explains when each applies (§8). |
| `pt_state` | Defaults to `state`; PT slabs come from §9.2. |
| `default_tax_regime` | `NEW` (default) / `OLD` — used only to prefill new employees. |
| `ot_multiplier` (2), `ot_days_divisor` (26), `ot_hours_per_day` (8) | Overtime rate settings. |
| `notice_period_days` (30), `probation_months` (0), `higher_wages_avenue` (text) | Used in the appointment letter. |
| `payslip_two_per_page` | Print two payslips on one A4 sheet (A5 halves) to save paper. |

### 4.2 User
Custom `AbstractUser`: `company` FK (null for superuser), `role` (`OWNER`), `must_change_password`, `failed_logins`, `locked_until`.

### 4.3 Department, Designation
`name` (unique per company), `is_active`. Managed from small list pages.

### 4.4 Employee
| Group | Fields |
|---|---|
| Identity | `emp_code` (unique per company; suggest next `E001`, `E002`…; locked once a month containing the employee is finalised), `first_name`, `surname`, `relation_type` (`Father`/`Mother`/`Spouse`), `relation_name`, `gender` (`M`/`F`/`O`), `date_of_birth`, `mobile`, `email`, `address` |
| Job | `date_of_joining`, `department` FK, `designation` FK, `reports_to` FK self (no loops), `employment_type` (`Permanent`, `Probation`, `Fixed-term`, `Contract`, `Part-time`), `skill_category` (`Unskilled`, `Semi-skilled`, `Skilled`, `Highly skilled`), `nature_of_duties` (text, for appointment letter) |
| Statutory IDs | `pan`, `aadhaar` (optional, only with `aadhaar_consent=True`; always shown masked), `uan`, `name_as_per_uan` (for ECR; default = full name in capitals), `esic_ip_no` |
| Bank | `payment_mode` (`Bank`, `Cash`, `Cheque`, `UPI`), `bank_name`, `bank_account_no`, `ifsc` |
| Rules | `pf_member` (bool), `eps_member` (bool), `esi_override` (`AUTO`/`YES`/`NO`), `is_pwd` (person with disability), `pt_exempt`, `tax_regime` (`NEW`/`OLD`), `old_regime_deductions_annual` |
| Exit | `status` (`ACTIVE`/`RELIEVED`), `date_of_leaving`, `leaving_reason` (`Resigned`, `Terminated`, `Retired`, `Absconding`, `Deceased`, `Other`), `resignation_date`, `exit_remarks` |

Validation: PAN `^[A-Z]{5}[0-9]{4}[A-Z]$`; UAN 12 digits; ESIC IP 10 digits; Aadhaar 12 digits; IFSC `^[A-Z]{4}0[A-Z0-9]{6}$`; mobile `^[6-9][0-9]{9}$`; pincode 6 digits; date of birth gives age 14–80 at joining (warn below 18). `eps_member` default: **Yes** if PF wages at joining ≤ PF ceiling and age < 58, else **No** (show this rule as help text).

### 4.5 SalaryRevision (salary structure history)
`employee`, `effective_from` (1st of a month), `basic`, `da`, `hra`, `conveyance`, `other_allowance`, `reason`, `source` (`MANUAL`/`MONTHLY_SHEET`/`OPENING`), `payroll_run` (FK, null; set when created by finalising a month). The structure in force for a month = latest row with `effective_from` ≤ month start. Every employee must have at least one row (created with the employee).

### 4.6 OpeningBalance (for a business that starts mid-year, or a new joiner's previous job)
`employee`, `tax_year` (e.g., 2026 = April 2026–March 2027), `ytd_gross`, `ytd_tds`, `ytd_pt`, `prev_employer_income`, `prev_employer_tds`. Used only by the TDS calculation.

### 4.7 Advance, AdvanceRecovery
`Advance`: `employee`, `date_given`, `amount`, `monthly_installment`, `note`, `status` (`OPEN`/`CLOSED`). Outstanding = amount − sum of recoveries.
`AdvanceRecovery`: `advance`, `payroll_line`, `month`, `amount`. Created only when a month is finalised; deleted if that month is reopened. Recoveries go to the oldest open advance first; an advance closes automatically when outstanding reaches 0.

### 4.8 PayrollRun (one per company per month)
`month` (1st of month, unique per company), `status` (`DRAFT`/`FINALISED`), `uploaded_file` (private), `uploaded_at`, `finalised_at`, `finalised_by`, `revision_no` (0, +1 on each reopen), `reopen_reason`, `pf_admin_establishment` (§8.4), `rates_snapshot` (JSON of every rate used).

### 4.9 PayrollLine (one per employee per month) — a complete snapshot
- **Inputs** (from the sheet): `days_in_month`, `present`, `paid_leave`, `week_off_holiday`, `lop_days`, `ot_hours`, `ot_amount_override`, `incentive`, `increment`, `advance_deduction`, `other_deduction`, `other_deduction_reason`, `tds_override`, `remarks`.
- **Structure used** (after increment): `rate_basic`, `rate_da`, `rate_hra`, `rate_conveyance`, `rate_other`.
- **Results**: `paid_days`, `ncp_days`, `earned_basic`, `earned_da`, `earned_hra`, `earned_conveyance`, `earned_other`, `ot_pay`, `incentive_pay`, `gross`, `code_wages`, `epf_wage`, `eps_wage`, `edli_wage`, `ee_pf`, `er_eps`, `er_epf`, `edli`, `pf_admin`, `esi_covered`, `esi_wage`, `ee_esi`, `er_esi`, `pt`, `tds`, `advance`, `other_deduction_amt`, `total_deductions`, `net_pay`, `employer_cost`, `warnings` (JSON list of codes), `engine_version`.
- Later edits to rates, employees or settings must **never** change a finalised line.

### 4.10 Statutory tables (platform level, no company)
`StatutoryRate(key, value, effective_from, note)`; `PTSlab(state, gender, from_amount, to_amount|null, amount, february_amount, effective_from)`; `TaxConfig(tax_year, regime, std_deduction, rebate_limit, rebate_max, cess_pct, slabs JSON, surcharge JSON, senior_exemption, super_senior_exemption)`; `DueDate(item, day_of_next_month, note)`. Lookup rule: use the row in force on the **last day** of the payroll month. If any `StatutoryRate` changed **inside** the month, add warning `RATE_CHANGED_IN_MONTH` (such a month needs a split calculation — e.g., September 2026 — which v1 does not do).

### 4.11 Letters
`LetterTemplate(company, letter_type, body, updated_at)` — one per type per company, created from defaults (§12).
`IssuedLetter(company, employee, letter_type, ref_no, issue_date, body_snapshot_html, issued_by)` — reprint shows exactly what was issued.

### 4.12 AuditLog
`company` (nullable), `user`, `action`, `object_type`, `object_id`, `details` (JSON with before/after for edits), `ip`, `created_at`. Logged: login, employee create/edit/relieve, salary revision, opening balance, advance, upload, finalise, reopen, letter issue, settings change.

---

## 5. Screens

### 5.1 Layout on every page
- **Header band**: app name (serif) on the left; company trade name and "Logout" on the right; thick maroon line below.
- **Menu** (plain text links in one row, wraps on phones): `Home · Employees · Monthly Salary · Advances · Letters · Reports · Settings · Help`. The current item is underlined and bold.
- **Page title** (H1) + one-line instruction in grey.
- **Messages** box under the title (green = done, yellow = check this, red = problem).
- **Footer**: "Need help? Call {SUPPORT_PHONE}".

### 5.2 URL map
```
/login/  /logout/  /password/change/  /password/forgot/
/                                      Home (dashboard)
/employees/  add/  <id>/  <id>/edit/  <id>/relieve/  <id>/undo-relieve/  <id>/salary/revise/
/employees/upload/  /employees/upload/sample.xlsx  /employees/export.xlsx
/organisation/   /departments/   /designations/
/salary/                               list of months
/salary/<yyyy-mm>/                     month page (steps 1–5)
/salary/<yyyy-mm>/sheet.xlsx           pre-filled attendance sheet
/salary/<yyyy-mm>/upload/              upload + validation result
/salary/<yyyy-mm>/errors.xlsx          the uploaded sheet with mistakes marked
/salary/<yyyy-mm>/line/<id>/edit/      edit one employee's inputs (draft only)
/salary/<yyyy-mm>/finalise/            confirmation page
/salary/<yyyy-mm>/reopen/              confirmation page with reason box
/salary/<yyyy-mm>/payslips/            all payslips, print view
/salary/<yyyy-mm>/payslip/<line_id>/   one payslip, print view
/salary/<yyyy-mm>/register/            salary register, print view (+ .xlsx)
/salary/<yyyy-mm>/bank-sheet.xlsx  /salary/<yyyy-mm>/cash-list/
/salary/<yyyy-mm>/ecr.txt  /salary/<yyyy-mm>/esi.xlsx  /salary/<yyyy-mm>/export.xlsx
/advances/  add/  <id>/
/letters/  new/<type>/  <id>/  templates/<type>/
/reports/  cost/  pf/<yyyy-mm>/  esi/<yyyy-mm>/  pt/<yyyy-mm>/  tds/<tax-year>/  advances/  joiners-leavers/  increments/
/settings/company/  /settings/payroll/  /settings/opening-balances/ (+ upload, sample.xlsx)
/help/
/files/logo/  /files/upload/<run_id>/   private file views
/admin/                                 platform admin only
```

### 5.3 Home (dashboard)
1. **"This month" box** for the first not-finalised month: the five steps with ✔ marks and a big button for the next step ("Download November sheet").
2. **Four plain boxes**: Active employees · Last finalised month · Net pay last month · Total cost last month.
3. **Cost table with a three-way toggle** (links, work without JS: `?view=net|gross|ctc`):
   `[ In-hand pay ] [ Gross salary ] [ Total cost to company ]` → last 12 finalised months: Month · Employees paid · Amount · simple bar (a `<div>` whose width is a % of the largest month).
4. **"PF and ESI this month"** table: Employee PF, Employer EPS, Employer EPF, EDLI, PF admin, **PF challan total**; Employee ESI, Employer ESI, **ESI challan total**; Professional tax; TDS. Plus a "This tax year so far" row for employer PF and employer ESI.
5. **Reminders** from `DueDate`: "Pay November salary by 7 December", "Deposit PF by 15 December", etc.
6. **Things to fix** (each a link): PF members without UAN; ESI-covered staff without IP number; active staff with no appointment letter issued; salary structures where allowances are more than half of pay; employees turning 58 in the next 3 months; open advances with no instalment set.

### 5.4 Employees
- **List**: search box (name/code/mobile), filter Active / Relieved / All and Department. Columns: Code, Name, Designation, Department, Date of joining, Monthly gross, PF, ESI, Status. Buttons: `Add employee`, `Upload from Excel` (with `Download sample file` beside it), `Download list (Excel)`.
- **Add / Edit form** (one column, grouped with headings: Personal · Job · Salary · PF/ESI/Tax · Bank · Documents). Salary fields appear on *Add* only; later changes go through **Revise salary** so history is kept. Help text under every field in plain words.
- **View page** (sections, not JS tabs): Details · Salary history · Advances · Payslips · Letters issued. Buttons: `Edit`, `Revise salary`, `Give advance`, `Issue appointment letter`, `Relieve employee`.
- **Relieve**: date of leaving (last working day), reason, resignation date (if resigned), remarks → status `RELIEVED`. The employee is still included in the month of leaving. Show: "Under the Code on Wages, final dues must be paid within two working days of leaving." `Undo relieve` allowed only if no finalised month after the leaving date exists.
- **Revise salary**: effective month (cannot be a finalised month), new Basic / DA / HRA / Conveyance / Other allowance, reason. Shows old vs new and the change in monthly gross.

### 5.5 Organisation (hierarchy)
Indented tree from *Reports to*: top level = staff who report to nobody (shown under the company name). Each node: `Name — Designation (Code)`. Relieved staff hidden. `Print` button. Reject loops on save ("Ramesh cannot report to someone who reports to him").

### 5.6 Monthly Salary
- **Months list**: one row per month from `payroll_start_month` to the current month + 1: Month · Status (Not started / Draft / Finalised) · Employees · Net pay · Total cost · Open.
- **Month page**: the five steps as numbered boxes.
  1. `Download November 2026 sheet` (pre-filled, §6.3) — plus `What do the columns mean?` link to the Help section.
  2. Upload form (single file input + `Upload and check` button) + `Download sample file`.
  3. **Review table** (shown after a good upload): one row per employee, sticky first two columns, horizontal scroll inside a box, totals row at the bottom with a double top border. Columns: Code · Name · Paid days · LOP · Gross · PF (emp) · ESI (emp) · PT · TDS · Advance · Other ded. · Net pay · Employer PF+EDLI+admin · Employer ESI · Total cost · Notes. A checkbox "Show only rows with notes". Each row has an `Edit` link (draft only). Links: `Download this table (Excel)`.
  4. `Finalise November 2026` → confirmation page with totals and the sentence "After finalising, the month is locked. Payslips and files will use these figures." → `Yes, finalise` / `Go back`.
  5. After finalising: buttons `Print all payslips`, `Print salary register`, `Bank transfer sheet (Excel)`, `Cash payment list (print)`, `PF ECR file`, `ESI upload file`, `Reopen this month`.
- Months must be finalised in order. Only the **latest** finalised month can be reopened; reopening needs a typed reason and increments `revision_no` (payslips then show "Revised").

### 5.7 Advances
List (Employee · Date · Amount · Instalment · Recovered · Outstanding · Status), `Give advance` form, detail page with the recovery history. Owner can change the instalment of an open advance.

### 5.8 Letters
Choose letter type → choose employee (appointment: active staff; relieving/experience: relieved staff) → **preview on A4** → optional "Change the text for this letter only" (a textarea with the rendered text) → `Save and print` (assigns the reference number, stores the snapshot) → print view with a choice "1 copy / 2 copies (office copy + employee copy)". History table with `Reprint`. `Edit default text` per letter type, with the list of placeholders and a `Reset to original text` button.

### 5.9 Reports
A plain list of links (§10.2). Every report has `Print` and `Download Excel` where it makes sense.

### 5.10 Settings
`Company details` (§4.1 identity fields + logo), `Payroll rules` (§4.1 payroll fields, each with a one-sentence explanation), `Opening balances` (form per employee + Excel upload with sample), `Departments`, `Designations`, `Change password`.

### 5.11 Help
Short questions and answers in plain words, e.g. "How do I pay this month's salary?", "What is LOP?", "Why is PF only on ₹25,000?", "Why does ESI continue after a raise?", "Old or new tax regime?", "I made a mistake after finalising", "What must an appointment letter contain?". Each answer ≤ 5 lines.

### 5.12 Platform admin (`/admin/`)
Django admin, superuser only: Companies (with an action "Create owner login" that sets a temporary password and `must_change_password`), Users (reset password, unlock), StatutoryRate, PTSlab, TaxConfig, DueDate, AuditLog (read-only). Admin edits to statutory tables are themselves audit-logged.

---

## 6. Excel files (download sample → fill → upload)

### 6.1 Rules for every template and upload
- Format `.xlsx` only, ≤ 5 MB. Reject `.xls`, `.xlsm`, `.csv` with the message "Please save the file as Excel Workbook (.xlsx) and upload again."
- Sheet 1 = data (header in row 1, data from row 2). Sheet 2 = **"How to fill"**: one line per column (English, then a short Hindi line underneath). A hidden sheet **`_meta`** holds `template_type`, `template_version`, `company_code` and (for monthly sheets) `month`. Upload checks these and explains mismatches ("This sheet is for October 2026, but you are uploading for November 2026").
- Header cells bold with a light fill; pre-filled columns grey; column widths set; first row frozen; numbers formatted `#,##0`; Yes/No, Gender, Regime and similar columns get Excel **drop-down lists** (data validation).
- Parsing is forgiving: trims spaces; accepts `12,000`, `₹12000`, `12000.0`; dates as real Excel dates or text `DD-MM-YYYY`, `DD/MM/YYYY`; Yes/No accepts `Yes/No/Y/N/1/0/True/False` in any case; blank optional numbers = 0.
- Rows with an empty first column are ignored. A row whose first cell is `EXAMPLE` is ignored (the sample file has one example row).
- **All or nothing**: if any row has a mistake, nothing is saved. The page shows a table *Row · Employee · Column · Problem* and a link **"Download my sheet with mistakes marked"** (the same file, bad cells filled red with a comment, plus an `Errors` column at the end).
- Uploading again for a draft month replaces the earlier inputs.
- Use `openpyxl.load_workbook(path, read_only=True, data_only=True)`; never evaluate macros.

### 6.2 Employee upload template (`employees_sample.xlsx`)
| Column header (exact text) | Required | Format / allowed values |
|---|---|---|
| Emp Code | yes | text, unique |
| First Name | yes | text |
| Surname | | text |
| Relation (Father/Mother/Spouse) | yes | drop-down |
| Relation Name | yes | text |
| Gender (M/F/O) | yes | drop-down |
| Date of Birth | yes | DD-MM-YYYY |
| Date of Joining | yes | DD-MM-YYYY |
| Mobile | | 10 digits |
| Email | | |
| Address | | |
| Department | | text; created if new |
| Designation | yes | text; created if new |
| Reports To (Emp Code) | | must exist in the sheet or the company |
| Employment Type | | drop-down (§4.4) |
| Skill Category | | drop-down (§4.4) |
| Nature of Duties | | text |
| PAN | | `ABCDE1234F` |
| Aadhaar (only with consent) | | 12 digits |
| Aadhaar Consent (Yes/No) | | required Yes if Aadhaar given |
| UAN | | 12 digits |
| ESIC IP Number | | 10 digits |
| Payment Mode | | Bank / Cash / Cheque / UPI |
| Bank Name, Bank Account No, IFSC | | required if Payment Mode = Bank |
| Basic | yes | ₹ per month |
| DA, HRA, Conveyance, Other Allowance | | ₹ per month, default 0 |
| PF Member (Yes/No) | | default Yes if company PF is on |
| EPS Member (Yes/No) | | default by rule in §4.4 |
| ESI (Auto/Yes/No) | | default Auto |
| Person with Disability (Yes/No) | | default No |
| PT Exempt (Yes/No) | | default No |
| Tax Regime (New/Old) | | default = company default |
| Old Regime Deductions per Year | | ₹, only for Old |

Upload creates employees and their first `SalaryRevision` (effective from the joining month, or `payroll_start_month` if they joined earlier). Existing Emp Codes are **updated** (except salary — use Revise salary), and the result page says "12 added, 3 updated".

### 6.3 Monthly attendance and pay sheet (`<CODE>_salary_<yyyy-mm>.xlsx`)
Pre-filled with every employee to be paid that month (joined on or before month end; not left before month start). Title row is **not** used — row 1 is the header.

| Col | Header (exact text) | Pre-filled? | Meaning |
|---|---|---|---|
| A | Emp Code | yes (grey) | do not change |
| B | Employee Name | yes (grey) | for reading only |
| C | Designation | yes (grey) | for reading only |
| D | Days in Month | yes (grey) | 28–31 |
| E | Days Present | | 0–31, halves allowed (e.g. 25.5) |
| F | Paid Leave Days | | halves allowed |
| G | Weekly Off + Holidays | | paid weekly offs and paid holidays |
| H | Absent / LOP Days | | leave **blank** to auto-calculate = D − (E+F+G) |
| I | OT Hours | | overtime hours this month |
| J | OT Amount ₹ (optional) | | if filled, used instead of OT Hours |
| K | Incentive / Bonus ₹ | | one-time extra pay this month |
| L | Increment ₹ per month | | raise added to Basic **from this month** |
| M | Advance Deduction ₹ | yes (suggested) | pre-filled with the instalment due; change if needed |
| N | Other Deduction ₹ | | e.g. canteen; needs a reason |
| O | Other Deduction Reason | | text |
| P | TDS Override ₹ (optional) | | only if your CA gave a TDS figure |
| Q | Remarks | | shown in the register |

**Errors (block the upload):** unknown Emp Code; same code twice; an expected employee missing from the sheet ("These employees are missing: …; add them with 0 days or mark them relieved"); a value that is not a number; negative numbers; days not a multiple of 0.5; E+F+G+H ≠ D when H is filled, or E+F+G > D; paid days more than days employed in the month (joined on 16th → at most 15); Advance Deduction more than the outstanding advance; Other Deduction without a reason; Increment negative (use Revise salary for a cut); deductions more than earnings.
**Warnings (allowed, shown in Notes):** see §8.11.

### 6.4 Opening balances template (`opening_balances_sample.xlsx`)
Columns: Emp Code · Tax Year (e.g. 2026-27) · Gross Paid This Year Before Start Month ₹ · TDS Deducted ₹ · Professional Tax Deducted ₹ · Income From Previous Employer ₹ · TDS By Previous Employer ₹. Pre-filled with active employees.

---

## 7. Month workflow and locking

```
NOT STARTED ──upload ok──▶ DRAFT ──finalise──▶ FINALISED
                 ▲  re-upload / edit row  │           │
                 └───────────────────────┘◀──reopen (latest month only, with reason)
```
- **Finalise** (one database transaction): recompute every line (inputs may have been edited), freeze results, store `rates_snapshot`, create `AdvanceRecovery` rows, create `SalaryRevision` rows (source `MONTHLY_SHEET`, effective this month) for every non-zero increment, set status, write audit log.
- **Reopen**: delete the recoveries and revisions created by that finalise, set status `DRAFT`, `revision_no += 1`, store the reason, audit.
- Payslips and reports are available for finalised months only (draft shows a grey "DRAFT — not final" watermark on any print preview).

---

## 8. Calculation rules (what `payroll/engine.py` must do)

Appendix A is the exact reference. In words:

### 8.1 Days
- `days_in_month` = 28/29/30/31.
- `LOP` (loss of pay days) = the sheet value, or if blank `days_in_month − (present + paid leave + week offs/holidays)`.
- `paid_days = days_in_month − LOP`.
- `factor = max(0, 1 − LOP ÷ divisor)`, where divisor = days in month (`CALENDAR`, default), 30 (`FIXED_30`) or 26 (`FIXED_26`).
- `NCP days` (for the PF file) = LOP minus days before joining and after leaving, rounded **down** to a whole day.

### 8.2 Earnings
- Monthly structure in force for the month (§4.5) **plus the increment** from the sheet (added to Basic).
- Each component earned = `round(monthly component × factor)`: Basic, DA, HRA, Conveyance, Other allowance.
- **Overtime**: if *OT Amount* is filled use it; else `round((Basic + DA + Other) ÷ ot_days_divisor ÷ ot_hours_per_day × ot_multiplier × OT hours)`. Defaults 26 days, 8 hours, ×2 (the Codes require overtime at twice the ordinary rate).
- Incentive/Bonus as entered.
- `gross` = sum of all the above.

### 8.3 Labour Code "wages" (the 50% rule) — drives PF and ESI
Since 21 Nov 2025 the four Labour Codes use one definition of wages: Basic + DA + retaining allowance + any pay that is not in the excluded list. HRA, conveyance, overtime, commission/bonus and employer contributions are excluded — but only up to half of total pay; any excess counts as wages.
- *Wage part* = earned Basic + DA + Other allowance ("other/special allowance" is not in the exclusion list, so it is treated as wages).
- `code_wages = round(max(wage part, 50% × gross))`.
- If the **monthly structure** has wage part below 50% of gross, add note `STRUCTURE_BELOW_50` ("Allowances are more than half of the salary; PF/ESI are worked out on half of the gross. Consider raising Basic.").
- Note for the decision log: Ministry FAQs count employer PF inside "total remuneration" for the 50% test; v1 uses gross pay without employer contributions to avoid a circular calculation. Keep this switchable in `engine.py` (one function).

### 8.4 Provident Fund (only if company `pf_enabled` and employee `pf_member`)
- PF ceiling = **₹25,000** for months from October 2026 (₹15,000 until 16-09-2026; S.O. 5109(E)).
- `epf_wage = min(code_wages, ceiling)` if `pf_restrict_to_ceiling`, else `code_wages`.
- `eps_wage = min(code_wages, ceiling)` if `eps_member` and age on the 1st of the month < 58, else 0 (note `EPS_STOPPED_AGE_58`).
- `edli_wage = min(code_wages, ceiling)`.
- Employee PF = `round(12% × epf_wage)` (10% if the company uses 10%).
- Employer total = `round(12% × epf_wage)`; **EPS** = `round(8.33% × eps_wage)`; **Employer EPF** = employer total − EPS (so a non-EPS member gets the full 12% in EPF).
- **EDLI** = `round(0.5% × edli_wage)`; **Admin charges** = `round(0.5% × epf_wage)` per person; for the whole establishment the month's admin charge is `max(sum, ₹500)` if at least one member contributed (store the top-up on the run and add it to company cost).
- Check numbers (EPFO FAQ): PF wages ₹25,000 → employee 3,000 · EPS 2,083 · employer EPF 917 · EDLI 125 · admin 125. ₹20,000 → 2,400 · 1,666 · 734 · 100 · 100.

### 8.5 ESI (only if company `esi_enabled`)
- Applies to establishments with 10 or more persons in notified areas — the owner switches it on. Rates: employee **0.75%**, employer **3.25%**, **each rounded up** to the next rupee.
- **Wages for ESI** = `code_wages` (ESIC applies the Code wage definition from the December 2025 payroll). A platform switch `ESI_ON_CODE_WAGES` (§9) set to 0 makes it use gross instead.
- **Coverage is fixed for a contribution period** (1 Apr–30 Sep, 1 Oct–31 Mar): covered if the *full-month* code wages of the salary structure in force at the period start (or at joining, if later) are ≤ **₹21,000** (₹25,000 for a person with disability). A raise in the middle of a period does not stop ESI until the period ends. `esi_override` YES/NO wins over AUTO.
- **Employee share waived** when average daily wage (`esi_wage ÷ paid_days`) ≤ **₹176**; the employer still pays (note `ESI_EMPLOYEE_SHARE_WAIVED`).
- Check numbers: ₹21,000 → employee 158, employer 683; ₹15,000 → 113, 488.

### 8.6 Professional tax (only if company `pt_enabled` and employee not `pt_exempt`)
- Slab lookup on the month's **gross** using `PTSlab` for the company's state and the employee's gender; February uses `february_amount`.
- Seeded: **Karnataka** — gross ₹25,000 and above → ₹200, February ₹300 (₹2,500 a year). **Maharashtra** — men: ₹7,501–10,000 → ₹175; above ₹10,000 → ₹200 (Feb ₹300); women: up to ₹25,000 nil, above → ₹200 (Feb ₹300). Other states: the admin adds slabs; until then the PT switch shows "No PT slabs set for your state — PT will be 0".

### 8.7 Income tax (TDS) — only if company `tds_enabled`
- Each employee chooses **New** (default) or **Old** regime; the owner records it on the employee page.
- Tax year 2026-27 (April 2026–March 2027), under the Income-tax Act, 2025 in force from 1 April 2026; slabs unchanged by Budget 2026:
  - **New**: 0–4 L nil · 4–8 L 5% · 8–12 L 10% · 12–16 L 15% · 16–20 L 20% · 20–24 L 25% · above 24 L 30%. Standard deduction ₹75,000. No tax if taxable income ≤ ₹12 lakh (rebate up to ₹60,000) with marginal relief just above ₹12 lakh. Cess 4%. Surcharge 10%/15%/25% above 50 L/1 Cr/2 Cr with marginal relief.
  - **Old**: 0–2.5 L nil · 2.5–5 L 5% · 5–10 L 20% · above 10 L 30% (age 60+: nil up to 3 L; 80+: nil up to 5 L). Standard deduction ₹50,000; professional tax deductible; plus the owner-entered *old-regime deductions per year* (80C including PF, 80D, HRA exemption, home-loan interest — one total). Rebate: no tax if taxable ≤ ₹5 lakh. Cess 4%.
- **Monthly TDS** = `round((annual tax on projected income − TDS already deducted this tax year) ÷ months left including this one)`, never below 0.
  Projected income = gross already paid this tax year (finalised months + opening balance + previous employer) + this month's gross + this month's structure gross × remaining months.
  `payroll/service.py` builds the engine's `YTD` as: `gross` = opening `ytd_gross` + `prev_employer_income` + gross of finalised months in this tax year; `tds` = opening `ytd_tds` + `prev_employer_tds` + TDS of those months; `pt` = opening `ytd_pt` + PT of those months.
- *TDS Override* in the sheet replaces the calculation for that month.
- If `TaxConfig` for the tax year is missing, use the latest earlier year and add note `TAX_SLABS_NOT_UPDATED`.

### 8.8 Advances and other deductions
- Advance deduction ≤ outstanding (error otherwise). Other deduction needs a reason (the Code on Wages allows deductions for damage/loss only under its conditions; the Help page says so).
- Warning `DEDUCTIONS_ABOVE_50` if all deductions exceed 50% of gross (Code on Wages limit).

### 8.9 Net pay and cost
- `total_deductions = employee PF + employee ESI + PT + TDS + advance + other deduction`.
- `net_pay = gross − total_deductions` (error if negative).
- `employer_cost = gross + EPS + employer EPF + EDLI + PF admin + employer ESI`. Company total cost for the month = sum + PF admin top-up (§8.4).
- Dashboard toggle: **In-hand** = Σ net pay · **Gross** = Σ gross · **Total cost to company** = Σ employer cost + top-up.

### 8.10 Rounding table
| Item | Rule |
|---|---|
| Earned components, OT, PF (EE, EPS, total ER), EDLI, admin, PT, TDS, code wages | nearest rupee, 50 paise goes up (`ROUND_HALF_UP`) |
| Employer EPF | employer total − EPS (no separate rounding) |
| ESI employee and employer shares | next higher rupee (`ROUND_CEILING`), each separately |
| NCP days | whole days, rounded down |
| Display | Indian grouping `12,34,567`; dates `DD-MM-YYYY`; months "November 2026" |

### 8.11 Notes (warnings) shown in the review table
| Code | Plain message |
|---|---|
| `STRUCTURE_BELOW_50` | Allowances are more than half of salary; PF/ESI worked out on half of gross. |
| `EPS_STOPPED_AGE_58` | Employee is 58 or older: no pension (EPS); full employer share goes to PF. |
| `ESI_EMPLOYEE_SHARE_WAIVED` | Daily wage ₹176 or less: employee ESI not deducted; employer still pays. |
| `DEDUCTIONS_ABOVE_50` | Deductions are more than half of the salary. Check the advance/other deduction. |
| `NO_UAN` | PF member without UAN — will be left out of the ECR file. |
| `NO_ESIC_NUMBER` | ESI covered but no IP number — will be left out of the ESI file. |
| `JOINED_THIS_MONTH` / `LEFT_THIS_MONTH` | Information. |
| `INCREMENT_LARGE` | Increment is more than 50% of current salary — please confirm. |
| `RATE_CHANGED_IN_MONTH` | A statutory rate changed inside this month; check with your CA. |
| `TAX_SLABS_NOT_UPDATED` | Tax slabs for this year are not set; last year's were used. |
| `ZERO_PAY` | No paid days this month. |

---

## 9. Statutory seed data (data migration in `statutory/`)

### 9.1 StatutoryRate
| Key | Value | Effective from | Note |
|---|---|---|---|
| `PF_WAGE_CEILING` | 15000 | 2014-09-01 | |
| `PF_WAGE_CEILING` | 25000 | 2026-09-17 | S.O. 5109(E) dated 17-09-2026 |
| `PF_ER_RATE` | 12.00 | 2014-09-01 | EPS + EPF |
| `EPS_RATE` | 8.33 | 2014-09-01 | |
| `EDLI_RATE` | 0.50 | 2014-09-01 | |
| `PF_ADMIN_RATE` | 0.50 | 2018-06-01 | |
| `PF_ADMIN_MIN` | 500 | 2018-06-01 | per establishment per month |
| `EPS_MAX_AGE` | 58 | 2014-09-01 | |
| `ESI_EE_RATE` | 0.75 | 2019-07-01 | |
| `ESI_ER_RATE` | 3.25 | 2019-07-01 | |
| `ESI_CEILING` | 21000 | 2017-01-01 | |
| `ESI_CEILING_PWD` | 25000 | 2017-01-01 | |
| `ESI_EE_EXEMPT_DAILY_WAGE` | 176 | 2019-07-01 | value confirmed for 2026 |
| `ESI_ON_CODE_WAGES` | 0 | 2017-01-01 | 1 = use code wages |
| `ESI_ON_CODE_WAGES` | 1 | 2025-11-21 | ESIC circular, Dec 2025 payroll onward |
| `CODE_WAGE_MIN_PCT` | 0 | 2014-09-01 | |
| `CODE_WAGE_MIN_PCT` | 50 | 2025-11-21 | Labour Codes in force |

### 9.2 PTSlab
| State | Gender | From ₹ | To ₹ | Monthly | February | Effective from |
|---|---|---|---|---|---|---|
| KA | ANY | 25,000 | — | 200 | 300 | 2025-04-01 |
| MH | M and O | 7,501 | 10,000 | 175 | 175 | 2023-04-01 |
| MH | M and O | 10,001 | — | 200 | 300 | 2023-04-01 |
| MH | F | 25,001 | — | 200 | 300 | 2023-04-01 |

### 9.3 TaxConfig — seed tax years 2025 and 2026 with identical values (§8.7)
New: `std_deduction 75000`, slabs `[[400000,0],[800000,5],[1200000,10],[1600000,15],[2000000,20],[2400000,25],[null,30]]`, `rebate_limit 1200000`, `rebate_max 60000`, surcharge `[[5000000,10],[10000000,15],[20000000,25]]`, cess 4.
Old: `std_deduction 50000`, slabs `[[250000,0],[500000,5],[1000000,20],[null,30]]`, `senior_exemption 300000`, `super_senior_exemption 500000`, `rebate_limit 500000`, `rebate_max 12500`, surcharge `[[5000000,10],[10000000,15],[20000000,25],[50000000,37]]`, cess 4.

### 9.4 DueDate (reminders only)
Salary — 7th of next month (Code on Wages) · PF — 15th · ESI — 15th · TDS — 7th (March: 30 April) · PT Karnataka — 20th (verify). The admin can edit these.

---

## 10. Dashboard and reports

### 10.1 Dashboard — see §5.3. Numbers come only from **finalised** months.

### 10.2 Reports
| Report | Contents | Outputs |
|---|---|---|
| Salary register (month) | §11.2 | print, Excel |
| Payslips (month) | §11.1 | print all / one |
| Bank transfer sheet | Sl · Emp Code · Name · Bank · Account No · IFSC · Net Pay · Narration "Salary Nov-2026" (Bank/UPI staff only) | Excel |
| Cash payment list | Sl · Code · Name · Net Pay · Signature / Thumb impression (empty column) | print |
| PF statement (month) | per employee: UAN, gross, EPF/EPS/EDLI wages, EE, EPS, ER EPF, EDLI, admin, NCP; challan totals | print, Excel, **ECR text** (§13.1) |
| ESI statement (month) | IP no, days, ESI wage, EE, ER; challan total | print, Excel, **ESI file** (§13.2) |
| Professional tax (month) | per employee and slab-wise totals | print, Excel |
| TDS (tax year) | per employee × month, regime, YTD | Excel |
| Cost report (12 months) | toggle In-hand / Gross / Total cost; per-employee view for one month | print, Excel |
| Advances register | outstanding by employee | print, Excel |
| Joiners and leavers | date range | print, Excel |
| Increment history | all salary revisions in a date range | Excel |

---

## 11. Printing

General: separate `print.css` with `@page { size: A4; margin: 12mm; }`; hide menu/buttons when printing; black text on white; tables with 1px black borders; repeat table headers on every page (`thead { display: table-header-group; }`); a big `Print` button on screen that calls `window.print()`.

### 11.1 Payslip (follows **Form V, rule 52, Wages (Central) Rules, 2026**)
```
┌──────────────────────────────────────────────────────────────────┐
│ [logo] SHRI RAM KIRANA STORES               WAGE SLIP (Form V)   │
│        12, Main Road, Jayanagar, Bengaluru 560041                │
│        Date of issue: 01-12-2026   Wage period: 01-11-2026 to 30-11-2026
├──────────────────────────────────────────────────────────────────┤
│ Name: Sunita Devi            Father's/Mother's/Spouse: Ram Lal   │
│ Emp code: E002   Designation: Cashier   Date of joining: 01-07-2021
│ UAN: 101234567802   ESIC IP: 1234567802   PAN: ABCPD1234E          │
│ Bank A/c: 1234567890 (SBI)                                       │
│ Attendance: Days in month 30 · Paid days 27 · LOP 3 · OT hours 0 │
├──────────────────────────────┬───────────────────────────────────┤
│ RATE OF WAGES (per month)    │                                   │
│ Basic 9,000 · DA 0 · Allowances 9,000 (HRA 6,000, Conv. 2,000, Other 1,000)
├──────────────────────────────┼───────────────────────────────────┤
│ EARNINGS              ₹      │ DEDUCTIONS                ₹       │
│ Basic              8,100     │ Provident Fund         1,080      │
│ DA                     0     │ ESI                       68      │
│ HRA                5,400     │ Professional Tax           0      │
│ Conveyance         1,800     │ Income Tax (TDS)           0      │
│ Other allowance      900     │ Advance recovery           0      │
│ Overtime               0     │ Other                      0      │
│ Incentive / Bonus      0     │                                   │
│ GROSS WAGES       16,200     │ TOTAL DEDUCTIONS       1,148      │
├──────────────────────────────┴───────────────────────────────────┤
│ NET WAGES PAID: ₹ 15,052  (Rupees Fifteen Thousand Fifty-Two Only)│
│ Employer also paid: PF 1,080 · EDLI 45 · PF admin 45 · ESI 293   │
│ Advance balance after this month: ₹ 0                            │
│ This is a computer-generated wage slip.      Employer / Pay-in-charge
└──────────────────────────────────────────────────────────────────┘
```
Rules: one A4 per payslip by default; `payslip_two_per_page` prints two on one A4 (cut line between). "Revised" label if `revision_no > 0`. Bulk print = all finalised lines of the month in employee-code order, page break between payslips.

### 11.2 Salary register (A4 landscape)
Title: "Register of Wages — November 2026 — Shri Ram Kirana Stores". Columns: Sl · Code · Name · Designation · Paid days · LOP · Basic · DA · HRA · Conv. · Other · OT · Incentive · Gross · PF · ESI · PT · TDS · Advance · Other ded. · Total ded. · Net · Signature/Thumb. Totals row with double top border. Footer: "Prepared on {date} · Page x of y" and a signature line for the employer.

---

## 12. Letters

### 12.1 How templates work (safe, simple)
- Template text is **plain text**, not HTML. Blank line = new paragraph. The owner cannot insert HTML or code.
- Placeholders `{{ name }}` are replaced by a small whitelist-based function (regex `\{\{\s*([a-z_.]+)\s*\}\}`). Values are HTML-escaped. Unknown placeholders stay visible and produce a warning on the preview.
- Do **not** use Django's template engine for owner-edited text.
- Ready-made "line" placeholders avoid if/else in templates (e.g. `{{ letter.resignation_line }}` is either "This refers to your resignation letter dated 15-10-2026." or empty).
- Reference numbers: `<CODE>/APT/2026-27/0001`, `/REL/`, `/EXP/` — a running number per company, type and tax year.

### 12.2 Placeholders
Company: `company.legal_name`, `company.trade_name`, `company.address`, `company.city`, `company.phone`, `company.email`, `company.lin`, `company.signatory_name`, `company.signatory_designation`.
Employee: `emp.title` (Mr./Ms./Mx. from gender), `emp.full_name`, `emp.code`, `emp.relation_label` ("Father's name"), `emp.relation_name`, `emp.dob`, `emp.doj`, `emp.dol`, `emp.designation`, `emp.department`, `emp.address`, `emp.employment_type`, `emp.skill_category`, `emp.nature_of_duties`, `emp.payment_mode`, `emp.he_she`, `emp.his_her`, `emp.him_her`.
Salary (structure in force on the letter date): `salary.basic`, `salary.da`, `salary.hra`, `salary.conveyance`, `salary.other`, `salary.gross`, `salary.gross_words`.
Letter: `letter.ref_no`, `letter.date`, `letter.notice_days`, `letter.probation_line`, `letter.resignation_line`, `letter.particulars_table` (appointment only).

### 12.3 Appointment letter (default text)
The **particulars table is generated by code** and cannot be removed, because the OSH Code (section 6) and the OSH Central Rules notified on 8 May 2026 make an appointment letter with prescribed particulars compulsory for every employee (state rules — e.g. Rajasthan's — list the same 15 items).

Particulars table (auto):
| # | Particular | Filled from |
|---|---|---|
| 1 | Name of employee | full name |
| 2 | Date of birth | DOB |
| 3 | Father's / Mother's name | relation name |
| 4 | Aadhaar number (with consent) | masked `XXXX XXXX 1234`, or "Not given" |
| 5 | Labour Identification Number (LIN) of the establishment | company LIN |
| 6 | UAN / ESIC insurance number (if available) | UAN, IP no |
| 7 | Designation | designation |
| 8 | Category of skill | skill category |
| 9 | Date of joining | DOJ |
| 10 | Wages: Basic pay and Dearness Allowance | "Basic ₹ x + DA ₹ y per month" |
| 11 | Other allowances (including accommodation, if any) | HRA, conveyance, other |
| 12 | Avenue for achieving higher wages or higher position | company setting `higher_wages_avenue` (default: "Yearly review of work; increments and promotion to higher posts based on performance and vacancies.") |
| 13 | Social security (EPFO / ESIC) applicable | "EPF: Yes/No · ESI: Yes/No" |
| 14 | Broad nature of duties | nature of duties |
| 15 | Any other information | "Nature of employment: {type}." For women: "Maternity benefits under the Code on Social Security, 2020 apply." |

Default body (editable):
```
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
```

### 12.4 Relieving letter (default text)
```
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
```

### 12.5 Experience letter (default text)
```
Ref: {{ letter.ref_no }}                                        Date: {{ letter.date }}

TO WHOM IT MAY CONCERN

This is to certify that {{ emp.title }} {{ emp.full_name }} ({{ emp.relation_label }}: {{ emp.relation_name }}) worked with {{ company.legal_name }}, {{ company.city }}, as {{ emp.designation }} from {{ emp.doj }} to {{ emp.dol }}.

During this period {{ emp.his_her }} work and conduct were found to be good.

We wish {{ emp.him_her }} all the best.

For {{ company.legal_name }}


{{ company.signatory_name }}
{{ company.signatory_designation }}
```

### 12.6 Letter print layout
Letterhead at top (logo, trade name, address, phone, email); body in a serif font at 12pt; signature block bottom-left; "Page 1 of 1". "2 copies" prints the letter twice with "OFFICE COPY" / "EMPLOYEE COPY" in the top-right corner.

---

## 13. Government upload files

### 13.1 PF — ECR text file (`ECR_<CODE>_<yyyymm>.txt`)
- Plain text, UTF-8 without BOM, no header, one line per PF member, 11 fields joined by `#~#`, whole numbers only:
  `UAN#~#Name as per UAN#~#Gross wages#~#EPF wages#~#EPS wages#~#EDLI wages#~#EE share (EPF)#~#EPS contribution#~#ER share (EPF − EPS difference)#~#NCP days#~#Refund of advances`
- Name: capitals, only letters, spaces and dots. EPS wages and EPS contribution = 0 for non-EPS members. Refund of advances = 0.
- Members without a valid 12-digit UAN are left out and listed on screen: "Generate UAN on the EPFO portal for: …".
- Example (from the golden data):
  `101234567802#~#SUNITA DEVI#~#16200#~#9000#~#9000#~#9000#~#1080#~#750#~#330#~#3#~#0`

### 13.2 ESI — monthly contribution sheet (`ESI_<CODE>_<yyyymm>.xlsx`)
Columns: IP Number (10 digits) · IP Name · No. of Days for which wages paid/payable · Total Monthly Wages · Reason Code for Zero working days · Last Working Day. Days = paid days rounded down; wages = ESI wage. Reason code 0 when days > 0; 2 (left service) with last working day when the employee left in the month; 1 (on leave) when days = 0 otherwise. **Before going live, compare the columns with the template currently offered on the ESIC portal and adjust if it has changed.**

---

## 14. Old-fashioned style guide

| Thing | Rule |
|---|---|
| Colours | Paper `#FFFDF5` background; ink `#1B1B1B` text; ledger maroon `#8B1E1E` for header band, H1 and primary buttons; rule lines `#C9C2B0`; links `#0B3D91` underlined; success `#1E6B2F` on `#E8F3EA`; warning `#7A5A00` on `#FFF4CC`; error `#8B1E1E` on `#FBE9E9`. Define as CSS variables. |
| Fonts | Body: `system-ui, "Segoe UI", Roboto, "Noto Sans", "Noto Sans Devanagari", Arial, sans-serif` at **17px**, line-height 1.5. Headings: `Georgia, "Times New Roman", serif`. Numbers: `font-variant-numeric: tabular-nums`. No webfonts. |
| Buttons | Rectangle, 2px border, radius 2px, min height 44px, padding 10px 18px, bold sentence-case text. Primary = maroon fill/white text; secondary = white fill/maroon border. Never icon-only. |
| Tables | Full borders like a register; header row `#F3EEDF`; padding 6px 10px; numbers right-aligned; zebra rows `#FBF8EE`; **totals row bold with a double top border** (`border-top: 3px double #1B1B1B`). |
| Forms | Label above field; one column; `*` for required; help text under the field in grey; errors in red under the field and in a summary box at the top. |
| Layout | Max width 1100px centred; wide tables scroll inside their own box; works at 360px wide; no fixed sidebars. |
| Behaviour | No animations, no modals (use confirmation pages), no infinite scroll, no drag-and-drop, no auto-saving. |
| Words | Short sentences; "Save", "Upload and check", "Finalise", "Print". Wrap every UI string in `gettext` so Hindi can be added. |
| Accessibility | Visible focus outline (3px maroon); every input has a label; contrast ≥ 4.5:1. |

---

## 15. Security and privacy
- Django's password hashing, CSRF, `SECURE_*` settings on in production (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, HSTS, `X_FRAME_OPTIONS=DENY`).
- Tenant isolation as in §2.1, with tests.
- Show PAN, Aadhaar and bank account **masked** on lists (last 4 characters); full values only on the edit form (Aadhaar always masked, even there, after saving).
- Aadhaar is optional and stored only when consent is ticked.
- Uploaded sheets and logos are private and served through company-checked views.
- Upload limits: 5 MB, `.xlsx` only, parsed read-only.
- Audit log for every change listed in §4.12; the admin can read it.
- Keep payroll records at least **5 years**; no hard delete anywhere in the owner UI.
- Note for the business: India's Digital Personal Data Protection Act applies to employee data — collect only what is needed (the forms do not ask for anything beyond §4.4).

---

## 16. Seed and demo data (`python manage.py seed_demo`)

Creates two companies and their owner logins (passwords printed once, with a warning that they are for local testing only).

### 16.1 Shri Ram Kirana Stores — code `SRKS`, Jayanagar, Bengaluru, Karnataka
Settings: PF on (12%, restricted to ceiling), ESI on, PT on (KA), TDS on, divisor `CALENDAR`, OT ×2 / 26 days / 8 hours, `payroll_start_month` = November 2026. LIN `DEMO-LIN-0001`. Departments: Store, Accounts, Delivery. A draft **November 2026** run is created from the inputs below (as if the sheet was uploaded).

| Code | Name | Gender | DOB | DOJ | Designation (reports to) | Monthly structure | Flags | Nov-2026 sheet inputs |
|---|---|---|---|---|---|---|---|---|
| E001 | Ramesh Kumar | M | 15-06-1995 | 01-04-2022 | Helper (E003) | Basic 12,000 | PF, EPS | Present 26, WO/H 4, OT 10 h, Increment 500, Advance 1,000 (advance of 5,000 given 10-10-2026, instalment 1,000) |
| E002 | Sunita Devi | F | 20-02-1990 | 01-07-2021 | Cashier (E003) | Basic 9,000, HRA 6,000, Conv 2,000, Other 1,000 | PF, EPS | Present 22, Paid leave 1, WO/H 4, LOP 3 |
| E003 | Mohd. Arif | M | 05-11-1985 | 10-01-2018 | Store Manager (—) | Basic 20,000, HRA 10,000, Other 10,000 | PF, EPS | Present 26, WO/H 4 |
| E004 | Priya Sharma | F | 30-08-1992 | 01-04-2026 | Accountant (E007) | Basic 60,000, HRA 30,000, Other 20,000 | PF, **no EPS**, New regime; opening YTD gross 7,70,000, TDS 27,300, PT 1,400 | Present 26, WO/H 4 |
| E005 | Gopal Rao | M | 10-05-1967 | 01-08-2015 | Senior Salesman (E003) | Basic 14,000, HRA 4,000, Other 2,000 | PF, EPS (age 59 → stops) | Present 26, WO/H 4 |
| E006 | Kiran B | M | 25-01-2003 | **16-11-2026** | Delivery Boy (E003) | Basic 8,000, HRA 6,000, Conv 4,000 | PF, EPS | Present 13, WO/H 2, LOP blank |
| E007 | Neha Joshi | F | 12-03-1988 | 01-06-2024 | Branch Manager (—) | Basic 50,000, HRA 25,000, Other 15,000 | PF, **no EPS**, **Old regime**, deductions 2,00,000/yr; opening YTD gross 6,30,000, TDS 47,320, PT 1,400 | Present 26, WO/H 4 |

UANs `1012345678` + `01`…`07`; ESIC IP numbers `12345678` + `01`…`07` for ESI-covered staff.

### 16.2 Annapurna Sweets — code `ANPS`, Pune, Maharashtra
PF **off**, ESI on, PT on (MH), TDS on. A001 Seema Patil (F, Basic 15,000), A002 Rahul More (M, Basic 9,500), both full attendance in November 2026. Used to show isolation and Maharashtra PT.

---

## 17. Tests

Run with `python manage.py test`. All must pass before calling any step done.

### 17.1 Golden numbers — Shri Ram Kirana Stores, November 2026 (30 days)
| Code | Paid days | LOP | NCP | Gross | Code wages | EPF wage | EPS wage | EE PF | ER EPS | ER EPF | EDLI | Admin | ESI wage | EE ESI | ER ESI | PT | TDS | Advance | Total ded. | **Net pay** | **Employer cost** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E001 | 30 | 0 | 0 | 13,702 | 12,500 | 12,500 | 12,500 | 1,500 | 1,041 | 459 | 63 | 63 | 12,500 | 94 | 407 | 0 | 0 | 1,000 | 2,594 | **11,108** | **15,735** |
| E002 | 27 | 3 | 3 | 16,200 | 9,000 | 9,000 | 9,000 | 1,080 | 750 | 330 | 45 | 45 | 9,000 | 68 | 293 | 0 | 0 | 0 | 1,148 | **15,052** | **17,663** |
| E003 | 30 | 0 | 0 | 40,000 | 30,000 | 25,000 | 25,000 | 3,000 | 2,083 | 917 | 125 | 125 | — | 0 | 0 | 200 | 0 | 0 | 3,200 | **36,800** | **43,250** |
| E004 | 30 | 0 | 0 | 1,10,000 | 80,000 | 25,000 | 0 | 3,000 | 0 | 3,000 | 125 | 125 | — | 0 | 0 | 200 | 3,900 | 0 | 7,100 | **1,02,900** | **1,13,250** |
| E005 | 30 | 0 | 0 | 20,000 | 16,000 | 16,000 | 0 | 1,920 | 0 | 1,920 | 80 | 80 | 16,000 | 120 | 520 | 0 | 0 | 0 | 2,040 | **17,960** | **22,600** |
| E006 | 15 | 15 | 0 | 9,000 | 4,500 | 4,500 | 4,500 | 540 | 375 | 165 | 23 | 23 | 4,500 | 34 | 147 | 0 | 0 | 0 | 574 | **8,426** | **9,733** |
| E007 | 30 | 0 | 0 | 90,000 | 65,000 | 25,000 | 0 | 3,000 | 0 | 3,000 | 125 | 125 | — | 0 | 0 | 200 | 6,760 | 0 | 9,960 | **80,040** | **93,250** |
| **Total** | | | | **2,98,902** | | | | **14,040** | **4,249** | **9,791** | **586** | **586** | | **316** | **1,367** | **600** | **10,660** | **1,000** | **26,616** | **2,72,286** | **3,15,481** |

Also assert: E001 OT pay 1,202 and earned Basic 12,500 (increment applied); E002 earned Basic 8,100 / HRA 5,400 / Conv 1,800 / Other 900; E005 warning `EPS_STOPPED_AGE_58`; E006 warning `STRUCTURE_BELOW_50`; establishment PF admin 586 (no top-up); PF challan total 29,252; ESI challan total 1,683; ECR line for E002 exactly as in §13.1; E003 ECR line `101234567803#~#MOHD ARIF#~#40000#~#25000#~#25000#~#25000#~#3000#~#2083#~#917#~#0#~#0`.

### 17.2 Edge cases (each one test)
| Case | Expected |
|---|---|
| E002 with LOP left blank | LOP auto 3; same results as above |
| E002 with divisor `FIXED_26` | Basic 7,962 · HRA 5,308 · Conv 1,769 · Other 885 · gross 15,924 · code wages 8,847 · EE PF 1,062 · EE ESI 67 · net 14,795 |
| E002 with `ESI_ON_CODE_WAGES = 0` | ESI wage 16,200 · EE 122 · ER 527 |
| E003 with `pf_restrict_to_ceiling = False` | EPF wage 30,000 · EE 3,600 · EPS 2,083 · ER EPF 1,517 · EDLI 125 · admin 150 |
| Basic 4,500, full month, ESI covered | EE ESI 0 (waived) · ER ESI 147 |
| E003 in February 2027 (Karnataka) | PT 300 |
| E004 in March 2027 (YTD gross 12,10,000, YTD TDS 42,900) | TDS 3,900 |
| E004 in April 2026, no YTD | TDS 3,900 |
| Karnataka gross 24,999 / 25,000 | PT 0 / 200 |
| Maharashtra: M 7,500 → 0; M 9,000 → 175; M 15,000 → 200 (Feb 300); F 20,000 → 0; F 30,000 Feb → 300 | as stated |
| E002 full attendance with OT Amount 750 (and OT Hours 5) | OT pay 750 (amount wins) · gross 18,750 · code wages 10,000 |
| Present 28 + WO/H 4 in a 30-day month | error "days do not add up" |
| Annapurna: Seema (F, 15,000, MH) / Rahul (M, 9,500, MH), PF off | Seema: ESI 113/488, PT 0, net 14,887 · Rahul: ESI 72/309, PT 175, net 9,253 |
| Annual tax (new): 12,00,000 → 0 · 12,45,000 → 46,800 · 12,75,000 → 74,100 · 16,00,000 → 1,24,800 · 25,00,000 → 3,43,200 · 50,10,000 → 11,33,600 · 55,00,000 → 14,07,120 | as stated |
| Annual tax (old): 5,00,000 → 0 · 8,27,500 → 81,120 (age 38) / 78,520 (age 62) · 12,00,000 → 1,79,400 | as stated |
| Amount in words: 0 → "Rupees Zero Only"; 21 → "Rupees Twenty-One Only"; 11,108 → "Rupees Eleven Thousand One Hundred Eight Only"; 1,23,456 → "Rupees One Lakh Twenty-Three Thousand Four Hundred Fifty-Six Only"; 12,34,56,789 → "Rupees Twelve Crore Thirty-Four Lakh Fifty-Six Thousand Seven Hundred Eighty-Nine Only" | exact strings |
| Indian format: 1234567 → `12,34,567`; 999 → `999`; 100000 → `1,00,000` | exact |

### 17.3 Behaviour tests
- **Isolation**: logged in as SRKS owner, every detail/edit/print/file URL of ANPS objects returns 404; lists never show ANPS rows.
- **Excel round trip**: download the November sheet → fill with §16.1 inputs using openpyxl in the test → upload → lines equal §17.1.
- **Upload errors**: unknown code, duplicate code, missing employee, wrong month in `_meta`, days not adding up, advance more than outstanding → nothing saved and the right messages.
- **Finalise/reopen**: finalise creates 1 recovery (E001, 1,000) and 1 salary revision (E001 Basic 12,500 from Nov 2026); advance outstanding becomes 4,000; reopen removes both and outstanding returns to 5,000; finalising out of order is refused.
- **Snapshot**: after finalising, changing `PF_WAGE_CEILING` or E002's structure does not change the November lines.
- **Letters**: appointment letter for E002 contains all 15 particulars, the maternity sentence, masked Aadhaar and the next reference number `SRKS/APT/2026-27/0001`; unknown placeholder shows a warning.
- **Hierarchy**: setting E003 to report to E001 (who reports to E003) is refused.
- **Login lockout** after 5 wrong passwords.

---

## 18. Running and deploying

### 18.1 Local
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # DEBUG=True, no POSTGRES_* → SQLite
python manage.py migrate        # includes statutory seed data
python manage.py seed_demo
python manage.py createsuperuser
python manage.py runserver
python manage.py test
```

### 18.2 Server (one small VPS, 1 vCPU / 2 GB RAM is enough to start)
- `docker compose up -d` with three services: `db` (postgres:16, named volume), `web` (Gunicorn, 3 workers, runs `migrate` and `collectstatic` on start), `caddy` (automatic HTTPS for the domain in `Caddyfile`, `request_body max_size 10MB`).
- Media folder on a named volume, **not** served by Caddy.
- Nightly backup: `pg_dump` + tar of media → keep 30 days locally and copy off the server (e.g., to object storage). Write a short restore note in `README_RUN.md`.
- `DEBUG=False`, strong `SECRET_KEY`, `ALLOWED_HOSTS` set, time zone Asia/Kolkata.

---

## 19. Build order and definition of done

| Step | Build | Done when |
|---|---|---|
| 1 | Project skeleton, settings, custom User, `core` (CompanyOwned, TenantMiddleware, CompanyScopedMixin, AuditLog), base templates and CSS, login/logout/change password/lockout, admin for Company and User | can log in as owner and as admin; isolation test scaffold passes |
| 2 | `statutory` app with seed migration and `rates.py` lookup (value in force on a date; detects in-month changes) | lookup tests pass |
| 3 | `employees`: departments, designations, employee CRUD, view page, relieve/undo, salary revisions, org chart, opening balances; employee Excel sample + upload | create/edit/upload/hierarchy tests pass |
| 4 | `payroll/engine.py` = Appendix A (rates passed in, no DB access) | §17.1 and §17.2 engine tests pass |
| 5 | `payroll/service.py` (DB → engine → lines), months list, month page, pre-filled sheet, upload and validation with error file, review table, line edit, finalise, reopen; `advances` | round-trip, errors, finalise/reopen, snapshot tests pass |
| 6 | Payslips (single, bulk, two-per-page), salary register, bank sheet, cash list | print previews fit A4; numbers match |
| 7 | Dashboard with toggle, reports list, PF statement + ECR, ESI statement + file, PT, TDS, cost, advances, joiners/leavers, increments, Excel exports | ECR lines match §13.1/§17.1 |
| 8 | `letters`: defaults, template editing, preview, issue, history, print (1 or 2 copies) | letter tests pass |
| 9 | Settings pages, Help page, "Things to fix" box, `seed_demo` | demo shows everything in §16 |
| 10 | Dockerfile, compose, Caddyfile, backup script, `README_RUN.md`, `DECISIONS.md` | fresh clone → `docker compose up` works |

**Definition of done (whole project)**
- [ ] `python manage.py test` passes; §17.1 numbers match exactly.
- [ ] After `seed_demo`, logging in as the SRKS owner shows the November 2026 draft with the §17.1 numbers.
- [ ] Every upload page has "Download sample file"; the monthly sheet comes pre-filled.
- [ ] Every page works at 360px width and without JavaScript.
- [ ] One payslip prints on one A4 page; the register prints in landscape with repeating headers.
- [ ] No owner page can show or change another company's data.
- [ ] No hard delete of employees, months, lines, letters or advances in the owner UI.
- [ ] All statutory values come from §9 tables.

---

## 20. Later (phase 2 ideas, in rough priority)
1. Hindi (and Kannada/Marathi) translations of the UI.
2. More users per company with roles (Accountant: everything except settings; Viewer: reports only).
3. Server-side PDF (WeasyPrint) and sending payslips by email/WhatsApp; employee login to see own payslips.
4. Full and final settlement: notice pay, leave encashment, gratuity (5 years; 1 year for fixed-term under the Codes), with a printable F&F statement.
5. Leave register (annual leave with wages under the OSH Code) feeding the monthly sheet.
6. Statutory bonus (Code on Wages), Labour Welfare Fund by state, more PT states (Tamil Nadu and Kerala are half-yearly).
7. Split-month calculation when a rate changes inside a month; arrears for back-dated increments.
8. State minimum-wage table with warnings; Form 16 / 24Q data export for the CA.
9. Multiple branches in different states under one company.

---

## 21. Legal basis checked on 4 November 2026 (and a warning)

| Topic | Rule used | Where it comes from |
|---|---|---|
| Labour Codes | All four Codes in force from 21-11-2025; one definition of wages; excluded allowances above 50% of pay are added back | Code on Wages s.2(y); Code on Social Security s.2(88); MoLE FAQs (Dec 2025, Mar 2026) |
| PF ceiling | ₹25,000 from 17-09-2026 (₹15,000 before); rates unchanged | S.O. 5109(E); EPFO FAQs on the revised ceiling |
| PF rates | 12% + 12% (8.33% EPS), EDLI 0.5%, admin 0.5% (min ₹500/month); EPS only if wages ≤ ceiling at joining; EPS stops at 58 | EPFO FAQs |
| ESI | 0.75% + 3.25%, each rounded up; ceiling ₹21,000 (₹25,000 PwD); contribution periods Apr–Sep / Oct–Mar; employee share waived ≤ ₹176/day; on Code wages | Social Security (Central) Rules 2026 r.19; saved ESI rules; ESIC circular Dec 2025 |
| PT | Karnataka ₹200 (Feb ₹300) at ₹25,000+ from 1-4-2025; Maharashtra slabs as §9.2 | State Acts |
| Income tax | Tax year 2026-27 slabs unchanged by Budget 2026; new regime default | Income-tax Act 2025 (from 1-4-2026); Finance Act |
| Wage slip | Form V, rule 52 | Wages (Central) Rules, 2026 (8 May 2026) |
| Appointment letter | Compulsory for every employee, prescribed particulars | OSH Code s.6; OSH (Central) Rules, 2026 (8 May 2026) |
| Paying wages | Monthly wages before the 7th of next month; on leaving, within 2 working days | Code on Wages s.17 |

> **Warning for the platform owner.** This software applies rules as understood on the date above. Rates, ceilings and formats change by notification, and State rules can differ. Before going live, have a Chartered Accountant check §8 and §9 against your customers' States, and review the statutory tables every April and whenever EPFO, ESIC, the Income Tax Department or a State notifies a change. Interpretations that are still debated (for example, exactly which allowances count inside the 50% test) are kept as switches so they can be changed without code.

Sources used: EPFO FAQs as summarised by CAclubindia (Oct 2026) and the EPFO ECR 2.0 file guide; LexComply on ESIC under the Code (Oct 2026); Patron Accounting on the 50% rule and MoLE FAQs (Apr 2026); ClearTax and CalcGuru on Karnataka PT; Business Today on tax slabs for FY 2026-27 (Apr 2026); TaxHeal on Form V wage slip; Lexplosion on appointment-letter particulars (Jul 2026); Key4Comply on the December 2025 ESIC circular.

---

## Appendix A — Reference engine (port to `payroll/engine.py`)

Rules for porting:
- Keep it **pure**: no Django imports, no database access. `payroll/service.py` loads the employee, structure, inputs, YTD and rates from the database, builds these dataclasses and stores the returned `Line`.
- Replace the module-level `PT_SLABS`, `NEW_REGIME` and `OLD_REGIME` constants with values passed in from `PTSlab` / `TaxConfig` (seeded with exactly these numbers). Results must not change.
- Keep function names so the tests in §17 read naturally.

```python
"""
Reference payroll engine (pure Python, Decimal only).
This file is the source of the "golden" numbers in the README.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP, ROUND_CEILING, ROUND_FLOOR
from typing import Optional

ZERO = D("0")
HUNDRED = D("100")


# ---------------------------------------------------------------- rounding
def rupee(x) -> D:
    """Nearest rupee, 0.50 goes up (PF, EPS, earnings, PT, TDS, net pay)."""
    return D(x).quantize(D("1"), rounding=ROUND_HALF_UP)


def rupee_up(x) -> D:
    """Next higher rupee (ESI shares - each share separately)."""
    return D(x).quantize(D("1"), rounding=ROUND_CEILING)


def pct(amount: D, rate: D) -> D:
    return amount * rate / HUNDRED


# ---------------------------------------------------------------- inputs
@dataclass
class Rates:
    """Statutory values in force on the LAST day of the payroll month."""
    pf_ceiling: D = D("25000")          # from 17-09-2026 (15000 before)
    pf_er_rate: D = D("12")
    eps_rate: D = D("8.33")
    edli_rate: D = D("0.50")
    pf_admin_rate: D = D("0.50")
    pf_admin_min: D = D("500")          # per establishment per month
    eps_max_age: int = 58
    esi_ee_rate: D = D("0.75")
    esi_er_rate: D = D("3.25")
    esi_ceiling: D = D("21000")
    esi_ceiling_pwd: D = D("25000")
    esi_ee_exempt_daily: D = D("176")
    esi_on_code_wages: bool = True      # False = old practice (gross)
    code_wage_min_pct: D = D("50")


@dataclass
class Settings:
    salary_divisor: str = "CALENDAR"    # CALENDAR | FIXED_30 | FIXED_26
    pf_enabled: bool = True
    pf_ee_rate: D = D("12")
    pf_restrict_to_ceiling: bool = True
    esi_enabled: bool = True
    pt_enabled: bool = True
    pt_state: str = "KA"
    tds_enabled: bool = True
    ot_multiplier: D = D("2")
    ot_days_divisor: D = D("26")
    ot_hours_per_day: D = D("8")


@dataclass
class Structure:
    basic: D
    da: D = ZERO
    hra: D = ZERO
    conveyance: D = ZERO
    other: D = ZERO

    def wage_part(self) -> D:           # counts as "wages" under the Codes
        return self.basic + self.da + self.other

    def gross(self) -> D:
        return self.basic + self.da + self.hra + self.conveyance + self.other

    def plus_increment(self, inc: D) -> "Structure":
        return Structure(self.basic + inc, self.da, self.hra, self.conveyance, self.other)


@dataclass
class Employee:
    code: str
    name: str
    gender: str                          # M / F / O
    dob: date
    doj: date
    dol: Optional[date] = None
    pf_member: bool = True
    eps_member: bool = True
    esi_override: str = "AUTO"           # AUTO / YES / NO
    is_pwd: bool = False
    pt_exempt: bool = False
    tax_regime: str = "NEW"              # NEW / OLD
    old_regime_deductions: D = ZERO      # per year, entered by owner/CA


@dataclass
class MonthInput:
    present: D
    paid_leave: D = ZERO
    week_off_holiday: D = ZERO
    lop: Optional[D] = None
    ot_hours: D = ZERO
    ot_amount: Optional[D] = None
    incentive: D = ZERO
    increment: D = ZERO
    advance: D = ZERO
    other_deduction: D = ZERO
    tds_override: Optional[D] = None


@dataclass
class YTD:
    """Tax-year-to-date BEFORE this month: finalised months + opening balance."""
    gross: D = ZERO
    tds: D = ZERO
    pt: D = ZERO


# ---------------------------------------------------------------- helpers
def days_in_month(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def code_wages(wage_part: D, total: D, min_pct: D) -> D:
    """Labour Code wages: wages part, but never less than 50% of all pay."""
    return max(wage_part, pct(total, min_pct))


def esi_covered(emp: Employee, structure_at_period_start: Structure, rates: Rates) -> bool:
    """Coverage fixed at start of contribution period (Apr-Sep / Oct-Mar) or at joining."""
    if emp.esi_override == "YES":
        return True
    if emp.esi_override == "NO":
        return False
    s = structure_at_period_start
    full = code_wages(s.wage_part(), s.gross(), rates.code_wage_min_pct) if rates.esi_on_code_wages else s.gross()
    ceiling = rates.esi_ceiling_pwd if emp.is_pwd else rates.esi_ceiling
    return rupee(full) <= ceiling


# Professional tax: (state, gender, from_inclusive, to_inclusive or None, amount, february_amount)
PT_SLABS = [
    ("KA", "ANY", D("25000"), None, D("200"), D("300")),
    ("MH", "M", D("7501"), D("10000"), D("175"), D("175")),
    ("MH", "M", D("10001"), None, D("200"), D("300")),
    ("MH", "O", D("7501"), D("10000"), D("175"), D("175")),
    ("MH", "O", D("10001"), None, D("200"), D("300")),
    ("MH", "F", D("25001"), None, D("200"), D("300")),
]


def professional_tax(state: str, gender: str, gross: D, month: int) -> D:
    for st, g, lo, hi, amt, feb in PT_SLABS:
        if st != state or (g != "ANY" and g != gender):
            continue
        if gross >= lo and (hi is None or gross <= hi):
            return feb if month == 2 else amt
    return ZERO


# Income tax, tax year 2026-27 (unchanged from 2025-26 by Budget 2026)
NEW_REGIME = dict(
    std_deduction=D("75000"),
    slabs=[(D("400000"), D("0")), (D("800000"), D("5")), (D("1200000"), D("10")),
           (D("1600000"), D("15")), (D("2000000"), D("20")), (D("2400000"), D("25")), (None, D("30"))],
    rebate_limit=D("1200000"), rebate_max=D("60000"),
    surcharge=[(D("5000000"), D("10")), (D("10000000"), D("15")), (D("20000000"), D("25"))],
    cess=D("4"),
)
OLD_REGIME = dict(
    std_deduction=D("50000"),
    slabs=[(D("250000"), D("0")), (D("500000"), D("5")), (D("1000000"), D("20")), (None, D("30"))],
    senior_exemption=D("300000"), super_senior_exemption=D("500000"),
    rebate_limit=D("500000"), rebate_max=D("12500"),
    surcharge=[(D("5000000"), D("10")), (D("10000000"), D("15")), (D("20000000"), D("25")), (D("50000000"), D("37"))],
    cess=D("4"),
)


def _slab_tax(income: D, slabs) -> D:
    tax, lower = ZERO, ZERO
    for upper, rate in slabs:
        if upper is None or income <= upper:
            tax += pct(income - lower, rate)
            return tax
        tax += pct(upper - lower, rate)
        lower = upper
    return tax


def _old_slabs_for_age(age: int):
    s = OLD_REGIME["slabs"]
    if age >= 80:
        return [(OLD_REGIME["super_senior_exemption"], D("0")), (D("1000000"), D("20")), (None, D("30"))]
    if age >= 60:
        return [(OLD_REGIME["senior_exemption"], D("0")), (D("500000"), D("5")), (D("1000000"), D("20")), (None, D("30"))]
    return s


def annual_income_tax(taxable: D, regime: str, age: int) -> D:
    cfg = NEW_REGIME if regime == "NEW" else OLD_REGIME
    slabs = cfg["slabs"] if regime == "NEW" else _old_slabs_for_age(age)
    base = _slab_tax(taxable, slabs)
    # rebate + marginal relief at the rebate limit
    if taxable <= cfg["rebate_limit"]:
        base = max(ZERO, base - cfg["rebate_max"])
    elif regime == "NEW":
        base = min(base, taxable - cfg["rebate_limit"])
    # surcharge with marginal relief
    sc = ZERO
    for threshold, rate in reversed(cfg["surcharge"]):
        if taxable > threshold:
            sc = pct(base, rate)
            # relief: tax+surcharge may not exceed tax at threshold (+its surcharge) + excess income
            lower_rate = ZERO
            for t2, r2 in cfg["surcharge"]:
                if t2 < threshold:
                    lower_rate = r2
            at_threshold = _slab_tax(threshold, slabs)
            at_threshold += pct(at_threshold, lower_rate)
            sc = min(sc, at_threshold + (taxable - threshold) - base)
            sc = max(sc, ZERO)
            break
    return rupee((base + sc) * (1 + cfg["cess"] / HUNDRED))


def months_left_in_tax_year(month: int) -> int:
    """Including the current month. April -> 12 ... March -> 1."""
    return 12 - ((month - 4) % 12)


def tax_year_end(year: int, month: int) -> date:
    return date(year + 1, 3, 31) if month >= 4 else date(year, 3, 31)


def future_months(year: int, month: int, count: int):
    out, y, m = [], year, month
    for _ in range(count):
        m += 1
        if m == 13:
            m, y = 1, y + 1
        out.append((y, m))
    return out


# ---------------------------------------------------------------- one line
@dataclass
class Line:
    code: str
    name: str
    paid_days: D = ZERO
    lop: D = ZERO
    ncp_days: int = 0
    basic: D = ZERO
    da: D = ZERO
    hra: D = ZERO
    conveyance: D = ZERO
    other: D = ZERO
    ot_pay: D = ZERO
    incentive: D = ZERO
    gross: D = ZERO
    code_wages: D = ZERO
    epf_wage: D = ZERO
    eps_wage: D = ZERO
    edli_wage: D = ZERO
    ee_pf: D = ZERO
    er_eps: D = ZERO
    er_epf: D = ZERO
    edli: D = ZERO
    pf_admin: D = ZERO
    esi_covered: bool = False
    esi_wage: D = ZERO
    ee_esi: D = ZERO
    er_esi: D = ZERO
    pt: D = ZERO
    tds: D = ZERO
    advance: D = ZERO
    other_deduction: D = ZERO
    total_deductions: D = ZERO
    net_pay: D = ZERO
    employer_cost: D = ZERO
    warnings: list = field(default_factory=list)


def compute_line(year: int, month: int, emp: Employee, structure: Structure,
                 structure_at_esi_period_start: Structure, inp: MonthInput,
                 settings: Settings, rates: Rates, ytd: YTD) -> Line:
    L = Line(emp.code, emp.name)
    dim = days_in_month(year, month)
    month_start, month_end = date(year, month, 1), date(year, month, dim)

    # 1. salary for this month (increment applies from this month)
    s = structure.plus_increment(inp.increment) if inp.increment else structure

    # 2. days
    counted = inp.present + inp.paid_leave + inp.week_off_holiday
    lop = inp.lop if inp.lop is not None else max(ZERO, D(dim) - counted)
    if counted + lop != D(dim):
        raise ValueError(f"{emp.code}: days do not add up to {dim}")
    L.lop = lop
    L.paid_days = D(dim) - lop
    divisor = {"CALENDAR": D(dim), "FIXED_30": D(30), "FIXED_26": D(26)}[settings.salary_divisor]
    factor = max(ZERO, 1 - lop / divisor)

    # days outside employment are not NCP days for EPFO
    not_employed = 0
    if emp.doj > month_start:
        not_employed += (emp.doj - month_start).days
    if emp.dol and emp.dol < month_end:
        not_employed += (month_end - emp.dol).days
    L.ncp_days = int(max(ZERO, lop - not_employed).to_integral_value(rounding=ROUND_FLOOR))

    # 3. earnings
    L.basic, L.da, L.hra = rupee(s.basic * factor), rupee(s.da * factor), rupee(s.hra * factor)
    L.conveyance, L.other = rupee(s.conveyance * factor), rupee(s.other * factor)
    if inp.ot_amount is not None:
        L.ot_pay = rupee(inp.ot_amount)
    elif inp.ot_hours:
        hourly = s.wage_part() / settings.ot_days_divisor / settings.ot_hours_per_day
        L.ot_pay = rupee(hourly * settings.ot_multiplier * inp.ot_hours)
    L.incentive = rupee(inp.incentive)
    wage_part = L.basic + L.da + L.other
    L.gross = wage_part + L.hra + L.conveyance + L.ot_pay + L.incentive

    # 4. Labour Code wages (50% rule)
    L.code_wages = rupee(code_wages(wage_part, L.gross, rates.code_wage_min_pct))
    if s.wage_part() < pct(s.gross(), rates.code_wage_min_pct):
        L.warnings.append("STRUCTURE_BELOW_50")

    # 5. PF
    if settings.pf_enabled and emp.pf_member and L.code_wages > 0:
        cap = rates.pf_ceiling
        L.epf_wage = min(L.code_wages, cap) if settings.pf_restrict_to_ceiling else L.code_wages
        too_old = age_on(emp.dob, month_start) >= rates.eps_max_age
        if too_old and emp.eps_member:
            L.warnings.append("EPS_STOPPED_AGE_58")
        L.eps_wage = min(L.code_wages, cap) if (emp.eps_member and not too_old) else ZERO
        L.edli_wage = min(L.code_wages, cap)
        L.ee_pf = rupee(pct(L.epf_wage, settings.pf_ee_rate))
        er_total = rupee(pct(L.epf_wage, rates.pf_er_rate))
        L.er_eps = rupee(pct(L.eps_wage, rates.eps_rate))
        L.er_epf = er_total - L.er_eps
        L.edli = rupee(pct(L.edli_wage, rates.edli_rate))
        L.pf_admin = rupee(pct(L.epf_wage, rates.pf_admin_rate))

    # 6. ESI
    if settings.esi_enabled:
        L.esi_covered = esi_covered(emp, structure_at_esi_period_start, rates)
        if L.esi_covered:
            L.esi_wage = L.code_wages if rates.esi_on_code_wages else L.gross
            L.er_esi = rupee_up(pct(L.esi_wage, rates.esi_er_rate))
            daily = (L.esi_wage / L.paid_days) if L.paid_days else ZERO
            if L.esi_wage > 0 and daily <= rates.esi_ee_exempt_daily:
                L.warnings.append("ESI_EMPLOYEE_SHARE_WAIVED")
            elif L.esi_wage > 0:
                L.ee_esi = rupee_up(pct(L.esi_wage, rates.esi_ee_rate))

    # 7. Professional tax (on gross earned this month)
    if settings.pt_enabled and not emp.pt_exempt:
        L.pt = professional_tax(settings.pt_state, emp.gender, L.gross, month)

    # 8. TDS (projection over the tax year, spread evenly over months left)
    if inp.tds_override is not None:
        L.tds = rupee(inp.tds_override)
    elif settings.tds_enabled:
        left = months_left_in_tax_year(month)
        later = future_months(year, month, left - 1)
        projected = ytd.gross + L.gross + s.gross() * (left - 1)
        if emp.tax_regime == "NEW":
            taxable = projected - min(NEW_REGIME["std_deduction"], projected)
        else:
            pt_later = sum((professional_tax(settings.pt_state, emp.gender, s.gross(), m)
                            for _, m in later), ZERO) if settings.pt_enabled and not emp.pt_exempt else ZERO
            pt_year = ytd.pt + L.pt + pt_later
            taxable = projected - min(OLD_REGIME["std_deduction"], projected) - pt_year - emp.old_regime_deductions
        taxable = max(ZERO, taxable)
        age = age_on(emp.dob, tax_year_end(year, month))
        annual = annual_income_tax(taxable, emp.tax_regime, age)
        L.tds = max(ZERO, rupee((annual - ytd.tds) / left))

    # 9. other deductions and net
    L.advance, L.other_deduction = rupee(inp.advance), rupee(inp.other_deduction)
    L.total_deductions = L.ee_pf + L.ee_esi + L.pt + L.tds + L.advance + L.other_deduction
    L.net_pay = L.gross - L.total_deductions
    if L.net_pay < 0:
        raise ValueError(f"{emp.code}: deductions are more than earnings")
    if L.total_deductions > pct(L.gross, D("50")):
        L.warnings.append("DEDUCTIONS_ABOVE_50")
    L.employer_cost = L.gross + L.er_eps + L.er_epf + L.edli + L.pf_admin + L.er_esi
    return L


def establishment_pf_admin(lines, rates: Rates) -> D:
    """PF admin charges for the whole establishment: 0.5% of EPF wages, minimum Rs 500."""
    total = sum((l.pf_admin for l in lines), ZERO)
    has_member = any(l.epf_wage > 0 for l in lines)
    return max(total, rates.pf_admin_min) if has_member else ZERO


def ecr_line(uan: str, name_as_per_uan: str, l: Line) -> str:
    f = [uan, name_as_per_uan, l.gross, l.epf_wage, l.eps_wage, l.edli_wage,
         l.ee_pf, l.er_eps, l.er_epf, l.ncp_days, 0]
    return "#~#".join(str(int(x)) if isinstance(x, D) else str(x) for x in f)


# ---------------------------------------------------------------- words
ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
        "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return ONES[n]
    return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")


def _three(n: int) -> str:
    h, rest = divmod(n, 100)
    parts = []
    if h:
        parts.append(ONES[h] + " Hundred")
    if rest:
        parts.append(_two(rest))
    return " ".join(parts)


def amount_in_words(amount: D) -> str:
    n = int(rupee(amount))
    if n == 0:
        return "Rupees Zero Only"
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    parts = []
    if crore:
        parts.append((amount_in_words(D(crore))[7:-5] if crore >= 100 else _two(crore)) + " Crore")
    if lakh:
        parts.append(_two(lakh) + " Lakh")
    if thousand:
        parts.append(_two(thousand) + " Thousand")
    if n:
        parts.append(_three(n))
    return "Rupees " + " ".join(parts) + " Only"


def indian_format(n) -> str:
    n = int(rupee(n))
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups) + "," + tail
```
