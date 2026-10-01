from apps.accounts.models import Role
from apps.approvals.models import ApprovalAction
from apps.budgets.models import Budget, BudgetReservation
from apps.requisitions.services import _delegated_department_ids

from .models import PurchaseRequisition

GLOBAL_READ_ROLES = {
    Role.SUPER_ADMIN,
    Role.PROC_EXEC,
    Role.PROC_MGR,
    Role.FINANCE_AP,
    Role.AUDITOR,
    Role.LEGAL_MGR,
}

WORKFLOW_STEPS = (
    ("requisition", "Purchase requisition"),
    ("need", "Department approver reviews need?"),
    ("budget", "Budget review"),
    ("decision", "Approved requisition"),
    ("route", "Sourcing required by policy?"),
)


def get_all_requisitions():
    return (
        PurchaseRequisition.objects.select_related("requester", "department", "cost_center")
        .prefetch_related("lines", "attachments")
        .order_by("-created_at")
    )


def get_requisition_by_id(pr_id):
    return (
        PurchaseRequisition.objects.select_related("requester", "department", "cost_center")
        .prefetch_related("lines", "attachments", "budget_reservations")
        .filter(id=pr_id)
        .first()
    )


def get_requisitions_by_department(department_id):
    return (
        PurchaseRequisition.objects.select_related("requester", "cost_center")
        .filter(department_id=department_id)
        .order_by("-created_at")
    )


def visible_department_ids(user):
    """Departments whose requisitions this user may review or read on the department dashboard."""
    if not user.is_authenticated:
        return []
    if user.role_code == Role.SUPER_ADMIN:
        return None
    ids = set()
    if user.role_code == Role.DEPT_APPROVER and user.department_id:
        ids.add(user.department_id)
    ids.update(_delegated_department_ids(user))
    return list(ids)


def requisitions_visible_to(user):
    qs = get_all_requisitions()
    if not user.is_authenticated:
        return qs.none()
    if user.role_code in GLOBAL_READ_ROLES:
        return qs
    if user.role_code == Role.DEPT_APPROVER:
        dept_ids = visible_department_ids(user)
        if not dept_ids:
            return qs.none()
        return qs.filter(department_id__in=dept_ids)
    if user.department_id:
        return qs.filter(department_id=user.department_id)
    return qs.filter(requester=user)


def _status_index(status, rejection_previous=None):
    if status == PurchaseRequisition.STATUS_REJECTED:
        if rejection_previous == PurchaseRequisition.STATUS_BUDGET_REVIEW:
            return 2
        return 1
    return {
        PurchaseRequisition.STATUS_DRAFT: 0,
        PurchaseRequisition.STATUS_SUBMITTED: 1,
        PurchaseRequisition.STATUS_MANAGER_REVIEW: 1,
        PurchaseRequisition.STATUS_BUDGET_REVIEW: 2,
        PurchaseRequisition.STATUS_APPROVED: 3,
        PurchaseRequisition.STATUS_SOURCING: 4,
        PurchaseRequisition.STATUS_PO_ISSUED: 4,
    }.get(status, 0)


ROUTE_NOTES = {
    PurchaseRequisition.STATUS_SOURCING: "Sourcing required — procurement invites eligible vendors.",
    PurchaseRequisition.STATUS_PO_ISSUED: "Direct PO permitted — purchase order has been issued.",
    PurchaseRequisition.STATUS_APPROVED: (
        "Approved. Procurement decides sourcing versus a direct purchase order."
    ),
    PurchaseRequisition.STATUS_BUDGET_REVIEW: "Need confirmed. Waiting on budget review.",
    PurchaseRequisition.STATUS_SUBMITTED: (
        "Waiting on the department approver to confirm the business need."
    ),
    PurchaseRequisition.STATUS_MANAGER_REVIEW: (
        "Waiting on the department approver to confirm the business need."
    ),
    PurchaseRequisition.STATUS_REJECTED: (
        "Request rejected. The decision is documented on the approval record."
    ),
}


def _step_state(index, current_index, rejected):
    if rejected and index == current_index:
        return "rejected"
    if rejected and index > current_index:
        return "skipped"
    if index < current_index:
        return "done"
    if index == current_index:
        return "current"
    return "upcoming"


def workflow_for_requisition(pr, rejection_action=None):
    """
    Maps a requisition onto the department approval flowchart.
    """
    previous = rejection_action.previous_state if rejection_action else None
    current_index = _status_index(pr.status, previous)
    rejected = pr.status == PurchaseRequisition.STATUS_REJECTED
    steps = [
        {
            "key": key,
            "label": label,
            "state": _step_state(index, current_index, rejected),
        }
        for index, (key, label) in enumerate(WORKFLOW_STEPS)
    ]
    return {
        "steps": steps,
        "route_note": ROUTE_NOTES.get(pr.status, ""),
        "rejection_comments": rejection_action.comments if rejection_action else "",
        "can_review": pr.status
        in (
            PurchaseRequisition.STATUS_SUBMITTED,
            PurchaseRequisition.STATUS_MANAGER_REVIEW,
        ),
    }


def build_department_dashboard_context(user):
    """
    Live department approval dashboard: scoped requisitions, budget position, and decisions.
    """
    dept_ids = visible_department_ids(user)
    base = PurchaseRequisition.objects.select_related(
        "requester", "department", "cost_center"
    ).prefetch_related("lines", "budget_reservations")
    if dept_ids is None:
        requisitions = base.order_by("-created_at")
        budgets = Budget.objects.select_related("cost_center", "fiscal_period").order_by(
            "cost_center__code"
        )
    elif dept_ids:
        requisitions = base.filter(department_id__in=dept_ids).order_by("-created_at")
        budgets = (
            Budget.objects.select_related("cost_center", "fiscal_period")
            .filter(cost_center__department_id__in=dept_ids)
            .order_by("cost_center__code")
        )
    else:
        requisitions = base.none()
        budgets = Budget.objects.none()

    pr_list = list(requisitions[:100])
    pr_ids = [pr.id for pr in pr_list]
    actions = ApprovalAction.objects.filter(
        target_model_name="PurchaseRequisition",
        target_object_id__in=pr_ids,
    ).select_related("actor")
    latest_reject = {}
    recent_actions = []
    for action in actions.order_by("-created_at"):
        recent_actions.append(action)
        if (
            action.action == ApprovalAction.ACTION_REJECT
            and action.target_object_id not in latest_reject
        ):
            latest_reject[action.target_object_id] = action
    recent_actions = recent_actions[:12]

    rows = []
    counts = {key: 0 for key, _label in PurchaseRequisition.STATUS_CHOICES}
    for pr in pr_list:
        counts[pr.status] = counts.get(pr.status, 0) + 1
        reservation = next(
            (
                item
                for item in pr.budget_reservations.all()
                if item.status == BudgetReservation.STATUS_RESERVED
            ),
            None,
        )
        released = next(
            (
                item
                for item in pr.budget_reservations.all()
                if item.status == BudgetReservation.STATUS_RELEASED
            ),
            None,
        )
        workflow = workflow_for_requisition(pr, latest_reject.get(pr.id))
        rows.append(
            {
                "pr": pr,
                "workflow": workflow,
                "budget_reserved": reservation.amount if reservation else None,
                "budget_released": bool(released) and reservation is None,
            }
        )

    pending = [row for row in rows if row["workflow"]["can_review"]]
    budget_rows = []
    for budget in budgets:
        budget_rows.append(
            {
                "code": budget.cost_center.code,
                "name": budget.cost_center.name,
                "period": budget.fiscal_period.name,
                "allocated": budget.allocated_amount,
                "reserved": budget.reserved_amount,
                "committed": budget.committed_amount,
                "actual": budget.actual_amount,
                "available": budget.available_amount,
            }
        )

    return {
        "department_name": (
            user.department.name if getattr(user, "department_id", None) else "All departments"
        ),
        "pending_reviews": pending,
        "requisition_rows": rows,
        "recent_actions": recent_actions,
        "budget_rows": budget_rows,
        "status_counts": counts,
        "kpi": {
            "awaiting_need_review": counts.get(PurchaseRequisition.STATUS_SUBMITTED, 0)
            + counts.get(PurchaseRequisition.STATUS_MANAGER_REVIEW, 0),
            "budget_review": counts.get(PurchaseRequisition.STATUS_BUDGET_REVIEW, 0),
            "approved": counts.get(PurchaseRequisition.STATUS_APPROVED, 0),
            "rejected": counts.get(PurchaseRequisition.STATUS_REJECTED, 0),
            "sourcing": counts.get(PurchaseRequisition.STATUS_SOURCING, 0),
            "po_issued": counts.get(PurchaseRequisition.STATUS_PO_ISSUED, 0),
        },
    }
