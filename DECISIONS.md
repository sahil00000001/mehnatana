# Decisions taken while building

README §0 rule 6: where the README was unclear, the simplest behaviour that keeps data safe was chosen and written down here. Places where the README disagrees with itself also have a `# NOTE:` comment in the code.

## Database (Supabase)

- **Own schema plus a table prefix.** Every table lives in the PostgreSQL schema `mehnatana` (setting `POSTGRES_SCHEMA`), and every Mehnatana table name starts with `mtn_`. Django's own tables (`django_migrations`, `auth_*`, sessions) live in the same schema, so nothing touches the other tables in the shared Supabase project. The schema is created on the first connection, and every connection sets `search_path` to it (`core/db.py`).
- **Not exposed by the Supabase Data API.** A non-`public` schema is not reachable through Supabase's REST API, so payroll data cannot be read with the project's anon key. Do not add `mehnatana` to "Exposed schemas" in Supabase.
- **No Supabase API keys.** Django talks to PostgreSQL directly, so it needs only the database connection string. Use the **Session pooler** (port 5432): the Transaction pooler (6543) does not keep the per-connection `search_path`. Server-side cursors are switched off so that poolers work.
- **SQLite when no database is configured.** This is for local development and the tests.

## Code structure

- Company screens are function views with `@company_required`. Every single-object fetch goes through `company_get()`, which always adds `company=request.company`. `CompanyScopedMixin` exists for class-based views; plain functions read more simply here.
- `page()` renders every normal screen with its title and one-line instruction.

## Money and the engine

- `payroll/engine.py` is Appendix A unchanged, except that PT slabs and tax slabs arrive through `Rates` (`pt_slabs`, `tax_new`, `tax_old`) from the database. The senior-citizen old-regime slab lists are built from `TaxConfig` instead of being written out by hand, with identical results (NOTE in `engine.py`). Every golden number in §17.1 and §17.2 is tested against both the reference constants and the seeded tables.
- The 50% wage test uses gross pay without employer contributions, per README §8.3. The switch point is `engine.code_wages()`.
- ESI coverage is decided by the structure at the start of the contribution period. An increment entered in the period's first month (April or October) takes effect from the 1st, so it counts for that period too.
- Money entered in sheets and forms must be whole rupees. Paise are refused, not silently rounded.
- The default "name as per UAN" drops dots, so "Mohd. Arif" becomes "MOHD ARIF" as in the golden ECR line (NOTE in `employees/models.py`).
- The PF ceiling of ₹25,000 starts on 17-09-2026, and rates are read on the month's last day. So September 2026 already uses ₹25,000 and shows `RATE_CHANGED_IN_MONTH` (NOTE in the statutory seed).
- `DueDate` has two extra optional fields (`march_due_day`, `march_due_month`) for "TDS for March by 30 April", plus `applies_to_state` for state PT reminders.

## Salary months

- **Uploads in order.** A month can be uploaded only after every earlier month is finalised, because TDS depends on the finalised year-to-date. Its sheet can be downloaded at any time.
- **Rechecked at finalise.** Finalising recalculates every line and checks again that each advance deduction is within what is still outstanding.
- **Settings changes update drafts.** Changing payroll rules or opening balances recalculates the draft months. Finalised months never change.
- **Re-upload clears stale rows.** Re-uploading a draft removes draft rows for people no longer paid that month, for example after their joining date was corrected.
- **Relieve and Undo relieve.** Undo relieve is refused if any month after the leaving month is finalised. Relieve is refused if the person was paid in a finalised month after the chosen last working day.
- **Revise salary from open months only.** Revise salary offers months from the first open month onwards, never a finalised month.
- **Month documents and files** (§7) are made only for finalised months; draft months redirect with a message. Print previews of drafts carry a "DRAFT — not final" watermark.

## Excel

- **Our sample files only.** A file without our hidden `_meta` sheet is refused, with a message to use our sample or pre-filled sheet. Wrong template type, company or month is explained in words.
- **Failed uploads are kept privately** (`media/private/uploads/<CODE>/failed/`), and the session remembers the mistakes. That is what "Download my sheet with mistakes marked" uses.
- **Monthly sample file.** The URL map has no route for the monthly sample, so it is served as `sheet.xlsx?sample=1`.
- **Opening-balances sheet** has an extra grey "Employee Name" column after Emp Code, for reading only.
- **Employee upload details:**
  - An empty Payment Mode means Bank if an account number is given, otherwise Cash.
  - For existing employees, empty Yes/No or drop-down cells keep the saved value.
  - An empty Aadhaar cell keeps the saved number, and the export never writes Aadhaar out.
- **ID columns** (UAN, IP, account, mobile, PAN, IFSC) are Excel Text cells, so long numbers and leading zeros survive.
- **Dates** are DD-MM-YYYY (or a real Excel date or ISO). The US month/day order and 2-digit years are never accepted. The same applies in forms, so `05/11/2026` means 5 November.

## Making it easy for the owner (added after testing)

- **Salary in the employee sheet.** When an uploaded employee list has a different salary (Basic, DA, HRA, Conveyance, Other) for someone already in the app, nothing is ignored silently. The app shows one page listing each change (now → in your file) with a "New salary from" month and two buttons: "Yes, save and change these salaries" or "Save, but keep the old salaries". The old salary stays in the history, and finalised months are never offered. An empty Basic cell means "no salary given" and is not a change.
- **Draft months follow changes at once.** Revise salary, editing or relieving an employee, an employee upload, payroll rules and opening balances all recalculate the draft months straight away. Finalised months never change.
- **Designation and Department are type-in boxes with suggestions.** A new name is created on save, so a brand-new shop can add its first employee without visiting Settings. Names are matched ignoring capitals.
- **"Save and add another"** on the Add employee form.
- **No duplicate numbers.** The same UAN, PAN, ESIC IP number or Aadhaar on two people is refused, in the form and in the upload.
- **Monthly sheet with empty days.** A completely unfilled sheet is refused with one message ("No days are filled in this sheet"). A single row with all day columns empty is refused for that row. Before this change, both would have paid ₹0.
- **Headings are matched forgivingly**, ignoring capitals, spaces, ₹, "*", "(optional)" and punctuation. A column we do not know, which has something written in it, is refused with a clear message, because silently ignoring it could lose data (for example a hand-renamed "Increment" column). An empty extra column is fine.
- **A new shop's Home page** says "Start here: add your employees". Uploading a month with no one to pay explains what to do instead.

## Payslip designs

- **Three designs, chosen per company** under Settings → Payslip design: *Classic ledger* (the README §11.1 Form V layout), *Modern* (shaded header, details grid, monthly rate next to each earning, large net pay box) and *Simple, with receipt* (one table plus a signature/thumb-impression receipt strip for cash pay). The platform admin can also set the design when creating the company.
- **Same particulars in every design.** All three print every Form V particular; only the layout differs. Modern and Simple leave out earnings and deductions that are zero; Classic shows every row, like a register.
- **Previews.** The settings page shows each design with the company's latest payslip, or a sample employee before the first month.
- **Options on the same page:** show or hide what the employer also paid (PF, EDLI, PF admin, ESI); two payslips per A4 sheet (moved here from Payroll rules); an optional note printed at the bottom.

## Safety checks added after testing

- **Amount limits.** Every amount box accepts its own displayed value (12000, 12000.00, 12,000 or ₹12000). Any single amount above ₹99,99,999, and more than 300 overtime hours in a month, is refused as a typing mistake; larger values would not fit the database columns.
- **Notes for doubtful values** (shown, not blocking): more than 100 OT hours; a TDS override above 30% of the salary; weekly offs left empty so those days became LOP.
- **What the owner confirms is what gets locked.** The Finalise page works the month out again before showing the totals. Finalising is refused while someone who should be paid that month is missing from the sheet, and for a month that has not started yet.
- **Finalised months are not contradicted.** A date of joining cannot move after a month the person was already paid for. A last working day cannot fall before the days a finalised month paid for. New staff whose joining month is already finalised are told they will be paid from the next open month.
- **Advances.** Only advances given by the end of a month are recovered in it, and an advance cannot be dated in the future.
- **Leavers' TDS.** For someone who leaves in the month, TDS is worked out on actual pay and settled in that month, instead of projecting salary until March (NOTE in `payroll/engine.py`). The README's numbers are unchanged.
- **People serving notice.** Someone with a future last working day still counts as working: they appear in the Active list as "Leaving on …" and can be given an advance.
- **Careful sheet reading:**
  - The monthly sheet must keep all of its columns, because a deleted column would silently become 0.
  - In the employee list, a missing column keeps the saved values, and rows that changed nothing are reported as "already up to date".
  - Emp codes are matched ignoring capitals and spaces.
  - A row with data but no Emp Code is pointed out.
  - Mobile numbers may be written with +91 or a leading 0.
- **Excel downloads never contain live formulas.** Text that starts with "=" (in a name, a note and so on) is written as plain text.
- **Logos are saved as .png or .jpg** according to the file's real content, not its name.
- **Statutory table changes in the admin** are applied to every company's draft months at once. Admin edits and bulk deletes are audit-logged.
- **Error pages.** A friendly page is shown for server errors and for an expired form (CSRF).

## Logins and admin

- **One login, many companies** (changed after the fourth review). The separate platform-admin and business-owner
  logins were replaced by one login that sees every company. The company being worked on is chosen on **All
  companies** and kept in the session (`core.tenancy.SESSION_COMPANY`); `TenantMiddleware` turns it into
  `request.company`, so every existing screen and every `company_get()` check works unchanged. A screen opened with
  no company chosen goes to All companies and comes back after **Open** (`?next=`, checked to be on this site).
- **Old business logins** are switched off (not deleted) by migration `accounts 0002`, so the audit log keeps their
  names. `User.company` and `User.role` were removed.
- **Companies are added and switched off on the main site**, not in the admin. Switching off keeps every record and
  closes the company if it was open.
- **The admin screens** keep only the statutory tables and the read-only audit log, behind the same login: `/admin/login/`
  redirects to the one login page. Companies, logins and groups are not registered there.
- **Lockout everywhere.** The 5-wrong-passwords lockout sits in the authentication backend.
- **Password-reset email** is offered only when `EMAIL_HOST` is set. Otherwise the page gives the help phone number.

## Working days, attendance register and annual report

- **Weekly off days per company** (`Company.weekly_off_days`, digits Monday = 0 … Sunday = 6; default Sunday = a
  six-day week). At least one working day is required.
- **Salary per working day** is a new choice of divisor (`WORKING`): absent days are cut at monthly salary ÷ (days in
  the month − weekly offs), so each month has its own count. Days before joining or after leaving count only their
  working days, whether or not the Sundays before joining were written as weekly offs. Existing companies keep their
  divisor; a new company starts with working days. Engine version 1.1.
- **The attendance sheet** fills in date of joining, PAN, UAN, ESI number and working days (grey, never read back, so
  older sheets without them still upload) and pre-fills *Weekly Off + Holidays* with the weekly offs while employed.
  Days present are never guessed. A row with only weekly offs is still "empty", so an untouched sheet is refused.
- **Each salary line stores the month's working days** (`PayrollLine.working_days`); older lines were filled with
  days minus Sundays by the migration.
- **Attendance register** reads PAN, UAN, ESI and date of joining from the employee record (falling back to the
  month's snapshot), so details added later appear without re-uploading. Excel only after finalising, like the
  salary register.
- **Salary payments** (`PayrollLine.paid_amount`, `paid_on`) can be marked only on finalised months and never change
  the salary. Pending = net pay − paid.
- **Annual salary report** covers April–March and includes draft months (marked), because their salary is payable
  too. "TSI number" in the request was read as the UAN (PF number); ESI is shown separately.

## Name

- The product is called **PayCore** (`APP_NAME`). The database schema `mehnatana`, the `mtn_` table prefix, the
  repository and the live address are unchanged, so nothing had to be moved.
- **Language.** Only English is listed in `LANGUAGES`, and `LocaleMiddleware` is not on yet. Otherwise the Django admin would switch to Hindi from the browser language before our own strings are translated. All UI strings are wrapped in gettext and ready for Hindi.

## Staff brought over from an older payroll system

- Old payroll records often have no date of birth or father's / spouse's name. Such people can still be
  saved: both fields may be empty **in the database only**. The add / edit form and the Excel upload still
  ask for both, so a person is completed the next time they are edited.
- "Things to fix" lists active staff missing either one, and the employee page says "Not given yet".
- Payroll without a date of birth: EPS follows the person's "pension (EPS) member" tick (the age-58 stop
  cannot be checked), and income tax uses the normal (under 60) slabs. The turning-58 list skips them.
- The one-off import of the old data is not part of this repository: it holds clients' private details.

## Settings

- **Company code** is set by the platform admin. Owners cannot change it, because it is part of letter numbers and file names.
- **"First salary month"** is locked after the first upload.
- **New-company defaults:** PF, PT and TDS on; ESI off, since it applies only to 10+ persons in notified areas.
- **Logos** are checked by their first bytes (PNG/JPG) and size (≤ 500 KB). No image library is needed.

## Reports

- **ECR file.** It lists PF members with PF wages, plus zero-pay members while PF is on. Members without a valid 12-digit UAN are left out and listed on the PF statement page, next to the ECR link.
- **ESI file.** Dates are written DD/MM/YYYY. Reason code 2 plus the last working day if the person left that month; 1 if zero days; otherwise 0. **Before going live, compare the columns with the template on the ESIC portal.**
- **"Advance balance after this month"** on the payslip = advances given up to month end, minus recoveries up to and including that month. On a draft it also subtracts this month's deduction.
- **Cash payment list** includes cheque-paid staff, marked "(Cheque)", because they also sign for payment.
- **Notices** replace the dashboard reminders (see "Notices" below).
- **Draft months.** PF, ESI and PT statements of a draft month can be seen and printed (marked draft) but not downloaded as Excel, the same rule as the register and bank sheet. The cost report uses finalised months only; asking for a draft month opens the latest finalised month with a note.
- **Shops without PF or ESI** do not see buttons, Home figures or payslip lines for the scheme they do not have. "Employer also paid" on a payslip lists only the parts that are not zero.
- **TDS report.** The totals row includes the opening TDS, so "Incl. opening" adds up on the page.
- **PT statement.** Slab-wise totals are grouped by the PT amount per person.
- **Register page numbers** ("Page x of y") use CSS page counters. Recent Chrome prints them; other browsers may leave the number off.

## Letters

- **Default texts** are copied exactly from README §12.3–§12.5.
- **"Change the text for this letter only".** The box is pre-filled with every placeholder already filled in except `letter.ref_no` and `letter.particulars_table`. On save the text is rendered again and HTML-escaped.
- **Particulars table placement.** The appointment particulars table cannot be removed. If its placeholder is deleted, the table goes after the paragraph with "following terms". If there is none, it goes before the first numbered term, otherwise at the end.
- **Empty line placeholders.** A line holding only `letter.probation_line` or `letter.resignation_line` disappears when that value is empty.
- **Stored copies include the letterhead**, so a reprint matches the original even after the address changes.
- **Reference numbers** are given while the company row is locked; the unique constraint is a backstop.
- **Pronouns.** Gender "Other" uses they/their/them.
- **Particulars row 13 (ESI: Yes/No)** uses the rates for the month of the letter date.
- **Mistyped placeholders** like `{{ Emp.Name }}` also produce a warning.
- **Save pressed twice.** Within 10 minutes, a letter for the same person, type and date with exactly the same printed text is not saved again (checked while the company row is locked). A corrected text is a new letter with the next number.
- **Resignation date** is kept only when the leaving reason is Resigned, and must be on or before the last working day. The relieving letter mentions the resignation letter only in that case.
- **Relieving and experience letters dated before the last working day** show a warning on the preview, with a link to use the last working day.
- **"Reset to original text"** asks once more before it replaces the company's wording.

## Notices

- **What they are.** The Notices page (and the strip at the top of every page) lists PF, ESI, PT, TDS and salary due dates with "due in N days" or "N days late", months not finalised after they end, staff leaving in the next 14 days, probation ending in the next 30 days, and the "things to fix" list.
- **Urgent** means due within 3 days or late; **soon** means within 10 days. Only these show in the strip and the menu count.
- **Paid or not.** The app does not know whether a due has been paid. A due disappears after its date once the month is finalised, and anything more than 45 days late is dropped.
- **In the app only.** Notices are not sent by SMS, WhatsApp or e-mail.
- **Never break a page.** If working out the notices fails, the error is logged and the page shows without them.

## Uploads (added after the third test round)

- **One answer per file.** The "salary changes" confirm page carries a token for its own file. If another file was uploaded meanwhile (another tab), the answer is refused and nothing is saved.
- **New salary month.** A salary change from the upload never starts before the joining month or before a salary already set for a later month; it starts from that later month instead, and the confirm page says so.
- **Stored files are deleted** as soon as they are not needed: a waiting file when it is answered, replaced or cancelled, and a failed file when it is replaced or the upload succeeds. They hold PAN and bank numbers.
- **Advances added after a sheet is uploaded.** The uploaded sheet keeps what was typed in it. After a new advance or a changed monthly cut, the app says which month is affected and links to that row.

## Vercel deployment

- **Region.** The app runs on Vercel in the Tokyo region (`hnd1`), next to the Supabase database in
  `ap-northeast-1`. A page makes many small database calls, so the app must sit next to the database;
  the one trip from the browser to Tokyo costs far less. (Until 12-11-2026 both were in Mumbai.)
- **Database connections** go through the Supabase **Session pooler**, which works over IPv4. The direct `db.…` host is IPv6-only. Each request opens and closes its own connection (`CONN_MAX_AGE=0`), so serverless instances never hold pooler slots.
- **Files.** Vercel has no lasting disk, so uploaded files are kept in the database (`FILE_STORAGE=database`, table `mtn_stored_file`). Static files are served by WhiteNoise straight from the app folders (`WHITENOISE_USE_FINDERS`), with no collectstatic step.
- **Upload limit.** Vercel accepts requests up to 4.5 MB, so the upload limit there is 4 MB (`MAX_UPLOAD_MB`).
- **Migrations** run in GitHub Actions after the tests pass, never on Vercel itself. Database credentials live only in GitHub secrets and Vercel environment variables, never in the repository.
- **The one login** is created once from secrets (`ensure_admin`) and is never changed by later deployments.

## Security and deployment

- Two `check --deploy` warnings are silenced on purpose. Caddy already redirects HTTP to HTTPS (W008), and HSTS preload is the domain owner's choice (W021).
- Static files use WhiteNoise without a manifest, so the app (and the tests) run with `DEBUG=False` before `collectstatic`.
- Docker Compose runs `web` and `caddy` against Supabase. A local `postgres:16` `db` service is available with `--profile localdb`.
- Backups dump only the `mehnatana` schema (`pg_dump --schema`) plus the media folder (`scripts/backup.sh`).

## Known limitations

- **Formula cells.** A sheet whose formula cells were never calculated (made by a program, not saved by Excel) is read as if those cells were empty. Files saved by Excel always work.
- **Merged cells** in an uploaded sheet are read only in their first row.
- **Very large uploads.** Uploads of thousands of rows are slow (10,000 employees take about a minute). Gunicorn's timeout is 120 seconds.
- **Full PAN and bank account on the payslip.** The payslip shows these in full, as in the README §11.1 example; lists and other screens mask them.

## Not verified yet

- The test suite runs on SQLite. No PostgreSQL was available on the build machine, so the schema switch in `core/db.py` and the migrations have not yet been run against a real PostgreSQL or Supabase database. The first `migrate` against Supabase is that check (see README_RUN.md).
- Docker images were not built on the build machine (Docker not installed).
