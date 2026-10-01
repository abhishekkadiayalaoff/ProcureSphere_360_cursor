from decimal import Decimal

from django.db import models

from apps.core.models import TimeStampedModel


class ApprovalPolicy(TimeStampedModel):
    MODULE_PR = "PR"
    MODULE_PO = "PO"
    MODULE_SOURCING = "SOURCING"
    MODULE_INVOICE = "INVOICE"
    MODULE_VENDOR = "VENDOR"

    MODULE_CHOICES = [
        (MODULE_PR, "Purchase Requisition"),
        (MODULE_PO, "Purchase Order"),
        (MODULE_SOURCING, "Sourcing Award"),
        (MODULE_INVOICE, "Invoice Match Exception"),
        (MODULE_VENDOR, "Vendor KYC Approval"),
    ]

    name = models.CharField(max_length=200)
    module = models.CharField(max_length=50, choices=MODULE_CHOICES)
    department = models.ForeignKey(
        "organization.Department",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="approval_policies",
    )
    min_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    max_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} [{self.module}] (Min: ${self.min_amount})"


class ApprovalStep(TimeStampedModel):
    policy = models.ForeignKey(ApprovalPolicy, on_delete=models.CASCADE, related_name="steps")
    step_number = models.PositiveIntegerField()
    approver_role = models.ForeignKey(
        "accounts.Role", on_delete=models.PROTECT, related_name="approval_steps"
    )
    specific_approver = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_approval_steps",
    )
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["step_number"]
        unique_together = ("policy", "step_number")

    def __str__(self):
        return f"Step {self.step_number}: {self.approver_role.name} for {self.policy.name}"


class ApprovalAction(TimeStampedModel):
    ACTION_SUBMIT = "SUBMITTED"
    ACTION_APPROVE = "APPROVED"
    ACTION_REJECT = "REJECTED"
    ACTION_DELEGATE = "DELEGATED"
    ACTION_CLARIFY = "CLARIFICATION"

    ACTION_CHOICES = [
        (ACTION_SUBMIT, "Submitted"),
        (ACTION_APPROVE, "Approved"),
        (ACTION_REJECT, "Rejected"),
        (ACTION_DELEGATE, "Delegated"),
        (ACTION_CLARIFY, "Clarification requested"),
    ]

    policy_step = models.ForeignKey(ApprovalStep, on_delete=models.SET_NULL, null=True, blank=True)
    target_object_id = models.UUIDField(db_index=True)
    target_model_name = models.CharField(max_length=100)
    actor = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="approval_actions"
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    comments = models.TextField(blank=True)
    previous_state = models.CharField(max_length=100)
    new_state = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.action} by {self.actor.email} on {self.target_model_name} ({self.target_object_id})"


class ApprovalDelegate(TimeStampedModel):
    approver = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="delegator_records"
    )
    delegate = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="delegated_records"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    reason = models.TextField(blank=True)

    def __str__(self):
        return f"Delegate {self.delegate.email} for {self.approver.email} ({self.start_date} to {self.end_date})"
