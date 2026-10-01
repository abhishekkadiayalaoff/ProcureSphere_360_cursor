from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.requisitions.forms import DepartmentNeedReviewForm
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.selectors import (
    build_department_dashboard_context,
    requisitions_visible_to,
)
from apps.requisitions.services import department_need_review_service


def _can_open_department_dashboard(user):
    return user.is_authenticated and user.role_code in (Role.DEPT_APPROVER, Role.SUPER_ADMIN)


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
    context = build_department_dashboard_context(request.user)
    context["role_code"] = request.user.role_code
    context["review_form"] = DepartmentNeedReviewForm()
    return render(request, "pages/dashboards/dept_approver.html", context)


@login_required(login_url="/login/")
@require_POST
def department_need_review_view(request, pr_id):
    if not _can_open_department_dashboard(request.user):
        raise PermissionDenied("Only a department approver can record this decision.")

    requisition = get_object_or_404(
        PurchaseRequisition.objects.select_related("department", "cost_center", "requester"),
        pk=pr_id,
    )
    form = DepartmentNeedReviewForm(request.POST)
    if not form.is_valid():
        error_text = (
            " ".join(str(err) for errors in form.errors.values() for err in errors)
            or "The decision could not be recorded."
        )
        messages.error(request, error_text)
        return redirect("department_dashboard")

    try:
        department_need_review_service(
            requisition=requisition,
            approver=request.user,
            decision=form.cleaned_data["decision"],
            comments=form.cleaned_data["comments"],
        )
    except PermissionDenied:
        raise
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("department_dashboard")

    if form.cleaned_data["decision"] == "CONFIRM_NEED":
        messages.success(
            request,
            f"{requisition.pr_number} need confirmed and sent to budget review.",
        )
    else:
        messages.success(
            request,
            f"{requisition.pr_number} rejected and the decision was documented.",
        )
    return redirect("department_dashboard")
