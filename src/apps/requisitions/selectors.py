from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import Role
from apps.approvals.models import ApprovalAction
from apps.budgets.models import Budget, BudgetReservation
from apps.requisitions.services import (
    REVIEWABLE_STATUSES,
    _delegated_department_ids,
    approval_authority,
    next_workflow_label,
    user_can_review_department_need,
)

from .models import PurchaseRequisition

CURRENCY_CODE = "USD"
QUEUE_PAGE_SIZE = 10

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


def _scope_queryset(user):
    base = PurchaseRequisition.objects.select_related(
        "requester", "department", "cost_center"
    ).prefetch_related("lines", "attachments", "budget_reservations")
    dept_ids = visible_department_ids(user)
    if dept_ids is None:
        return base
    if not dept_ids:
        return base.none()
    return base.filter(department_id__in=dept_ids)


def schedule_label(pr, today=None):
    today = today or timezone.now().date()
    if pr.requested_delivery_date < today:
        return "Overdue"
    if pr.requested_delivery_date <= today + timedelta(days=7):
        return "Due soon"
    return "Scheduled"


def aging_label(pr, today=None):
    today = today or timezone.now().date()
    days = (today - timezone.localtime(pr.created_at).date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "1 day"
    return f"{days} days"


def budget_snapshot(pr):
    reservation = next(
        (
            item
            for item in pr.budget_reservations.all()
            if item.status == BudgetReservation.STATUS_RESERVED
        ),
        None,
    )
    released = any(
        item.status == BudgetReservation.STATUS_RELEASED for item in pr.budget_reservations.all()
    )
    budget = None
    if reservation:
        budget = reservation.budget
    else:
        budget = (
            Budget.objects.select_related("cost_center", "fiscal_period")
            .filter(cost_center_id=pr.cost_center_id)
            .order_by("-created_at")
            .first()
        )
    if budget is None:
        state = "Budget Pending"
    elif budget.allow_overspend and pr.total_amount > budget.available_amount and not reservation:
        state = "Budget Exception"
    elif reservation:
        state = "Budget Available"
    elif pr.total_amount > budget.available_amount:
        state = "Budget Insufficient"
    else:
        state = "Budget Available"
    if pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW and reservation:
        state = "Budget Available"
    return {
        "state": state,
        "reserved_amount": reservation.amount if reservation else None,
        "released": released and reservation is None,
        "allocated": budget.allocated_amount if budget else None,
        "available": budget.available_amount if budget else None,
        "reserved": budget.reserved_amount if budget else None,
        "committed": budget.committed_amount if budget else None,
        "actual": budget.actual_amount if budget else None,
        "requested": pr.total_amount,
        "remaining": (
            (budget.available_amount if reservation else budget.available_amount - pr.total_amount)
            if budget
            else None
        ),
    }


def _search_filter(queryset, query):
    if not query:
        return queryset
    return queryset.filter(
        Q(pr_number__icontains=query)
        | Q(title__icontains=query)
        | Q(justification__icontains=query)
        | Q(requester__email__icontains=query)
        | Q(requester__first_name__icontains=query)
        | Q(requester__last_name__icontains=query)
        | Q(department__name__icontains=query)
        | Q(department__code__icontains=query)
        | Q(cost_center__code__icontains=query)
        | Q(cost_center__name__icontains=query)
    )


def _range_filters(queryset, params):
    if params.get("status"):
        queryset = queryset.filter(status=params["status"])
    if params.get("department"):
        queryset = queryset.filter(department_id=params["department"])
    if params.get("cost_center"):
        queryset = queryset.filter(cost_center_id=params["cost_center"])
    if params.get("amount_min") not in (None, ""):
        queryset = queryset.filter(total_amount__gte=params["amount_min"])
    if params.get("amount_max") not in (None, ""):
        queryset = queryset.filter(total_amount__lte=params["amount_max"])
    if params.get("date_from"):
        queryset = queryset.filter(created_at__date__gte=params["date_from"])
    if params.get("date_to"):
        queryset = queryset.filter(created_at__date__lte=params["date_to"])
    return queryset


def _priority_filter(queryset, params, today):
    if str(params.get("overdue") or "") in {"1", "true", "on"}:
        queryset = queryset.filter(
            requested_delivery_date__lt=today,
            status__in=REVIEWABLE_STATUSES,
        )
    priority = params.get("priority") or ""
    if priority == "overdue":
        return queryset.filter(requested_delivery_date__lt=today)
    if priority == "due_soon":
        return queryset.filter(
            requested_delivery_date__gte=today,
            requested_delivery_date__lte=today + timedelta(days=7),
        )
    if priority == "scheduled":
        return queryset.filter(requested_delivery_date__gt=today + timedelta(days=7))
    return queryset


def _queue_ordering(params):
    ordering = params.get("ordering") or "-created_at"
    allowed = {
        "created_at",
        "-created_at",
        "total_amount",
        "-total_amount",
        "requested_delivery_date",
        "-requested_delivery_date",
        "pr_number",
        "-pr_number",
        "status",
        "-status",
    }
    return ordering if ordering in allowed else "-created_at"


def apply_queue_filters(queryset, params):
    params = params or {}
    today = timezone.now().date()
    queryset = _search_filter(queryset, (params.get("q") or "").strip())
    queryset = _range_filters(queryset, params)
    queryset = _priority_filter(queryset, params, today)
    return queryset.order_by(_queue_ordering(params))


def _department_kpis(user, scoped):
    today = timezone.now().date()
    pending = scoped.filter(status__in=REVIEWABLE_STATUSES)
    pending_value = pending.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
    overdue = pending.filter(requested_delivery_date__lt=today).count()
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    approved_today = ApprovalAction.objects.filter(
        actor=user,
        action=ApprovalAction.ACTION_APPROVE,
        target_model_name="PurchaseRequisition",
        created_at__gte=start,
    ).count()
    rejected_today = ApprovalAction.objects.filter(
        actor=user,
        action=ApprovalAction.ACTION_REJECT,
        target_model_name="PurchaseRequisition",
        created_at__gte=start,
    ).count()
    approve_actions = list(
        ApprovalAction.objects.filter(
            actor=user,
            action=ApprovalAction.ACTION_APPROVE,
            target_model_name="PurchaseRequisition",
        )
    )
    submit_times = {
        row["target_object_id"]: row["created_at"]
        for row in ApprovalAction.objects.filter(
            action=ApprovalAction.ACTION_SUBMIT,
            target_model_name="PurchaseRequisition",
            target_object_id__in=[item.target_object_id for item in approve_actions],
        ).values("target_object_id", "created_at")
    }
    durations = []
    for action in approve_actions:
        started = submit_times.get(action.target_object_id)
        if started and action.created_at >= started:
            durations.append((action.created_at - started).total_seconds() / 3600)
    average_hours = round(sum(durations) / len(durations), 2) if durations else None
    return {
        "pending_approval": pending.count(),
        "approved_today": approved_today,
        "rejected_today": rejected_today,
        "total_pending_value": pending_value,
        "overdue_approvals": overdue,
        "average_approval_hours": average_hours,
    }


def queue_row(pr, rejection_action=None):
    budget = budget_snapshot(pr)
    workflow = workflow_for_requisition(pr, rejection_action)
    requester = pr.requester
    name = f"{requester.first_name} {requester.last_name}".strip() or requester.email
    return {
        "pr": pr,
        "id": str(pr.id),
        "pr_number": pr.pr_number,
        "requester": name,
        "requester_email": requester.email,
        "department": pr.department.name,
        "cost_center": pr.cost_center.code,
        "request_date": pr.created_at,
        "required_by": pr.requested_delivery_date,
        "description": pr.title,
        "total_amount": pr.total_amount,
        "currency": CURRENCY_CODE,
        "budget_state": budget["state"],
        "status": pr.status,
        "status_label": pr.get_status_display(),
        "priority": schedule_label(pr),
        "aging": aging_label(pr),
        "next_step": next_workflow_label(pr),
        "workflow": workflow,
        "budget_reserved": budget["reserved_amount"],
        "budget_released": budget["released"],
        "can_review": workflow["can_review"],
    }


def build_requisition_detail(user, pr):
    actions = list(
        ApprovalAction.objects.filter(
            target_model_name="PurchaseRequisition",
            target_object_id=pr.id,
        )
        .select_related("actor", "actor__role")
        .order_by("created_at")
    )
    rejection = next(
        (item for item in reversed(actions) if item.action == ApprovalAction.ACTION_REJECT),
        None,
    )
    authority = approval_authority(user, pr)
    lines = [
        {
            "description": line.item_description,
            "quantity": line.quantity,
            "unit": line.unit_of_measure,
            "unit_price": line.estimated_unit_price,
            "total": line.estimated_total,
        }
        for line in pr.lines.all()
    ]
    history = [
        {
            "actor": action.actor.email if action.actor else "",
            "role": action.actor.role_code if action.actor else "",
            "action": action.action,
            "timestamp": action.created_at,
            "comment": action.comments,
            "previous_state": action.previous_state,
            "new_state": action.new_state,
        }
        for action in actions
    ]
    attachments = [
        {
            "id": str(item.id),
            "title": item.title,
            "name": item.file.name.rsplit("/", 1)[-1],
        }
        for item in pr.attachments.all()
    ]
    return {
        "row": queue_row(pr, rejection),
        "justification": pr.justification,
        "lines": lines,
        "grand_total": pr.total_amount,
        "currency": CURRENCY_CODE,
        "budget": budget_snapshot(pr),
        "attachments": attachments,
        "history": history,
        "authority": authority,
        "next_step": next_workflow_label(pr),
        "can_decide": pr.status in REVIEWABLE_STATUSES and user_can_decide(user, pr),
    }


def user_can_decide(user, pr):
    return user_can_review_department_need(user, pr)


def build_department_dashboard_context(user, params=None):
    """
    Live department approval dashboard: scoped requisitions, budget position, and decisions.
    """
    params = params or {}
    scoped = _scope_queryset(user)
    filtered = apply_queue_filters(scoped, params)
    paginator = Paginator(filtered, QUEUE_PAGE_SIZE)
    page = paginator.get_page(params.get("page") or 1)
    pr_list = list(page.object_list)
    pr_ids = [pr.id for pr in pr_list]
    latest_reject = {}
    recent_actions = []
    for action in (
        ApprovalAction.objects.filter(
            target_model_name="PurchaseRequisition",
            target_object_id__in=pr_ids,
        )
        .select_related("actor")
        .order_by("-created_at")
    ):
        recent_actions.append(action)
        if (
            action.action == ApprovalAction.ACTION_REJECT
            and action.target_object_id not in latest_reject
        ):
            latest_reject[action.target_object_id] = action

    rows = [queue_row(pr, latest_reject.get(pr.id)) for pr in pr_list]
    counts = {key: 0 for key, _label in PurchaseRequisition.STATUS_CHOICES}
    for status, _label in PurchaseRequisition.STATUS_CHOICES:
        counts[status] = scoped.filter(status=status).count()

    dept_ids = visible_department_ids(user)
    budgets = Budget.objects.select_related("cost_center", "fiscal_period")
    if dept_ids is not None:
        budgets = budgets.filter(cost_center__department_id__in=dept_ids or [])
    budget_rows = [
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
        for budget in budgets.order_by("cost_center__code")
    ]
    metrics = _department_kpis(user, scoped)
    return {
        "department_name": (
            user.department.name if getattr(user, "department_id", None) else "All departments"
        ),
        "pending_reviews": [row for row in rows if row["can_review"]],
        "requisition_rows": rows,
        "page_obj": page,
        "filters": params,
        "page_query": urlencode(
            {
                key: value
                for key, value in params.items()
                if value not in (None, "") and key != "page"
            }
        ),
        "recent_actions": recent_actions[:12],
        "budget_rows": budget_rows,
        "status_counts": counts,
        "departments": scoped.values(
            "department_id", "department__name", "department__code"
        ).distinct(),
        "cost_centers": scoped.values("cost_center_id", "cost_center__code").distinct(),
        "kpi": {
            "pending_approval": metrics["pending_approval"],
            "approved_today": metrics["approved_today"],
            "rejected_today": metrics["rejected_today"],
            "total_pending_value": metrics["total_pending_value"],
            "overdue_approvals": metrics["overdue_approvals"],
            "average_approval_hours": metrics["average_approval_hours"],
            "awaiting_need_review": metrics["pending_approval"],
            "budget_review": counts.get(PurchaseRequisition.STATUS_BUDGET_REVIEW, 0),
            "approved": counts.get(PurchaseRequisition.STATUS_APPROVED, 0),
            "rejected": counts.get(PurchaseRequisition.STATUS_REJECTED, 0),
            "sourcing": counts.get(PurchaseRequisition.STATUS_SOURCING, 0),
            "po_issued": counts.get(PurchaseRequisition.STATUS_PO_ISSUED, 0),
        },
    }
