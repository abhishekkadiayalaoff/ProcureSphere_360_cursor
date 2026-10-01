from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.requisitions.forms import ClarificationForm, DepartmentNeedReviewForm
from apps.requisitions.models import PRAttachment, PurchaseRequisition
from apps.requisitions.selectors import (
    build_department_dashboard_context,
    build_requisition_detail,
    requisitions_visible_to,
)
from apps.requisitions.services import (
    WorkflowConflict,
    department_need_review_service,
    next_workflow_label,
    request_pr_clarification_service,
)


def _can_open_department_dashboard(user):
    return user.is_authenticated and user.role_code in (Role.DEPT_APPROVER, Role.SUPER_ADMIN)


def _filter_params(request):
    return {
        "q": request.GET.get("q", ""),
        "status": request.GET.get("status", ""),
        "department": request.GET.get("department", ""),
        "cost_center": request.GET.get("cost_center", ""),
        "amount_min": request.GET.get("amount_min", ""),
        "amount_max": request.GET.get("amount_max", ""),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "overdue": request.GET.get("overdue", ""),
        "priority": request.GET.get("priority", ""),
        "ordering": request.GET.get("ordering", ""),
        "page": request.GET.get("page", ""),
    }


@login_required(login_url="/login/")
def list_view(request):
    items = requisitions_visible_to(request.user)
    return render(request, "pages/requisitions/list.html", {"items": items})


@login_required(login_url="/login/")
def department_dashboard_view(request):
    if not _can_open_department_dashboard(request.user):
        raise PermissionDenied(
            "The department approval dashboard is limited to department approvers."
        )
    context = build_department_dashboard_context(request.user, _filter_params(request))
    context["role_code"] = request.user.role_code
    context["review_form"] = DepartmentNeedReviewForm()
    return render(request, "pages/dashboards/dept_approver.html", context)


@login_required(login_url="/login/")
def requisition_detail_view(request, pr_id):
    requisition = get_object_or_404(
        requisitions_visible_to(request.user).prefetch_related("lines", "attachments"),
        pk=pr_id,
    )
    detail = build_requisition_detail(request.user, requisition)
    return render(
        request,
        "pages/requisitions/detail.html",
        {
            "requisition": requisition,
            "detail": detail,
            "role_code": request.user.role_code,
            "reject_form": DepartmentNeedReviewForm(initial={"decision": "REJECT_NEED"}),
            "clarify_form": ClarificationForm(),
        },
    )


@login_required(login_url="/login/")
def attachment_download_view(request, attachment_id):
    attachment = get_object_or_404(
        PRAttachment.objects.select_related("requisition"), pk=attachment_id
    )
    visible = requisitions_visible_to(request.user).filter(pk=attachment.requisition_id).exists()
    if not visible:
        raise PermissionDenied("You cannot access this attachment.")
    if not attachment.file:
        raise Http404("Attachment file is missing.")
    filename = attachment.file.name.rsplit("/", 1)[-1]
    return FileResponse(attachment.file.open("rb"), as_attachment=True, filename=filename)


def _handle_decision(request, pr_id):
    if not _can_open_department_dashboard(request.user):
        raise PermissionDenied("Only a department approver can record this decision.")
    requisition = get_object_or_404(PurchaseRequisition, pk=pr_id)
    form = DepartmentNeedReviewForm(request.POST)
    if not form.is_valid():
        error_text = " ".join(str(err) for errors in form.errors.values() for err in errors)
        messages.error(request, error_text or "The decision could not be recorded.")
        return redirect("requisition_detail", pr_id=pr_id)
    try:
        updated = department_need_review_service(
            requisition=requisition,
            approver=request.user,
            decision=form.cleaned_data["decision"],
            comments=form.cleaned_data["comments"],
        )
    except PermissionDenied:
        raise
    except WorkflowConflict as exc:
        messages.error(request, exc.message)
        return redirect("requisition_detail", pr_id=pr_id)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("requisition_detail", pr_id=pr_id)
    messages.success(
        request,
        f"{updated.pr_number} is now {updated.get_status_display()}. Next: {next_workflow_label(updated)}.",
    )
    return redirect("requisition_detail", pr_id=updated.id)


@login_required(login_url="/login/")
@require_POST
def department_need_review_view(request, pr_id):
    return _handle_decision(request, pr_id)


@login_required(login_url="/login/")
@require_POST
def clarification_view(request, pr_id):
    if not _can_open_department_dashboard(request.user):
        raise PermissionDenied("Only a department approver can request clarification.")
    requisition = get_object_or_404(PurchaseRequisition, pk=pr_id)
    form = ClarificationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A clarification question is required.")
        return redirect("requisition_detail", pr_id=pr_id)
    try:
        updated = request_pr_clarification_service(
            requisition=requisition,
            approver=request.user,
            question=form.cleaned_data["question"],
        )
    except PermissionDenied:
        raise
    except WorkflowConflict as exc:
        messages.error(request, exc.message)
        return redirect("requisition_detail", pr_id=pr_id)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("requisition_detail", pr_id=pr_id)
    messages.success(
        request,
        f"Clarification requested on {updated.pr_number}. Status remains {updated.get_status_display()}.",
    )
    return redirect("requisition_detail", pr_id=updated.id)
