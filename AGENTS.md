# AGENTS.md — ProcureSphere 360

> **Read this whole file before doing anything.** It is the single source of truth for every AI agent or tool (Antigravity, Claude, Codex, Cursor, Copilot, Gemini, etc.) working in this repository. If a chat instruction conflicts with this file, ask the human; otherwise follow this file.

- **Project:** ProcureSphere 360 — Enterprise Procurement, Vendor & Contract Management ERP (source-to-pay + AP workflow)
- **PRD ref:** HPE-PRD-2026-PROC01 (VER-1.0-DRAFT) · Issuing authority: HPE Team · Development partner: VPD Technologies Pvt. Ltd.
- **Local path:** `C:\Users\Dell\Desktop\ProcureSphere_360` (Windows — use PowerShell-compatible commands)
- **Repository:** https://github.com/VPDTechnologies/ProcureSphere_360
- **Team:** 7 engineers, led by the Technical Lead
- **Execution window:** ~45 days (target ≤ 40). Plan and prioritize against this window, not the PRD's 90-day schedule.

---

## 1. Mission

Build a **real, working, database-backed** ERP that replaces email/spreadsheet procurement with a controlled workflow. Every requisition, sourcing event, bid, PO, receipt, invoice, contract and approval must be traceable to **who, what state, when, and with what evidence**.

The project is complete only when all seven Day-90 acceptance demonstrations (Section 14) run end-to-end on real PostgreSQL data, all exit conditions (Section 15) are met, and the handover package (Section 16) exists.

---

## 2. Non-negotiable rules (rejection triggers)

Violating any of these gets the project rejected. Never do them, even as a "temporary" step.

1. **No mock functionality.** No hardcoded JSON, static screens, fake success messages, dummy buttons, dummy filters, or exports that don't work. Every UI action must persist to PostgreSQL.
2. **No frontend-only security.** Hiding a menu is not authorization. Every view and API endpoint enforces RBAC on the backend.
3. **Business rules live in backend/domain code** (services, models, validators) — never only in JavaScript or templates.
4. **No secrets in Git.** Use environment variables. Commit `.env.example` only. Never commit `.env`, keys, tokens, DB URLs.
5. **No destructive overwrites** of history: PO amendments, contract versions, receipts, corrections, and approvals preserve previous values.
6. **All state changes go through controlled transitions** (validated, audited) — never raw `obj.status = "X"; obj.save()` scattered in views.
7. **Meaningful, incremental commits and PRs.** No bulk code dumps, no generic messages ("update", "final", "fix issue").
8. **Stack is fixed.** Do not replace Python/Django/DRF/PostgreSQL/Redis/Celery with anything else.
9. **Scope discipline.** Implement what the PRD specifies. Do not invent extra modules or features. If something is ambiguous, follow the minimal PRD-faithful interpretation and log it in `docs/assumptions.md` (requirement, question, impact, assumption).
10. **Fresh-database reproducibility.** Migrations must apply cleanly from zero; Docker setup must work in a clean environment.

---

## 3. Mandatory technology stack

| Layer | Requirement |
|---|---|
| Backend | Python 3.11+, Django 4.2+ LTS, Django REST Framework 3.14+ |
| Database | PostgreSQL 15+ — normalized schema, FKs, unique/check constraints, indexes, migrations, transactions |
| Async | Redis 7+, Celery 5+ (+ Celery Beat) for notifications, scheduled jobs, reports, selected audit/event tasks |
| Frontend | Django Templates + HTMX, HTML5/CSS3/ES6+, Bootstrap 5 **or** Tailwind CSS (pick one, stay consistent). React 18+ only for an isolated, justified widget |
| API | Versioned REST (`/api/v1/`), JWT/token auth where applicable, validation, filtering, pagination, structured errors, OpenAPI/Swagger (e.g. drf-spectacular) |
| Serving | Gunicorn behind Nginx (TLS, secure headers, compression) |
| Container | Docker (+ docker-compose for local dependencies) |
| CI/CD | GitHub Actions — lint + test + build on PRs and protected branches |
| Observability | Structured logging, health endpoint, request correlation ID |

Recommended supporting libraries (allowed, keep dependencies pinned): `psycopg[binary]`, `djangorestframework-simplejwt`, `drf-spectacular`, `django-filter`, `django-environ`, `celery`, `django-celery-beat`, `redis`, `openpyxl` (XLSX export), `reportlab` or `weasyprint` (PDF export), `pytest`, `pytest-django`, `factory-boy`, `coverage`, `ruff`/`flake8`, `black`, `django-axes` (login throttling), `django-cors-headers`, `whitenoise` (optional).

---

## 4. Repository structure

Follow the PRD structure exactly:

```
ProcureSphere_360/
├── .github/                 # workflows/, PULL_REQUEST_TEMPLATE.md, ISSUE_TEMPLATE/
├── docs/                    # architecture, ERD, API, assumptions, RBAC matrix, review evidence
├── src/
│   ├── apps/                # domain modules (Section 6)
│   └── config/              # settings/ (base, dev, staging, prod), urls.py, wsgi.py, asgi.py, celery.py
├── templates/               # server-rendered UI (base layout, partials for HTMX)
├── static/                  # css, js, images
├── tests/                   # automated suites (unit, api, integration, rbac, db, security)
├── scripts/                 # maintenance / deployment helpers
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── manage.py
├── README.md
└── requirements.txt / pyproject.toml
```

`manage.py` lives at the root; make sure `src/` is on the Python path (e.g. add it to `sys.path` in `manage.py`, `wsgi.py`, `asgi.py`, `celery.py`).

### Per-app layout (every domain app)

```
apps/<module>/
├── models.py          # data + constraints (thin behavior)
├── services.py        # ALL business logic / workflow transitions (transaction.atomic)
├── selectors.py       # read/query logic (optimized, select_related/prefetch_related)
├── permissions.py     # DRF/permission classes tied to roles
├── serializers.py
├── api_views.py       # DRF viewsets (/api/v1/...)
├── views.py           # template views (HTMX partials)
├── forms.py
├── urls.py / api_urls.py
├── tasks.py           # Celery tasks
├── admin.py
├── migrations/
└── tests/ (or under /tests/<module>/)
```

**Rule:** views/serializers call `services.py`. Never put workflow logic in templates, serializers' `save()` hacks, or JS.

---

## 5. Roles & access model

| Role | Access / responsibility |
|---|---|
| Super Admin | System config, all masters, roles, policies, audit visibility |
| Requester | Create/track purchase requisitions for authorized departments/cost centers |
| Department Approver | Review PR / business justification within approval limits |
| Procurement Executive | Vendors, sourcing, negotiation, award, PO execution |
| Procurement Manager | Approval oversight, policy exceptions, vendor governance, sourcing approval |
| Finance / AP | Budget review, invoice validation, 3-way-match exceptions, payment readiness |
| Stores / Receiver | Goods/service receipt, inspection, quantity/quality acceptance |
| Legal / Contract Manager | Contract review, versioning, obligations, renewal, termination |
| Compliance / Auditor | **Read-only** audit, approvals, evidence, exports |
| Vendor User | Restricted self-service: onboarding, bid response, PO acknowledgement, selected documents |

Implementation requirements:
- Custom `User` model from day one (set `AUTH_USER_MODEL` before the first migration).
- `Role` / `Permission` model (or Django Groups + explicit permissions) with a documented **RBAC matrix** in `docs/rbac_matrix.md`.
- Object-level scoping: requesters see their own PRs; vendors see **only** their own data/bids/POs; approvers see items in their chain; department scoping via department/cost-center.
- Every sensitive endpoint and state-changing action needs positive **and** negative permission tests.

---

## 6. Modules → Django apps

| Module (PRD) | Suggested app | Key content |
|---|---|---|
| Accounts / RBAC | `accounts` | User, Role, Permission, login throttling, JWT |
| Organization / Cost Center | `organization` | Organization (legal entities/units), Department, location, CostCenter, fiscal periods, approval limits |
| Approval Policy Engine | `approvals` | ApprovalPolicy, ApprovalStep, ApprovalAction, rule-based routing, delegates |
| Vendor Onboarding & KYC / Vendor Master & Risk | `vendors` | Vendor, VendorContact, VendorDocument, VendorCategory, VendorRiskRecord, due-diligence checklist, hold/suspend/blacklist |
| Purchase Requisition | `requisitions` | PurchaseRequisition, PRLine, PRAttachment |
| RFQ/RFP Sourcing + Vendor Bid Portal + Evaluation & Award | `sourcing` | SourcingEvent, BidInvite, VendorBid, BidLine, BidEvaluation, Clarification, AwardDecision |
| Purchase Order | `orders` | PurchaseOrder, POLine, POAmendment, DeliverySchedule |
| Receipt / Inspection | `receipts` | GoodsReceipt, ReceiptLine, InspectionRecord, RejectionRecord |
| Invoice & 3-Way Match | `invoices` | SupplierInvoice, InvoiceLine, MatchResult, MatchException, PaymentStatus |
| Contract Lifecycle | `contracts` | Contract, ContractVersion, ContractMilestone, ContractObligation, ContractAlert, ContractDocument |
| Budget / Spend | `budgets` | Budget, BudgetReservation, SpendLedger |
| Supplier Performance | `scorecards` | VendorScorecard (from transactional indicators + manual review factors) |
| Notifications / Escalation | `notifications` | Notification (in-app + email), reminders, escalation, Celery tasks |
| Reports / Audit / Export | `reports`, `audit` | AuditLog (append-only), ExportJob, CSV/XLSX/PDF export, dashboards |
| Shared | `core` | Base models (timestamps, soft-state helpers), state-machine helper, file validators, middleware (request ID, audit context), health endpoint |

---

## 7. Workflows (state machines)

Implement each as an explicit transition table (allowed `from → to`, required role, guard conditions, side effects). Invalid transitions must raise a domain error and return a clear API/UI error. **Every transition writes an ApprovalAction and/or AuditLog** (actor, action, timestamp, comments, previous state, new state).

| Workflow | States |
|---|---|
| Vendor | DRAFT → SUBMITTED → KYC REVIEW → APPROVED / REJECTED → ACTIVE → ON HOLD / SUSPENDED |
| Purchase Requisition | DRAFT → SUBMITTED → MANAGER REVIEW → BUDGET REVIEW → APPROVED / REJECTED → SOURCING / PO |
| Sourcing Event (RFQ/RFP) | DRAFT → PUBLISHED → BID WINDOW → TECHNICAL REVIEW → COMMERCIAL REVIEW → AWARD APPROVAL → AWARDED / CANCELLED |
| Purchase Order | DRAFT → APPROVAL → ISSUED → ACKNOWLEDGED → PARTIAL RECEIPT → COMPLETED / CANCELLED |
| Invoice | RECEIVED → VALIDATION → 3-WAY MATCH → EXCEPTION / APPROVAL → READY FOR PAYMENT → PAID / REJECTED |
| Contract | DRAFT → LEGAL REVIEW → BUSINESS APPROVAL → ACTIVE → RENEWAL DUE → RENEWED / EXPIRED / TERMINATED |

---

## 8. Critical business rules

1. **Approval routing** is configurable and evaluated **server-side** from amount threshold, department, cost center, and transaction type (with optional exception routing). Multi-level chains; support delegate/unavailable approver.
2. **Budget validation** distinguishes **available**, **reserved/committed**, and **actual** spend. Block silent overspend; allow overspend only via a documented exception-approval route. PR approval creates a `BudgetReservation`; PO/invoice move reserved → committed → actual via `SpendLedger`.
3. **Sealed bids:** vendors can never see competitors' bids. Reviewer visibility is enforced by event stage (bids invisible to reviewers until the configured close/stage). Enforce in querysets/selectors and serializers, not just templates. Vendors may amend/withdraw only before close.
4. **PO amendments** create a new version and store prior values (never overwrite); approval required per configured rules.
5. **3-way match** compares PO + receipt/service entry + invoice, with configurable tolerance (**amount and/or percentage**), and creates explicit `MatchException` records with a resolution workflow and Finance approval.
6. **Contract renewal/expiry/SLA/milestone alerts** are generated by scheduled **Celery Beat** tasks, retaining execution/audit evidence (task run records).
7. **Supplier scorecards** are computed from real indicators (delivery, quality, price, responsiveness, compliance, SLA) plus manual review factors, with periodic review and trend history.
8. **Audit records** are append-only/immutable and capture: actor, action, object, previous state, updated state, source IP / user agent (where available), timestamp. Cover create/update/delete/approval/export/login. Enforce immutability (block update/delete at model/manager level and ideally via a DB trigger/permission revoke).
9. **Vendor lifecycle controls:** a suspended/blacklisted vendor cannot be invited, awarded, or issued POs; handle suspension while sourcing/PO transactions are open.

### Edge cases that MUST be handled and tested
- Partial deliveries; multiple receipts against one PO
- Multiple invoices against one PO where rules permit
- Rejected/returned goods and quantity correction **without destroying history**
- Vendor suspension with open sourcing events or POs
- Bid withdrawal/amendment before event close
- Approval delegate / approver unavailability
- Contract amendment changing price/SLA/term while preserving the prior version
- **Duplicate invoice-number detection within a vendor**

---

## 9. Data model (core entities)

Create normalized tables with FKs, `unique`/`check` constraints, and indexes on FK/filter/status/date columns.

- **Org/Access:** Organization, Department, CostCenter, User, Role, Permission, ApprovalPolicy, ApprovalStep
- **Vendor:** Vendor, VendorContact, VendorDocument, VendorCategory, VendorRiskRecord, VendorScorecard
- **Requisition:** PurchaseRequisition, PRLine, PRAttachment, ApprovalAction, BudgetReservation
- **Sourcing:** SourcingEvent, BidInvite, VendorBid, BidLine, BidEvaluation, Clarification, AwardDecision
- **Ordering:** PurchaseOrder, POLine, POAmendment, DeliverySchedule
- **Receipt:** GoodsReceipt, ReceiptLine, InspectionRecord, RejectionRecord
- **Invoice:** SupplierInvoice, InvoiceLine, MatchResult, MatchException, PaymentStatus
- **Contract:** Contract, ContractVersion, ContractMilestone, ContractObligation, ContractAlert, ContractDocument
- **Control:** Budget, SpendLedger, Notification, AuditLog, ExportJob

Conventions: `created_at/updated_at/created_by` on business models; `Decimal` (`DecimalField`) for all money/quantities — never float; explicit document numbering (PR-, RFQ-, PO-, GRN-, INV-, CON-) generated safely inside a transaction; `on_delete=PROTECT` for transactional references; timezone-aware datetimes.

---

## 10. API requirements

All under `/api/v1/`, versioned JSON, with validation, filtering, pagination, structured error format `{ "error": { "code", "message", "details" } }`, OpenAPI docs at `/api/docs/` (Swagger) and schema at `/api/schema/`.

| Group | Example endpoints | Access |
|---|---|---|
| Vendors | `/vendors/`, `/vendors/{id}/documents/`, `/vendors/{id}/scorecard/` | Procurement/Admin + restricted vendor self-service |
| Requisitions | `/requisitions/`, `/requisitions/{id}/submit/`, `/approve/` | Requester + policy-based approvers |
| Sourcing | `/sourcing-events/`, `/bids/`, `/evaluations/` | Procurement / evaluator / vendor scopes |
| PO / Receipts | `/purchase-orders/`, `/receipts/` | Procurement, Stores/Receiver, Finance read |
| Invoices | `/invoices/`, `/match/`, `/exceptions/` | Finance/AP |
| Contracts | `/contracts/`, `/renewals/`, `/alerts/` | Legal / procurement / business owner |
| Reports | `/reports/spend/`, `/aging/`, `/supplier-performance/` | Role-filtered read/export |

Also expose auth (`/api/v1/auth/token/`, refresh), notifications, approvals inbox, audit (read-only), and export-job endpoints as needed by the UI. State-changing actions are explicit POST action endpoints (`/submit/`, `/approve/`, `/reject/`, `/acknowledge/`…), never a raw `PATCH status`.

---

## 11. Reports & dashboards (all mandatory)

1. PR aging and approval-bottleneck report
2. Spend by vendor / category / department / cost center / period
3. Sourcing cycle time and bid participation
4. PO open / partial / closed status
5. Receipt/rejection and delivery performance
6. Invoice match-exception aging
7. Contract expiry / renewal / obligation
8. Supplier performance scorecard and trend
9. User/approval audit export with filters and immutable references

Exports: CSV / XLSX / PDF, filtered, RBAC-scoped, and **audited** (an export is itself an audit event; heavy exports run as Celery `ExportJob`s). Dashboards: pending approvals, PR aging, sourcing cycle time, spend by category/vendor, contract expiry, invoice exceptions, supplier performance.

---

## 12. Security baseline

- Django password hashing + strong password validators; login throttling/lockout
- CSRF on, ORM parameterization only (no raw SQL with string formatting), output escaping, secure session/cookie flags, restricted CORS, security headers, HTTPS-ready settings
- JWT/token auth for API; rate limiting (DRF throttling) on public and authenticated endpoints
- File uploads: validate extension **and** MIME type **and** size; sanitize/randomize storage path; store outside web root or serve via controlled view; vendors can only access their own files
- IDOR protection: always scope querysets by user/role/department; test with cross-user access attempts
- Secrets only via environment variables; run secret scanning (e.g. gitleaks) in CI
- OWASP Top 10 review checklist completed and stored in `docs/security_checklist.md`

---

## 13. Testing & quality (build tests as you build features)

| Type | Minimum validation |
|---|---|
| Unit | Models, validators, domain rules, state transitions, calculations (tolerance, budget, scoring) |
| API | Auth, authorization, success/failure codes, validation, filtering, pagination, file upload |
| Integration | Cross-module flows from creation → approval → execution → closure |
| RBAC | Positive and negative cases for every sensitive endpoint/action |
| Database | Constraints, transactions, rollback, migration integrity, referential integrity |
| UI/Responsive | Chrome/Edge/Firefox; desktop/tablet/mobile; no blocking overflow; keyboard focus |
| Security | CSRF, permission bypass/IDOR, injection, secret scan, upload validation |
| Performance | N+1 review (use `assertNumQueries`), paginated lists/reports, critical API timing, async job processing |
| Regression | Repeatable pack (`pytest -m regression`) run at each review gate |

**Defect severity:** Critical (security/RBAC/data corruption/unusable core flow — fix ≤12h) · High (major module failure or wrong financial state — ≤24h) · Medium (≤48h) · Low (before final acceptance). **Exit: zero open Critical/High.**

Commands the agent must be able to run (define them in README and keep them working):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py seed_demo_data       # realistic simulated data (never a substitute for real logic)
python manage.py runserver
celery -A config worker -l info -P solo    # Windows: use solo/threads pool
celery -A config beat -l info
pytest --cov=src
ruff check . ; black --check .
docker compose up --build
```

---

## 14. Day-90 acceptance demonstrations (build toward these from day one)

Write each as an automated integration test **and** a documented demo script in `docs/demo_scripts.md`.

1. Onboard a vendor with KYC documents → review → approve, with a complete audit trail.
2. Create a PR above a configured threshold → prove multi-level approval routing **and** budget reservation.
3. Run an RFQ/RFP with ≥3 simulated vendor bids → technical/commercial evaluation → award approval.
4. Generate a PO → amend it → record partial and final receipt → show preserved PO versions.
5. Submit an invoice → 3-way match → trigger a tolerance exception → resolve → move to payment-ready.
6. Create a contract with milestone/renewal dates → show scheduled (Celery Beat) notification execution.
7. Show supplier performance and spend dashboards and export an audit report tracing the complete transaction lifecycle.

---

## 15. Final exit conditions

- Zero open Critical and High defects
- All mandatory workflows demonstrated with database-backed evidence
- RBAC verified at backend/API level (tests exist)
- Fresh database + migrations run cleanly; Docker setup reproducible
- Clean commit/PR history; no exposed secrets
- API, ERD, setup, admin/user, and KT documentation complete

---

## 16. Handover package (keep `docs/` updated continuously, not at the end)

| Deliverable | Contents |
|---|---|
| Source | Repo URL, final tag/hash, branch list, full commit/PR history |
| Database | ER diagram (`docs/erd.*`), migration history, seed-data instructions, schema notes |
| API | OpenAPI/Swagger, auth approach, example requests/responses, error format |
| QA | Test plan, execution results, regression evidence, bug-closure matrix |
| Security | RBAC matrix, security checklist, secret-scan result, known-risk statement |
| Deployment | Dockerfile, env template, pinned dependencies, Nginx/Gunicorn notes, run commands, staging notes |
| Operations | Admin guide, user guide, backup/restore approach, troubleshooting |
| KT | Architecture walkthrough, module notes, deployment walkthrough, handover record |

Documentation must be **project-specific and accurate** — no copied boilerplate.

---

## 17. Git & workflow governance

- Branches: `main` (production/UAT-approved only) · `staging` (integration/review build) · `feature/<module-name>` · `bugfix/<ticket-id>`
- **Never commit directly to `main` or `staging`.** Merge via PR with review history and a meaningful title.
- Commit messages describe the functional change, e.g. `feat(requisitions): add multi-level approval routing by amount threshold`. Use `feat|fix|test|docs|refactor|chore(scope): summary`. Small, frequent commits.
- Do not rewrite history after a submitted checkpoint.
- CI must pass (lint + tests + build) before merge.
- Each PR: description, requirement IDs covered, tests added, migration notes, screenshots for UI changes.
- Periodic checkpoint submission (per the PRD template): Project, Submission Day, GitHub URL, Branch, Commit/Tag, PR Summary, CI Status, Tests (total/pass/fail/coverage), Staging URL, Open Defects (C/H/M/L), Next Scope. Store in `docs/review_evidence/`.

---

## 18. Execution plan (~45-day window, target ≤ 40)

Work in this order; each phase ends with green CI, a tagged build, and an updated tracker (Section 20). Parallelize across the 7-person team by module ownership, but respect the dependency order.

**Phase 0 — Foundation (Days 1–4)**
Repo scaffold, settings split, Docker/compose (Postgres, Redis), custom User, CI workflow, structured logging, request-ID middleware, health endpoint, base templates/design system, Celery wiring, OpenAPI setup, `core` state-machine + audit helpers, `docs/` skeleton.

**Phase 1 — Masters, Access, Vendors, Requisitions (Days 5–14)**
RBAC + login throttling + JWT → Organization/Department/CostCenter/fiscal periods/approval limits → Approval Policy Engine → Vendor onboarding portal + KYC + Vendor Master/risk/hold/suspend → Budget model + reservations → Purchase Requisition + multi-level approval + attachments. *(Demo 1 and 2 must pass.)*

**Phase 2 — Sourcing, PO, Receipt, Contracts (Days 15–26)**
RFQ/RFP events, invitations, clarifications, sealed vendor bid portal, evaluation & award approval → PO generation, versioning/amendment, acknowledgement → Goods/service receipt, inspection, rejection → Contract lifecycle, versions, milestones, document vault → Notification engine (in-app + email). *(Demo 3, 4 and part of 6 must pass.)*

**Phase 3 — Invoice, Spend, Scorecards, Reports (Days 27–36)**
Invoice capture + duplicate detection + 3-way match + tolerance + exceptions → Budget/spend ledger (reserved→committed→actual) → Supplier scorecards → Celery Beat contract/SLA/overdue-approval alerts and escalations → Dashboards, all 9 reports, CSV/XLSX/PDF export jobs, full audit-trail views. *(Demo 5, 6, 7 must pass.)*

**Phase 4 — Hardening & Delivery (Days 37–45)**
Edge-case coverage, RBAC/IDOR/security tests, N+1 and index tuning, upload validation review, OWASP checklist, secret scan, regression pack, cross-browser/responsive pass, defect closure (zero Critical/High), Nginx/Gunicorn deployment files, full documentation, KT material, final tag.

---

## 19. How an agent should work (protocol)

**Before starting any task**
1. Read this file fully. Read `docs/PROGRESS.md` (or Section 20) to see what's done/in-progress. Pick the **next unchecked item in dependency order** unless the human specifies otherwise.
2. Inspect the existing code before writing new code — reuse `core` helpers (state machine, audit, base models, validators). Do not duplicate patterns.
3. If a requirement is ambiguous, don't stall and don't invent scope: choose the minimal PRD-faithful assumption, record it in `docs/assumptions.md`, and continue.

**While implementing a feature (vertical slice, every time)**
1. Model + constraints + migration
2. Service layer with transition/guard rules + audit/approval records
3. Permissions (RBAC) + selectors that scope data
4. DRF serializers/viewsets + OpenAPI annotations
5. Template views/HTMX partials with real forms, validation errors, and success only after persistence
6. Celery tasks if the feature needs async/scheduled work
7. Tests: unit + API + RBAC (positive/negative) + one integration flow; check query counts
8. Docs: update API docs / ERD / RBAC matrix if touched

**Definition of Done (per feature)**
- Works end-to-end on PostgreSQL through the UI **and** API
- Backend RBAC enforced and tested (including a negative test)
- State changes validated and audited
- Migrations apply on a fresh DB; `makemigrations --check` is clean
- Tests pass; lint/format pass; no new N+1
- No secrets, no debug prints, no TODO stubs posing as features
- Tracker + docs updated; conventional commit made on a feature branch

**Before finishing a session**
- Run the full test suite and linters; fix failures instead of skipping tests.
- Update `docs/PROGRESS.md` (what was done, what's next, known issues, decisions).
- Leave the working tree in a committable state and summarize changes, tests run, and next steps for the human.

**Things agents must never do**
- Delete or weaken tests to get green; disable CSRF/auth to "make it work"
- Hardcode data to fake a workflow; return fake success responses
- Add libraries or frameworks outside the approved stack without asking
- Run destructive commands (dropping the DB, `git reset --hard`, force-push, deleting migrations) without explicit human approval
- Edit already-applied migrations — add a new migration instead
- Commit `.env`, credentials, or generated bulk data

---

## 20. Progress tracker (agents: keep this current)

Mark `[x]` only when the Definition of Done is met.

**Phase 0 — Foundation**
- [ ] Repo scaffold + settings split + `.env.example`
- [ ] Docker / docker-compose (Postgres, Redis, web, worker, beat)
- [ ] GitHub Actions CI (lint, test, build, secret scan)
- [ ] Custom User + auth (session + JWT) + login throttling
- [ ] Core: state-machine helper, AuditLog (append-only), base models, file validators, request-ID middleware, health endpoint
- [ ] Base UI layout (Bootstrap 5 or Tailwind) + HTMX setup
- [ ] OpenAPI/Swagger docs
- [ ] Celery + Beat wiring

**Phase 1 — Masters, Vendors, PR**
- [ ] RBAC roles/permissions + RBAC matrix doc
- [ ] Organization / Department / CostCenter / fiscal periods / approval limits
- [ ] Approval Policy Engine (amount, department, cost center, transaction type, delegate)
- [ ] Vendor registration portal + KYC documents + due-diligence checklist
- [ ] Vendor approval / hold / suspension / blacklist + risk records + history
- [ ] Budget + BudgetReservation
- [ ] Purchase Requisition (lines, attachments, budget check, multi-level approval)
- [ ] Demo 1 & 2 automated tests passing

**Phase 2 — Sourcing, PO, Receipt, Contract**
- [ ] Sourcing event (RFQ/RFP) + invitations + clarifications
- [ ] Vendor bid portal + sealed visibility + amend/withdraw before close
- [ ] Technical/commercial evaluation + weighted scoring + award approval
- [ ] Purchase Order + versioning/amendments + delivery schedule + vendor acknowledgement
- [ ] Goods/service receipt + partial receipts + inspection + rejection
- [ ] Contract lifecycle + versions + milestones + obligations + document vault
- [ ] Notification engine (in-app + email)
- [ ] Demo 3 & 4 automated tests passing

**Phase 3 — Invoice, Spend, Analytics**
- [ ] Invoice capture + duplicate detection + validation
- [ ] 3-way match + tolerance config + MatchException workflow + finance approval
- [ ] SpendLedger (reserved → committed → actual) + budget alerts + exception approval
- [ ] Supplier scorecards (transactional + manual factors)
- [ ] Celery Beat: contract/SLA alerts, overdue-approval reminders, escalations (+ run evidence)
- [ ] Dashboards + 9 mandatory reports
- [ ] CSV / XLSX / PDF exports (ExportJob, audited)
- [ ] Full audit-trail views
- [ ] Demo 5, 6, 7 automated tests passing

**Phase 4 — Hardening & Handover**
- [ ] All edge cases in Section 8 covered by tests
- [ ] RBAC + IDOR + security test suite; OWASP checklist; secret-scan clean
- [ ] Performance pass (N+1, indexes, pagination)
- [ ] Cross-browser + responsive verification
- [ ] Regression pack; zero open Critical/High
- [ ] Nginx/Gunicorn deployment files; clean-environment Docker verification
- [ ] Complete handover package (Section 16) + KT
- [ ] Final release tag

---

## 21. Session log (append newest at top)

```
YYYY-MM-DD | agent/tool | branch | what was done | tests run | next step | blockers
```

_(No sessions logged yet.)_

---

## Cursor Cloud specific instructions

Cloud Agent VMs use native PostgreSQL and Redis (not Docker Compose). After checkout, run:

```bash
bash scripts/cloud-agent-install.sh   # venv, pip, collectstatic (idempotent)
bash scripts/cloud-agent-start.sh       # start DB/Redis, migrate, seed if empty, Django :8000
```

- **Demo login:** `admin@hpe.com` / `Password123!` (see `docs/docker_setup.md` for all roles).
- **Health:** `curl http://127.0.0.1:8000/health/`
- **Lint/tests:** `source .venv/bin/activate && export PYTHONPATH=src DJANGO_SETTINGS_MODULE=config.settings.dev` then `ruff check .`, `black --check .`, `pytest --cov=src`
- **Celery (optional):** `celery -A config worker -l info` and `celery -A config beat -l info` with Redis running.
