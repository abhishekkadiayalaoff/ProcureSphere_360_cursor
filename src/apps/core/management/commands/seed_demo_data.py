from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalPolicy
from apps.approvals.services import add_approval_step_service, create_approval_policy_service
from apps.budgets.models import Budget, BudgetReservation, SpendLedger
from apps.budgets.services import allocate_budget_service
from apps.contracts.models import Contract, ContractAlert, ContractMilestone
from apps.contracts.services import (
    activate_contract_service,
    add_contract_milestone_service,
    create_contract_service,
)
from apps.invoices.models import InvoiceLine, MatchException, SupplierInvoice
from apps.invoices.services import (
    create_supplier_invoice_service,
    mark_invoice_paid_service,
    run_3_way_match_service,
)
from apps.orders.models import POAmendment, POLine, PurchaseOrder
from apps.orders.services import (
    acknowledge_purchase_order_service,
    generate_purchase_order_service,
)
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.models import GoodsReceipt, InspectionRecord, ReceiptLine, RejectionRecord
from apps.receipts.services import create_goods_receipt_service
from apps.requisitions.models import PRAttachment, PRLine, PurchaseRequisition
from apps.requisitions.services import (
    create_purchase_requisition_service,
    submit_purchase_requisition_service,
)
from apps.scorecards.models import VendorScorecard
from apps.scorecards.services import calculate_vendor_scorecard_service
from apps.vendors.models import Vendor, VendorCategory, VendorDocument
from apps.vendors.services import (
    approve_vendor_service,
    register_vendor_service,
    submit_vendor_kyc_service,
)


class Command(BaseCommand):
    help = "Seeds ProcureSphere 360 database with realistic demonstration data for all core ERP workflows."

    def get_or_create_user(self, email, role, is_superuser=True):
        user = User.objects.filter(email=email).first()
        if not user:
            user = User.objects.create_user(email=email, role=role)
        user.role = role
        user.set_password("Password123!")
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.save()
        return user

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting ProcureSphere 360 database seeding..."))

        # Clean existing transactional data for clean idempotent seed
        VendorScorecard.objects.all().delete()
        ContractAlert.objects.all().delete()
        ContractMilestone.objects.all().delete()
        Contract.objects.all().delete()
        MatchException.objects.all().delete()
        InvoiceLine.objects.all().delete()
        SupplierInvoice.objects.all().delete()
        InspectionRecord.objects.all().delete()
        RejectionRecord.objects.all().delete()
        ReceiptLine.objects.all().delete()
        GoodsReceipt.objects.all().delete()
        POAmendment.objects.all().delete()
        POLine.objects.all().delete()
        PurchaseOrder.objects.all().delete()
        BudgetReservation.objects.all().delete()
        SpendLedger.objects.all().delete()
        PRAttachment.objects.all().delete()
        PRLine.objects.all().delete()
        PurchaseRequisition.objects.all().delete()
        Budget.objects.all().delete()

        # 1. Initialize System Roles
        roles_data = [
            (Role.SUPER_ADMIN, "Super Admin"),
            (Role.REQUESTER, "Requester"),
            (Role.DEPT_APPROVER, "Department Approver"),
            (Role.PROC_EXEC, "Procurement Executive"),
            (Role.PROC_MGR, "Procurement Manager"),
            (Role.FINANCE_AP, "Finance / AP Specialist"),
            (Role.STORES_RECEIVER, "Stores / Receiver"),
            (Role.LEGAL_MGR, "Legal / Contract Manager"),
            (Role.AUDITOR, "Compliance Auditor"),
            (Role.VENDOR_USER, "Vendor Portal User"),
        ]
        roles = {}
        for code, name in roles_data:
            role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
            roles[code] = role

        # 2. Seed Users
        self.get_or_create_user("admin@hpe.com", roles[Role.SUPER_ADMIN], is_superuser=True)
        requester = self.get_or_create_user("requester@hpe.com", roles[Role.REQUESTER])
        approver = self.get_or_create_user("approver@hpe.com", roles[Role.DEPT_APPROVER])
        proc_exec = self.get_or_create_user("procexec@hpe.com", roles[Role.PROC_EXEC])
        proc_mgr = self.get_or_create_user("procmgr@hpe.com", roles[Role.PROC_MGR])
        finance_user = self.get_or_create_user("finance@hpe.com", roles[Role.FINANCE_AP])
        receiver_user = self.get_or_create_user("receiver@hpe.com", roles[Role.STORES_RECEIVER])
        legal_user = self.get_or_create_user("legal@hpe.com", roles[Role.LEGAL_MGR])
        self.get_or_create_user("auditor@hpe.com", roles[Role.AUDITOR])
        vendor_user = self.get_or_create_user("vendoruser@cisco.com", roles[Role.VENDOR_USER])

        # 3. Organization Master Data
        org, _ = Organization.objects.get_or_create(
            code="HPE-GLOBAL", defaults={"name": "Hewlett Packard Enterprise"}
        )
        dept_it, _ = Department.objects.get_or_create(
            organization=org, code="IT-OPS", defaults={"name": "IT Infrastructure Operations"}
        )
        _, _ = Department.objects.get_or_create(
            organization=org, code="OPS", defaults={"name": "Global Operations"}
        )

        requester.department = dept_it
        requester.save(update_fields=["department"])
        approver.department = dept_it
        approver.save(update_fields=["department"])

        cost_center, _ = CostCenter.objects.get_or_create(
            department=dept_it,
            code="CC-IT-101",
            defaults={"name": "Compute & Cloud Center", "manager": approver},
        )

        today = timezone.now().date()
        fiscal_period, _ = FiscalPeriod.objects.get_or_create(
            organization=org,
            year=2026,
            period_number=1,
            defaults={
                "name": "FY2026-Q1",
                "start_date": today - timezone.timedelta(days=30),
                "end_date": today + timezone.timedelta(days=150),
            },
        )

        # 4. Budget Allocation
        budget = allocate_budget_service(
            cost_center=cost_center,
            fiscal_period=fiscal_period,
            allocated_amount=Decimal("250000.00"),
        )
        self.stdout.write(f"Budget allocated for {cost_center.code}: ${budget.allocated_amount}")

        # 5. Vendor Master & Onboarding
        cat_hw, _ = VendorCategory.objects.get_or_create(
            code="HW-SERVERS", defaults={"name": "Server Hardware & Appliances"}
        )
        cat_net, _ = VendorCategory.objects.get_or_create(
            code="NET-EQUIP", defaults={"name": "Networking Equipment"}
        )

        vendor1 = Vendor.objects.filter(tax_identification_number="TAX-CISCO-991").first()
        if not vendor1:
            vendor1 = register_vendor_service(
                legal_name="Cisco Systems Inc",
                tax_identification_number="TAX-CISCO-991",
                category=cat_net,
                email="enterprise-sales@cisco.com",
                address="170 West Tasman Dr, San Jose, CA",
                created_by_user=proc_exec,
            )
            VendorDocument.objects.create(
                vendor=vendor1,
                document_type=VendorDocument.DOC_TYPE_CERT,
                title="Certificate of Incorporation",
                file=SimpleUploadedFile(
                    "cert.pdf", b"%PDF-1.4 Content", content_type="application/pdf"
                ),
                is_verified=True,
            )
            submit_vendor_kyc_service(vendor=vendor1, user=proc_exec)
            approve_vendor_service(vendor=vendor1, manager=proc_mgr, notes="Verified & Approved")

        vendor_user.vendor = vendor1
        vendor_user.save(update_fields=["vendor"])

        vendor2 = Vendor.objects.filter(tax_identification_number="TAX-INTEL-882").first()
        if not vendor2:
            register_vendor_service(
                legal_name="Intel Corporation",
                tax_identification_number="TAX-INTEL-882",
                category=cat_hw,
                email="sales@intel.com",
                address="2200 Mission College Blvd, Santa Clara, CA",
                created_by_user=proc_exec,
            )

        # 6. Requisition & Budget Reservation
        if not ApprovalPolicy.objects.filter(name="IT Operations PR Policy").exists():
            policy = create_approval_policy_service(
                name="IT Operations PR Policy",
                module=ApprovalPolicy.MODULE_PR,
                min_amount=Decimal("0.00"),
                department=dept_it,
            )
            add_approval_step_service(
                policy=policy,
                step_number=1,
                approver_role=roles[Role.DEPT_APPROVER],
                specific_approver=approver,
                description="Department approver confirms business need",
            )
        pr = create_purchase_requisition_service(
            title="Enterprise Core Routers Upgrade",
            justification="Upgrade data center core networking switches to support high-throughput cloud cluster.",
            requester=requester,
            department=dept_it,
            cost_center=cost_center,
            requested_delivery_date=today + timezone.timedelta(days=30),
            line_items=[
                {
                    "item_description": "Cisco Catalyst 9600 Switch",
                    "quantity": 2,
                    "estimated_unit_price": 25000.00,
                },
                {
                    "item_description": "100G SFP Transceiver Module",
                    "quantity": 10,
                    "estimated_unit_price": 1200.00,
                },
            ],
        )
        submit_purchase_requisition_service(requisition=pr, user=requester)

        # 7. Purchase Order Execution
        po = generate_purchase_order_service(
            vendor=vendor1,
            cost_center=cost_center,
            requisition=pr,
            line_items=[
                {
                    "item_description": "Cisco Catalyst 9600 Switch",
                    "quantity": 2,
                    "unit_price": 25000.00,
                },
                {
                    "item_description": "100G SFP Transceiver Module",
                    "quantity": 10,
                    "unit_price": 1200.00,
                },
            ],
            created_by_user=proc_exec,
        )
        acknowledge_purchase_order_service(po=po, vendor_user=requester)

        # 8. Goods Receipt Note (GRN)
        create_goods_receipt_service(
            po=po,
            received_by=receiver_user,
            receipt_items=[
                {"po_line": po.lines.all()[0], "quantity_received": 2, "quantity_accepted": 2},
                {"po_line": po.lines.all()[1], "quantity_received": 10, "quantity_accepted": 10},
            ],
        )

        # 9. Invoice & 3-Way Match
        invoice = create_supplier_invoice_service(
            vendor=vendor1,
            po=po,
            invoice_number=f"INV-CISCO-{timezone.now().strftime('%M%S')}",
            invoice_date=today,
            due_date=today + timezone.timedelta(days=30),
            line_items=[
                {
                    "po_line": po.lines.all()[0],
                    "item_description": "Cisco Catalyst 9600 Switch",
                    "quantity": 2,
                    "unit_price": 25000.00,
                },
                {
                    "po_line": po.lines.all()[1],
                    "item_description": "100G SFP Transceiver Module",
                    "quantity": 10,
                    "unit_price": 1200.00,
                },
            ],
            created_by_user=finance_user,
        )
        run_3_way_match_service(invoice=invoice, user=finance_user)
        mark_invoice_paid_service(invoice=invoice, user=finance_user)

        # 10. Contract & Scorecard
        contract = create_contract_service(
            title="Cisco Enterprise Hardware Support SLA",
            vendor=vendor1,
            contract_value=Decimal("62000.00"),
            start_date=today - timezone.timedelta(days=60),
            end_date=today + timezone.timedelta(days=300),
            renewal_notice_days=30,
            contract_owner=legal_user,
        )
        activate_contract_service(contract=contract, user=legal_user)
        add_contract_milestone_service(
            contract=contract,
            title="Quarterly SLA Audit",
            due_date=today + timezone.timedelta(days=14),
            amount=Decimal("15000.00"),
        )

        calculate_vendor_scorecard_service(
            vendor=vendor1,
            evaluation_period="Q1-2026",
            evaluated_by_user=proc_mgr,
            comments="Excellent 100% GRN delivery performance.",
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Database seeding completed successfully! Demonstration dataset active."
            )
        )
