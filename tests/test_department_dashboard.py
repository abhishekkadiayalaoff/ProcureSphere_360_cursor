from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalAction, ApprovalPolicy
from apps.approvals.services import (
    add_approval_step_service,
    create_approval_policy_service,
    set_approval_delegate_service,
)
from apps.audit.models import AuditLog
from apps.budgets.models import BudgetReservation
from apps.budgets.services import allocate_budget_service
from apps.notifications.models import Notification
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PRAttachment, PurchaseRequisition
from apps.requisitions.services import (
    DECISION_CONFIRM_NEED,
    DECISION_REJECT_NEED,
    LIMIT_EXCEEDED_MESSAGE,
    WorkflowConflict,
    create_purchase_requisition_service,
    department_need_review_service,
    submit_purchase_requisition_service,
)


def _org_setup(db_roles, dept_code="DEPT-IT"):
    today = timezone.now().date()
    org = Organization.objects.create(name="HPE", code=f"HPE-{dept_code}")
    dept = Department.objects.create(organization=org, name="Infrastructure", code=dept_code)
    approver = User.objects.create_user(
        email=f"approver-{dept_code.lower()}@hpe.com",
        password="Password123!",
        role=db_roles[Role.DEPT_APPROVER],
        department=dept,
        is_superuser=True,
    )
    requester = User.objects.create_user(
        email=f"requester-{dept_code.lower()}@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        department=dept,
        is_superuser=True,
    )
    cost_center = CostCenter.objects.create(
        department=dept, code=f"CC-{dept_code}", name="Cloud", manager=approver
    )
    period = FiscalPeriod.objects.create(
        organization=org,
        year=today.year,
        period_number=1,
        name=f"FY-{dept_code}",
        start_date=today - timedelta(days=10),
        end_date=today + timedelta(days=90),
    )
    allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=period,
        allocated_amount=Decimal("100000.00"),
    )
    policy = create_approval_policy_service(
        name=f"{dept_code} PR need review",
        module=ApprovalPolicy.MODULE_PR,
        min_amount=Decimal("0.00"),
        department=dept,
    )
    add_approval_step_service(
        policy=policy,
        step_number=1,
        approver_role=db_roles[Role.DEPT_APPROVER],
        specific_approver=approver,
    )
    return dept, approver, requester, cost_center


def _submitted_pr(requester, dept, cost_center, title="Core switch refresh"):
    pr = create_purchase_requisition_service(
        title=title,
        justification="Replace end-of-support core switches.",
        requester=requester,
        department=dept,
        cost_center=cost_center,
        requested_delivery_date=timezone.now().date() + timedelta(days=21),
        line_items=[
            {
                "item_description": "Switch",
                "quantity": 2,
                "estimated_unit_price": "1500.00",
            }
        ],
    )
    return submit_purchase_requisition_service(requisition=pr, user=requester)


@pytest.mark.django_db
def test_department_confirm_need_moves_to_budget_review(db_roles):
    dept, approver, requester, cost_center = _org_setup(db_roles)
    pr = _submitted_pr(requester, dept, cost_center)

    pr = department_need_review_service(
        requisition=pr,
        approver=approver,
        decision=DECISION_CONFIRM_NEED,
        comments="Need is justified for the data center refresh.",
    )

    assert pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW
    action = ApprovalAction.objects.filter(
        target_object_id=pr.id, action=ApprovalAction.ACTION_APPROVE
    ).latest("created_at")
    assert action.actor == approver
    assert action.new_state == PurchaseRequisition.STATUS_BUDGET_REVIEW
    assert BudgetReservation.objects.get(requisition=pr).status == BudgetReservation.STATUS_RESERVED


@pytest.mark.django_db
def test_department_reject_documents_reason_and_releases_budget(db_roles):
    dept, approver, requester, cost_center = _org_setup(db_roles, "DEPT-OPS")
    pr = _submitted_pr(requester, dept, cost_center, title="Unused lab gear")

    with pytest.raises(ValidationError):
        department_need_review_service(
            requisition=pr,
            approver=approver,
            decision=DECISION_REJECT_NEED,
            comments="  ",
        )

    pr = department_need_review_service(
        requisition=pr,
        approver=approver,
        decision=DECISION_REJECT_NEED,
        comments="Need is not justified. Existing stock covers this request.",
    )

    assert pr.status == PurchaseRequisition.STATUS_REJECTED
    reservation = BudgetReservation.objects.get(requisition=pr)
    assert reservation.status == BudgetReservation.STATUS_RELEASED
    reservation.budget.refresh_from_db()
    assert reservation.budget.reserved_amount == Decimal("0.00")


@pytest.mark.django_db
def test_requester_and_other_department_cannot_review(db_roles):
    dept, approver, requester, cost_center = _org_setup(db_roles, "DEPT-FIN")
    other_dept, other_approver, _other_requester, _other_cc = _org_setup(db_roles, "DEPT-HR")
    pr = _submitted_pr(requester, dept, cost_center)

    with pytest.raises(PermissionDenied):
        department_need_review_service(
            requisition=pr,
            approver=requester,
            decision=DECISION_CONFIRM_NEED,
            comments="Trying to self-approve.",
        )
    with pytest.raises(PermissionDenied):
        department_need_review_service(
            requisition=pr,
            approver=other_approver,
            decision=DECISION_CONFIRM_NEED,
        )
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_MANAGER_REVIEW
    assert other_dept.code == "DEPT-HR"
    assert approver.department_id == dept.id


@pytest.mark.django_db
def test_active_delegate_can_confirm_need(db_roles):
    dept, approver, requester, cost_center = _org_setup(db_roles, "DEPT-DEL")
    delegate = User.objects.create_user(
        email="delegate-dept-del@hpe.com",
        password="Password123!",
        role=db_roles[Role.DEPT_APPROVER],
    )
    today = timezone.now().date()
    set_approval_delegate_service(
        approver=approver,
        delegate=delegate,
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=5),
        reason="Approver unavailable",
    )
    pr = _submitted_pr(requester, dept, cost_center, title="Delegated review")
    pr = department_need_review_service(
        requisition=pr,
        approver=delegate,
        decision=DECISION_CONFIRM_NEED,
        comments="Covering for the department approver.",
    )
    assert pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW


@pytest.mark.django_db
def test_department_dashboard_session_flow(db_roles):
    dept, approver, requester, cost_center = _org_setup(db_roles, "DEPT-UI")
    pr = _submitted_pr(requester, dept, cost_center, title="Session dashboard routers")
    client = Client()

    anonymous = client.get(reverse("department_dashboard"))
    assert anonymous.status_code == 302
    assert "/login/" in anonymous.url

    client.force_login(requester)
    denied = client.get(reverse("department_dashboard"))
    assert denied.status_code == 403

    blocked = client.post(
        reverse("department_need_review", args=[pr.id]),
        {"decision": DECISION_CONFIRM_NEED, "comments": "no"},
    )
    assert blocked.status_code == 403
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_MANAGER_REVIEW

    client.force_login(approver)
    page = client.get(reverse("home"))
    assert page.status_code == 200
    body = page.content.decode()
    assert "Department Approval" in body
    assert pr.pr_number in body
    assert "Review and process purchase requisitions awaiting your approval." in body
    assert "Session dashboard routers" in body

    confirmed = client.post(
        reverse("department_need_review", args=[pr.id]),
        {
            "decision": DECISION_CONFIRM_NEED,
            "comments": "Need confirmed from the department dashboard.",
        },
    )
    assert confirmed.status_code == 302
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW

    queue = client.get(reverse("department_dashboard"))
    assert (
        "sent to budget review" in queue.content.decode().lower()
        or pr.pr_number in queue.content.decode()
    )
    assert "Waiting on budget review." in queue.content.decode()


@pytest.mark.django_db
def test_department_review_api_rbac(db_roles, api_client):
    dept, approver, requester, cost_center = _org_setup(db_roles, "DEPT-API")
    pr = _submitted_pr(requester, dept, cost_center, title="API review item")
    url = f"/api/v1/requisitions/{pr.id}/department-review/"

    api_client.force_login(requester)
    denied = api_client.post(
        url, {"decision": DECISION_CONFIRM_NEED, "comments": "no"}, format="json"
    )
    assert denied.status_code == 403

    api_client.force_login(approver)
    missing_reason = api_client.post(
        url, {"decision": DECISION_REJECT_NEED, "comments": ""}, format="json"
    )
    assert missing_reason.status_code == 400

    accepted = api_client.post(
        url,
        {"decision": DECISION_CONFIRM_NEED, "comments": "Confirmed via API."},
        format="json",
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == PurchaseRequisition.STATUS_BUDGET_REVIEW
    assert accepted.json()["next_step"] == "Budget review"


@pytest.mark.django_db
def test_queue_search_limit_and_conflict(db_roles, api_client):
    dept, approver, requester, cost_center = _org_setup(db_roles, "DEPT-Q")
    pr = _submitted_pr(requester, dept, cost_center, title="Unique router search token")
    Notification.objects.get(recipient=approver, title__contains=pr.pr_number)

    api_client.force_login(approver)
    queue = api_client.get("/api/v1/requisitions/approval-queue/", {"q": "Unique router"})
    assert queue.status_code == 200
    body = queue.json()
    assert body["kpis"]["pending_approval"] >= 1
    assert body["results"][0]["pr_number"] == pr.pr_number
    assert body["results"][0]["next_step"] == "Department approver reviews need"

    missed = api_client.get("/api/v1/requisitions/approval-queue/", {"q": "does-not-exist"})
    assert missed.json()["results"] == []

    detail = api_client.get(f"/api/v1/requisitions/{pr.id}/approval-detail/")
    assert detail.status_code == 200
    assert detail.json()["authority"]["within_limit"] is True

    pr.status = PurchaseRequisition.STATUS_APPROVED
    pr.save(update_fields=["status"])
    conflict = api_client.post(
        f"/api/v1/requisitions/{pr.id}/department-review/",
        {"decision": DECISION_CONFIRM_NEED, "comments": "late"},
        format="json",
    )
    assert conflict.status_code == 409

    capped = ApprovalPolicy.objects.get(department=dept)
    capped.max_amount = Decimal("10.00")
    capped.save(update_fields=["max_amount"])
    other = _submitted_pr(requester, dept, cost_center, title="Above the cap")
    with pytest.raises(ValidationError, match="Approval limit exceeded"):
        department_need_review_service(
            requisition=other,
            approver=approver,
            decision=DECISION_CONFIRM_NEED,
            comments="too large",
        )
    assert LIMIT_EXCEEDED_MESSAGE
    audit = AuditLog.objects.filter(target_object_id=str(pr.id), action=AuditLog.ACTION_UPDATE)
    assert audit.exists()


@pytest.mark.django_db
def test_attachment_download_is_scoped(db_roles):
    dept, approver, requester, cost_center = _org_setup(db_roles, "DEPT-FILE")
    pr = _submitted_pr(requester, dept, cost_center, title="With attachment")
    attachment = PRAttachment.objects.create(
        requisition=pr,
        title="Quote",
        file=SimpleUploadedFile("quote.pdf", b"%PDF-1.4 quote", content_type="application/pdf"),
    )
    _other_dept, other_approver, _req, _cc = _org_setup(db_roles, "DEPT-OTHER")
    client = Client()
    client.force_login(other_approver)
    denied = client.get(reverse("requisition_attachment_download", args=[attachment.id]))
    assert denied.status_code == 403
    client.force_login(approver)
    allowed = client.get(reverse("requisition_attachment_download", args=[attachment.id]))
    assert allowed.status_code == 200


@pytest.mark.django_db
def test_detail_page_records_server_status(db_roles):
    dept, approver, requester, cost_center = _org_setup(db_roles, "DEPT-PAGE")
    pr = _submitted_pr(requester, dept, cost_center, title="Detail page switch")
    client = Client()
    client.force_login(approver)
    page = client.get(reverse("requisition_detail", args=[pr.id]))
    assert page.status_code == 200
    html = page.content.decode()
    assert "Approve Requisition" in html
    assert "Within approval limit." in html
    posted = client.post(
        reverse("department_need_review", args=[pr.id]),
        {"decision": DECISION_CONFIRM_NEED, "comments": "Confirmed on the detail page."},
    )
    assert posted.status_code == 302
    followed = client.get(posted.url)
    assert "Budget Review" in followed.content.decode()
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW
    with pytest.raises(WorkflowConflict):
        department_need_review_service(
            requisition=pr,
            approver=approver,
            decision=DECISION_REJECT_NEED,
            comments="too late",
        )
