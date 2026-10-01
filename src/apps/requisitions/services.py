from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalAction, ApprovalDelegate, ApprovalPolicy, ApprovalStep
from apps.approvals.services import evaluate_approval_chain, record_approval_action_service
from apps.audit.models import AuditLog
from apps.budgets.services import (
    check_and_reserve_budget_service,
    release_pr_budget_reservation_service,
)
from apps.notifications.models import Notification
from apps.organization.models import CostCenter, Department

from .models import PRLine, PurchaseRequisition

DECISION_CONFIRM_NEED = "CONFIRM_NEED"
DECISION_REJECT_NEED = "REJECT_NEED"

REVIEWABLE_STATUSES = {
    PurchaseRequisition.STATUS_SUBMITTED,
    PurchaseRequisition.STATUS_MANAGER_REVIEW,
}

LIMIT_EXCEEDED_MESSAGE = "Approval limit exceeded. This requisition requires escalation."


class WorkflowConflict(Exception):
    """The requisition changed before this decision could be recorded."""

    def __init__(self, message):
        self.message = message
        super().__init__(message)


@transaction.atomic
def create_purchase_requisition_service(
    *,
    title: str,
    justification: str,
    requester: User,
    department: Department,
    cost_center: CostCenter,
    requested_delivery_date,
    line_items: list,
) -> PurchaseRequisition:
    """
    Creates a new PurchaseRequisition in DRAFT status with atomic document number generation.
    """
    if not line_items:
        raise ValidationError("Purchase Requisition must contain at least one line item.")

    pr_count = PurchaseRequisition.objects.count() + 1
    pr_number = f"PR-{timezone.now().strftime('%Y')}-{pr_count:05d}"

    pr = PurchaseRequisition.objects.create(
        pr_number=pr_number,
        title=title,
        justification=justification,
        requester=requester,
        department=department,
        cost_center=cost_center,
        status=PurchaseRequisition.STATUS_DRAFT,
        requested_delivery_date=requested_delivery_date,
        total_amount=Decimal("0.00"),
    )

    total = Decimal("0.00")
    for item in line_items:
        line = PRLine.objects.create(
            requisition=pr,
            item_description=item["item_description"],
            quantity=Decimal(str(item["quantity"])),
            unit_of_measure=item.get("unit_of_measure", "EA"),
            estimated_unit_price=Decimal(str(item["estimated_unit_price"])),
            specifications=item.get("specifications", ""),
        )
        total += line.estimated_total

    pr.total_amount = total
    pr.save(update_fields=["total_amount", "updated_at"])

    AuditLog.objects.create(
        actor=requester,
        action=AuditLog.ACTION_CREATE,
        target_model="PurchaseRequisition",
        target_object_id=str(pr.id),
        new_state={
            "pr_number": pr.pr_number,
            "total_amount": str(pr.total_amount),
            "status": pr.status,
        },
    )

    return pr


@transaction.atomic
def submit_purchase_requisition_service(
    *, requisition: PurchaseRequisition, user: User
) -> PurchaseRequisition:
    """
    Submits a PR for approval:
    1. Evaluates server-side approval chain.
    2. Runs pre-approval budget check & locks reservation.
    3. Transitions PR status to SUBMITTED / MANAGER_REVIEW.
    """
    if requisition.status != PurchaseRequisition.STATUS_DRAFT:
        raise ValidationError(
            f"Cannot submit requisition in status '{requisition.status}'. Must be DRAFT."
        )

    if requisition.total_amount <= Decimal("0.00"):
        raise ValidationError("Requisition total amount must be greater than zero.")

    # Execute budget reservation check
    check_and_reserve_budget_service(requisition=requisition, requested_by_user=user)

    # Evaluate approval policy
    chain = evaluate_approval_chain(
        module=ApprovalPolicy.MODULE_PR,
        amount=requisition.total_amount,
        department=requisition.department,
    )

    previous_status = requisition.status
    requisition.status = (
        PurchaseRequisition.STATUS_MANAGER_REVIEW if chain else PurchaseRequisition.STATUS_APPROVED
    )
    requisition.save(update_fields=["status", "updated_at"])

    record_approval_action_service(
        target_object_id=requisition.id,
        target_model_name="PurchaseRequisition",
        actor=user,
        action=ApprovalAction.ACTION_SUBMIT,
        previous_state=previous_status,
        new_state=requisition.status,
        comments="PR submitted for approval workflow.",
    )
    if requisition.status in REVIEWABLE_STATUSES:
        _notify_department_approvers(requisition)

    return requisition


@transaction.atomic
def approve_purchase_requisition_service(
    *, requisition: PurchaseRequisition, approver: User, comments: str = ""
) -> PurchaseRequisition:
    """
    Approves a PR and transitions state to APPROVED.
    """
    allowed_statuses = [
        PurchaseRequisition.STATUS_SUBMITTED,
        PurchaseRequisition.STATUS_MANAGER_REVIEW,
        PurchaseRequisition.STATUS_BUDGET_REVIEW,
    ]
    if requisition.status not in allowed_statuses:
        raise ValidationError(f"Cannot approve requisition in status '{requisition.status}'.")

    previous_status = requisition.status
    requisition.status = PurchaseRequisition.STATUS_APPROVED
    requisition.save(update_fields=["status", "updated_at"])

    record_approval_action_service(
        target_object_id=requisition.id,
        target_model_name="PurchaseRequisition",
        actor=approver,
        action=ApprovalAction.ACTION_APPROVE,
        previous_state=previous_status,
        new_state=requisition.status,
        comments=comments or "Requisition approved.",
    )

    return requisition


@transaction.atomic
def reject_purchase_requisition_service(
    *, requisition: PurchaseRequisition, approver: User, comments: str
) -> PurchaseRequisition:
    """
    Rejects a PR and transitions state to REJECTED.
    """
    if not comments:
        raise ValidationError("Rejection justification comments are mandatory.")

    previous_status = requisition.status
    requisition.status = PurchaseRequisition.STATUS_REJECTED
    requisition.save(update_fields=["status", "updated_at"])

    release_pr_budget_reservation_service(requisition=requisition, actor=approver)

    _notify(
        requisition.requester,
        f"{requisition.pr_number} was rejected",
        comments,
        f"/requisitions/{requisition.id}/",
    )

    record_approval_action_service(
        target_object_id=requisition.id,
        target_model_name="PurchaseRequisition",
        actor=approver,
        action=ApprovalAction.ACTION_REJECT,
        previous_state=previous_status,
        new_state=requisition.status,
        comments=comments,
    )

    return requisition


def _delegated_department_ids(user: User):
    today = timezone.now().date()
    approver_ids = ApprovalDelegate.objects.filter(
        delegate=user,
        is_active=True,
        start_date__lte=today,
        end_date__gte=today,
    ).values_list("approver_id", flat=True)
    return set(
        User.objects.filter(id__in=approver_ids, department_id__isnull=False).values_list(
            "department_id", flat=True
        )
    )


def next_workflow_label(requisition: PurchaseRequisition) -> str:
    """Server-side description of the next step. The UI must not invent this."""
    labels = {
        PurchaseRequisition.STATUS_DRAFT: "Submit requisition",
        PurchaseRequisition.STATUS_SUBMITTED: "Department approver reviews need",
        PurchaseRequisition.STATUS_MANAGER_REVIEW: "Department approver reviews need",
        PurchaseRequisition.STATUS_BUDGET_REVIEW: "Budget review",
        PurchaseRequisition.STATUS_APPROVED: "Procurement decides sourcing or direct purchase order",
        PurchaseRequisition.STATUS_REJECTED: "Request closed",
        PurchaseRequisition.STATUS_SOURCING: "Sourcing — invite eligible vendors",
        PurchaseRequisition.STATUS_PO_ISSUED: "Purchase order issued",
    }
    return labels.get(requisition.status, requisition.status)


def _notify(recipient, title, message, target_url):
    if recipient is None:
        return
    Notification.objects.create(
        recipient=recipient,
        notification_type=Notification.TYPE_APPROVAL_REQUIRED,
        title=title,
        message=message,
        target_url=target_url,
    )


def _notify_department_approvers(requisition: PurchaseRequisition):
    approvers = User.objects.filter(
        role__code=Role.DEPT_APPROVER,
        department_id=requisition.department_id,
        is_active=True,
    )
    url = f"/requisitions/{requisition.id}/"
    for approver in approvers:
        _notify(
            approver,
            f"{requisition.pr_number} requires your approval",
            f"{requisition.title} is waiting for a department need decision.",
            url,
        )


def _policies_for_amount(requisition: PurchaseRequisition):
    amount = requisition.total_amount
    return (
        ApprovalPolicy.objects.filter(
            module=ApprovalPolicy.MODULE_PR,
            is_active=True,
            min_amount__lte=amount,
        )
        .filter(Q(max_amount__isnull=True) | Q(max_amount__gte=amount))
        .filter(Q(department=requisition.department) | Q(department__isnull=True))
        .prefetch_related("steps__approver_role", "steps__specific_approver")
    )


def _user_covers_policy_step(user: User, step: ApprovalStep) -> bool:
    if step.approver_role.code != Role.DEPT_APPROVER:
        return False
    if step.specific_approver_id is None:
        return True
    if step.specific_approver_id == user.id:
        return True
    today = timezone.now().date()
    return ApprovalDelegate.objects.filter(
        approver_id=step.specific_approver_id,
        delegate=user,
        is_active=True,
        start_date__lte=today,
        end_date__gte=today,
    ).exists()


def approval_authority(user: User, requisition: PurchaseRequisition) -> dict:
    """
    Whether this user may confirm the department need for this amount.
    Limit comes from active PR approval policies, not from the browser.
    """
    if user.role_code == Role.SUPER_ADMIN:
        return {
            "within_limit": True,
            "limit_amount": None,
            "unlimited": True,
            "message": "Super admin authority.",
        }

    covering = []
    for policy in _policies_for_amount(requisition):
        for step in policy.steps.all():
            if _user_covers_policy_step(user, step):
                covering.append(policy)
                break

    if not covering:
        capped = (
            ApprovalPolicy.objects.filter(
                module=ApprovalPolicy.MODULE_PR,
                is_active=True,
                steps__approver_role__code=Role.DEPT_APPROVER,
            )
            .filter(Q(department=requisition.department) | Q(department__isnull=True))
            .exclude(max_amount__isnull=True)
        )
        highest = capped.order_by("-max_amount").values_list("max_amount", flat=True).first()
        return {
            "within_limit": False,
            "limit_amount": highest,
            "unlimited": False,
            "message": LIMIT_EXCEEDED_MESSAGE,
        }

    unlimited = any(policy.max_amount is None for policy in covering)
    numeric_limits = [policy.max_amount for policy in covering if policy.max_amount is not None]
    limit_amount = None if unlimited else (max(numeric_limits) if numeric_limits else None)
    return {
        "within_limit": True,
        "limit_amount": limit_amount,
        "unlimited": unlimited,
        "message": "Within approval limit.",
    }


def user_can_review_department_need(user: User, requisition: PurchaseRequisition) -> bool:
    """
    Department need review is limited to the department approver for that department,
    an active delegate of that approver, or a super admin.
    """
    if not user or not user.is_authenticated:
        return False
    if user.role_code == Role.SUPER_ADMIN:
        return True
    if user.role_code != Role.DEPT_APPROVER:
        return False
    if user.department_id and user.department_id == requisition.department_id:
        return True
    return requisition.department_id in _delegated_department_ids(user)


def _lock_reviewable(requisition: PurchaseRequisition) -> PurchaseRequisition:
    locked = (
        PurchaseRequisition.objects.select_for_update()
        .select_related("requester", "department", "cost_center")
        .get(pk=requisition.pk)
    )
    if locked.status not in REVIEWABLE_STATUSES:
        raise WorkflowConflict(
            "This requisition has already been processed. Refreshing the latest status."
        )
    return locked


@transaction.atomic
def department_need_review_service(
    *,
    requisition: PurchaseRequisition,
    approver: User,
    decision: str,
    comments: str = "",
) -> PurchaseRequisition:
    """
    Records the flowchart decision "Department approver reviews need?".

    The next status is chosen here, not by the caller.
    CONFIRM_NEED moves a reviewable requisition to BUDGET_REVIEW when the
    approver's policy covers the amount. REJECT_NEED documents the reason,
    sets REJECTED, and releases the reservation.
    """
    if not user_can_review_department_need(approver, requisition):
        raise PermissionDenied("You are not allowed to review this department requisition.")

    locked = _lock_reviewable(requisition)
    comments = (comments or "").strip()

    if decision == DECISION_REJECT_NEED:
        if not comments:
            raise ValidationError("Document the approval request: a rejection reason is required.")
        return reject_purchase_requisition_service(
            requisition=locked,
            approver=approver,
            comments=comments,
        )

    if decision != DECISION_CONFIRM_NEED:
        raise ValidationError("Decision must be CONFIRM_NEED or REJECT_NEED.")

    authority = approval_authority(approver, locked)
    if not authority["within_limit"]:
        raise ValidationError(authority["message"])

    previous_status = locked.status
    locked.status = PurchaseRequisition.STATUS_BUDGET_REVIEW
    locked.save(update_fields=["status", "updated_at"])

    record_approval_action_service(
        target_object_id=locked.id,
        target_model_name="PurchaseRequisition",
        actor=approver,
        action=ApprovalAction.ACTION_APPROVE,
        previous_state=previous_status,
        new_state=locked.status,
        comments=comments
        or "Department approver confirmed the business need. Forwarded to budget review.",
    )
    for finance_user in User.objects.filter(role__code=Role.FINANCE_AP, is_active=True):
        _notify(
            finance_user,
            f"{locked.pr_number} is ready for budget review",
            f"{locked.title} was confirmed by the department approver.",
            f"/requisitions/{locked.id}/",
        )
    return locked


@transaction.atomic
def request_pr_clarification_service(
    *,
    requisition: PurchaseRequisition,
    approver: User,
    question: str,
) -> PurchaseRequisition:
    """
    Asks the requester for clarification without changing the requisition state.
    """
    if not user_can_review_department_need(approver, requisition):
        raise PermissionDenied("You are not allowed to review this department requisition.")

    question = (question or "").strip()
    if not question:
        raise ValidationError("A clarification question is required.")

    locked = _lock_reviewable(requisition)
    record_approval_action_service(
        target_object_id=locked.id,
        target_model_name="PurchaseRequisition",
        actor=approver,
        action=ApprovalAction.ACTION_CLARIFY,
        previous_state=locked.status,
        new_state=locked.status,
        comments=question,
    )
    _notify(
        locked.requester,
        f"Clarification requested on {locked.pr_number}",
        question,
        f"/requisitions/{locked.id}/",
    )
    return locked
