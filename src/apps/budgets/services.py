from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditLog
from apps.organization.models import CostCenter, FiscalPeriod

from .models import Budget, BudgetReservation, SpendLedger


@transaction.atomic
def allocate_budget_service(
    *,
    cost_center: CostCenter,
    fiscal_period: FiscalPeriod,
    allocated_amount: Decimal,
    allow_overspend: bool = False,
) -> Budget:
    """
    Allocates budget to a CostCenter for a FiscalPeriod inside an atomic transaction.
    """
    budget, created = Budget.objects.get_or_create(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        defaults={"allocated_amount": allocated_amount, "allow_overspend": allow_overspend},
    )
    if not created:
        budget.allocated_amount = allocated_amount
        budget.allow_overspend = allow_overspend
        budget.save(update_fields=["allocated_amount", "allow_overspend", "updated_at"])
    return budget


@transaction.atomic
def check_and_reserve_budget_service(*, requisition, requested_by_user) -> BudgetReservation:
    """
    Checks available budget for a PurchaseRequisition's cost center.
    If available, locks a BudgetReservation in RESERVED status and writes a SpendLedger entry.
    """
    cost_center = requisition.cost_center
    today = (
        requisition.created_at.date()
        if requisition.created_at
        else requisition.requested_delivery_date
    )

    budget = (
        Budget.objects.select_for_update()
        .filter(
            cost_center=cost_center,
            fiscal_period__start_date__lte=today,
            fiscal_period__end_date__gte=today,
            fiscal_period__is_closed=False,
        )
        .first()
    )

    if not budget:
        raise ValidationError(
            f"No active fiscal period budget allocation found for Cost Center '{cost_center.code}'."
        )

    amount = requisition.total_amount
    if amount > budget.available_amount and not budget.allow_overspend:
        raise ValidationError(
            f"Insufficient budget in Cost Center '{cost_center.code}'. "
            f"Requested: ${amount:,.2f}, Available: ${budget.available_amount:,.2f}."
        )

    # Update reserved amount on budget
    budget.reserved_amount += amount
    budget.save(update_fields=["reserved_amount", "updated_at"])

    # Create BudgetReservation
    reservation = BudgetReservation.objects.create(
        budget=budget,
        requisition=requisition,
        amount=amount,
        status=BudgetReservation.STATUS_RESERVED,
    )

    # Record SpendLedger
    SpendLedger.objects.create(
        budget=budget,
        entry_type=SpendLedger.ENTRY_RESERVATION,
        amount=amount,
        reference_number=requisition.pr_number,
        description=f"PR Budget Reservation: {requisition.title}",
    )

    AuditLog.objects.create(
        actor=requested_by_user,
        action=AuditLog.ACTION_CREATE,
        target_model="BudgetReservation",
        target_object_id=str(reservation.id),
        new_state={
            "amount": str(amount),
            "pr_number": requisition.pr_number,
            "cost_center": cost_center.code,
        },
    )

    return reservation


@transaction.atomic
def release_pr_budget_reservation_service(*, requisition, actor) -> int:
    """
    Releases active PR budget reservations when a requisition is rejected.
    Prior reservation and ledger rows are kept; a reversing ledger entry records the release.
    """
    reservations = BudgetReservation.objects.select_for_update().filter(
        requisition=requisition,
        status=BudgetReservation.STATUS_RESERVED,
    )
    released = 0
    for reservation in reservations:
        budget = Budget.objects.select_for_update().get(pk=reservation.budget_id)
        budget.reserved_amount -= reservation.amount
        if budget.reserved_amount < Decimal("0.00"):
            budget.reserved_amount = Decimal("0.00")
        budget.save(update_fields=["reserved_amount", "updated_at"])

        reservation.status = BudgetReservation.STATUS_RELEASED
        reservation.save(update_fields=["status", "updated_at"])

        SpendLedger.objects.create(
            budget=budget,
            entry_type=SpendLedger.ENTRY_RESERVATION,
            amount=-reservation.amount,
            reference_number=requisition.pr_number,
            description=f"PR reservation released on rejection: {requisition.title}",
        )
        AuditLog.objects.create(
            actor=actor,
            action=AuditLog.ACTION_UPDATE,
            target_model="BudgetReservation",
            target_object_id=str(reservation.id),
            previous_state={"status": BudgetReservation.STATUS_RESERVED},
            new_state={
                "status": BudgetReservation.STATUS_RELEASED,
                "amount": str(reservation.amount),
                "pr_number": requisition.pr_number,
            },
        )
        released += 1
    return released


@transaction.atomic
def convert_commitment_to_actual_service(*, po, invoice, user) -> SpendLedger:
    """
    Moves spend from COMMITMENT to ACTUAL when an invoice is approved/paid.
    """
    cost_center = po.cost_center
    budget = Budget.objects.filter(cost_center=cost_center).order_by("-created_at").first()
    if not budget:
        raise ValidationError(f"No budget found for Cost Center '{cost_center.code}'.")

    amount = invoice.total_amount
    if budget.committed_amount >= amount:
        budget.committed_amount -= amount
    else:
        budget.committed_amount = Decimal("0.00")
    budget.actual_amount += amount
    budget.save(update_fields=["committed_amount", "actual_amount", "updated_at"])

    ledger_entry = SpendLedger.objects.create(
        budget=budget,
        entry_type=SpendLedger.ENTRY_ACTUAL,
        amount=amount,
        reference_number=invoice.invoice_number,
        description=f"Actual Invoice Spend for PO {po.po_number}",
    )

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="Budget",
        target_object_id=str(budget.id),
        new_state={
            "actual_amount": str(budget.actual_amount),
            "invoice_number": invoice.invoice_number,
            "po_number": po.po_number,
        },
    )

    return ledger_entry
