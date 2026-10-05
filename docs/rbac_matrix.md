# Role-Based Access Control (RBAC) Matrix — ProcureSphere 360

PRD Reference: HPE-PRD-2026-PROC01 (VER-1.0-DRAFT)  
Primary Auth Model: Django Custom User + Role/Permission Scoping + Object-Level Ownership Checks

---

## 1. System Roles Summary

1. **Super Admin (`SUPER_ADMIN`)**: System configuration, master data management, global roles, approval policy definitions, full audit log access.
2. **Requester (`REQUESTER`)**: Create, submit, track purchase requisitions within authorized department / cost center scope.
3. **Department Approver (`DEPT_APPROVER`)**: Review, approve, reject purchase requisitions within configured approval limit and department chain.
4. **Procurement Executive (`PROC_EXEC`)**: Vendor onboarding review, RFQ/RFP event management, bid evaluation, purchase order creation/amendment, goods receipt tracking.
5. **Procurement Manager (`PROC_MGR`)**: High-value approval oversight, vendor governance/status changes (hold/suspend/blacklist), sourcing award approval, PO approvals.
6. **Finance / AP (`FINANCE_AP`)**: Cost center budget oversight, invoice capture & validation, 3-way match exception resolution, payment status management.
7. **Stores / Receiver (`STORES_RECEIVER`)**: Goods Receipt Note (GRN) entry, service entry processing, quality inspection logging, rejection records.
8. **Legal / Contract Manager (`LEGAL_MGR`)**: Contract creation, legal review, contract versioning, obligations management, renewal/termination governance.
9. **Compliance / Auditor (`AUDITOR`)**: Read-only system-wide audit access, approval history verification, export generation.
10. **Vendor User (`VENDOR_USER`)**: Self-service onboarding portal, bid response submission (sealed), PO acknowledgement, invoice submission, milestone proof upload.

---

## 2. Module Permission Matrix

Legend: **C** = Create, **R** = Read, **U** = Update, **D** = Delete, **A** = Approve/Action, **V** = Vendor Scoped Read/Submit Only

| Module / Feature | SUPER_ADMIN | REQUESTER | DEPT_APPROVER | PROC_EXEC | PROC_MGR | FINANCE_AP | STORES_RECEIVER | LEGAL_MGR | AUDITOR | VENDOR_USER |
|---|---|---|---|---|---|---|---|---|---|---|
| User & System Config | C R U D | - | - | - | - | - | - | - | R | - |
| Org, Dept & Cost Centers | C R U D | R | R | R | R | R | R | R | R | - |
| Approval Policies | C R U D | R | R | R | R | R | - | - | R | - |
| Vendor Onboarding & KYC | C R U | - | - | C R U A | C R U A | R | - | - | R | C R U (Self) |
| Vendor Risk & Status | C R U | - | - | R U | C R U A | R | - | - | R | R (Self) |
| Purchase Requisitions | R D | C R U (Dept) | R A (Dept) | R | R A | R | - | - | R | - |
| Budget & Spend Ledger | C R U | R (Dept) | R (Dept) | R | R | C R U A | - | - | R | - |
| Sourcing Events (RFQ/RFP) | C R U D | - | - | C R U A | C R U A | R | - | R | R | V (Invited) |
| Vendor Sealed Bids | R | - | - | R (Post-Close) | R (Post-Close) | - | - | - | R | C R U (Self, Bid Window) |
| Purchase Orders | C R U D | R (Dept) | R (Dept) | C R U | C R U A | R | R | R | R | R A (Acknowledged) |
| Goods Receipt & Inspection | C R U | - | - | R | R | R | C R U A | - | R | R (Self POs) |
| Invoices & 3-Way Match | C R U | - | - | R | R | C R U A | R | - | R | C R (Self) |
| Contracts & Versioning | C R U D | - | - | R | R | R | - | C R U A | R | R (Self) |
| Supplier Scorecards | C R U | - | - | R U | C R U A | R | R | - | R | R (Self Summary) |
| Audit Logs & Exports | R | - | - | - | - | - | - | - | R | - |

---

## 3. Object-Level Scoping Rules

- **Requester**: Restricted to items created by user or owned by user's assigned Department / Cost Center.
- **Vendor User**: Enforced at database selector level (`Vendor.objects.filter(id=user.vendor_id)`). Bids from competing vendors return `403 Forbidden` / filtered out at ORM query level.
- **Approver**: Restricted to requisitions/POs currently assigned in approval chain or department scope. A department approver can confirm or reject business need only for their own department, or for a department whose approver has delegated to them. Superuser flag alone does not grant that action.
- **Auditor**: Global read-only access; state-changing endpoints return `403 Forbidden`.
