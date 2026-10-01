from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.core.middleware import get_client_ip, get_current_request_id, get_user_agent

from .models import ApprovalAction, ApprovalDelegate, ApprovalPolicy, ApprovalStep


@transaction.atomic
def create_approval_policy_service(
    *,
    name: str,
    module: str,
    min_amount: Decimal = Decimal("0.00"),
    max_amount=None,
    department=None,
) -> ApprovalPolicy:
    """
    Creates an Approval Policy configuration.
    """
    policy = ApprovalPolicy.objects.create(
        name=name,
        module=module,
        min_amount=min_amount,
        max_amount=max_amount,
        department=department,
    )
    return policy


@transaction.atomic
def add_approval_step_service(
    *,
    policy: ApprovalPolicy,
    step_number: int,
    approver_role: Role,
    specific_approver: User = None,
    description: str = "",
) -> ApprovalStep:
    """
    Adds an ordered step to an Approval Policy.
    """
    step = ApprovalStep.objects.create(
        policy=policy,
        step_number=step_number,
        approver_role=approver_role,
        specific_approver=specific_approver,
        description=description,
    )
    return step


def resolve_active_delegate(approver: User) -> User:
    """
    Resolves if an approver has an active delegate for today's date.
    If active delegate exists, returns the delegate user; otherwise returns the original approver.
    """
    today = timezone.now().date()
    delegate_record = ApprovalDelegate.objects.filter(
        approver=approver,
        is_active=True,
        start_date__lte=today,
        end_date__gte=today,
    ).first()

    if delegate_record and delegate_record.delegate:
        return delegate_record.delegate
    return approver


def evaluate_approval_chain(*, module: str, amount: Decimal, department=None):
    """
    Evaluates server-side approval routing policies for a module & amount threshold.
    Returns list of tuples: [(step, target_approver_role, effective_approver_user)]
    """
    policies = ApprovalPolicy.objects.filter(
        module=module,
        is_active=True,
        min_amount__lte=amount,
    ).order_by("-min_amount")

    if department:
        # Filter policy matching department or fallback to global policy
        dept_policy = policies.filter(department=department).first()
        selected_policy = dept_policy or policies.filter(department__isnull=True).first()
    else:
        selected_policy = policies.filter(department__isnull=True).first()

    if not selected_policy:
        return []

    chain = []
    steps = selected_policy.steps.select_related("approver_role", "specific_approver").order_by(
        "step_number"
    )
    for step in steps:
        effective_user = None
        if step.specific_approver:
            effective_user = resolve_active_delegate(step.specific_approver)
        chain.append(
            {
                "step_number": step.step_number,
                "policy_step": step,
                "approver_role": step.approver_role,
                "specific_approver": step.specific_approver,
                "effective_approver": effective_user,
                "description": step.description,
            }
        )
    return chain


@transaction.atomic
def record_approval_action_service(
    *,
    target_object_id,
    target_model_name: str,
    actor: User,
    action: str,
    previous_state: str,
    new_state: str,
    policy_step: ApprovalStep = None,
    comments: str = "",
) -> ApprovalAction:
    """
    Records an append-only ApprovalAction and corresponding AuditLog entry inside a transaction.
    """
    approval_action = ApprovalAction.objects.create(
        policy_step=policy_step,
        target_object_id=target_object_id,
        target_model_name=target_model_name,
        actor=actor,
        action=action,
        comments=comments,
        previous_state=previous_state,
        new_state=new_state,
    )

    if action == ApprovalAction.ACTION_APPROVE:
        audit_action = AuditLog.ACTION_APPROVE
    elif action == ApprovalAction.ACTION_REJECT:
        audit_action = AuditLog.ACTION_REJECT
    else:
        audit_action = AuditLog.ACTION_UPDATE

    AuditLog.objects.create(
        actor=actor,
        action=audit_action,
        target_model=target_model_name,
        target_object_id=str(target_object_id),
        previous_state={"status": previous_state},
        new_state={"status": new_state, "comments": comments},
        ip_address=get_client_ip(),
        request_id=get_current_request_id() or "",
        user_agent=get_user_agent() or "",
    )

    return approval_action


@transaction.atomic
def set_approval_delegate_service(
    *, approver: User, delegate: User, start_date, end_date, reason: str = ""
) -> ApprovalDelegate:
    """
    Sets up a temporary delegate for an approver.
    """
    delegate_record = ApprovalDelegate.objects.create(
        approver=approver,
        delegate=delegate,
        start_date=start_date,
        end_date=end_date,
        is_active=True,
        reason=reason,
    )
    return delegate_record
