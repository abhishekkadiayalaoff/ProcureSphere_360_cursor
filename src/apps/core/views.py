from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import Avg, Sum
from django.http import JsonResponse
from django.shortcuts import render
from redis import Redis
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.budgets.models import Budget, SpendLedger
from apps.invoices.models import MatchException, SupplierInvoice
from apps.orders.models import PurchaseOrder
from apps.reports.services import (
    get_contract_expiry_report,
    get_invoice_exception_aging_report,
    get_po_status_report,
    get_pr_aging_report,
    get_spend_analytics_report,
    get_supplier_performance_report,
)
from apps.requisitions.forms import DepartmentNeedReviewForm
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.selectors import build_department_dashboard_context
from apps.scorecards.models import VendorScorecard
from apps.sourcing.models import SourcingEvent
from apps.vendors.models import Vendor


def health_check_view(request):
    """
    Health check endpoint returning DB and Redis status.
    """
    health_status = {
        "status": "healthy",
        "service": "ProcureSphere 360",
        "database": "unknown",
        "redis": "unknown",
    }

    # Check Database connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            health_status["database"] = "ok"
    except Exception as e:
        health_status["database"] = f"error: {str(e)}"
        health_status["status"] = "unhealthy"

    # Check Redis connection
    try:
        redis_client = Redis.from_url(settings.REDIS_URL, socket_timeout=2)
        if redis_client.ping():
            health_status["redis"] = "ok"
    except Exception as e:
        health_status["redis"] = f"error: {str(e)}"
        health_status["status"] = "unhealthy"

    http_status = 200 if health_status["status"] == "healthy" else 503
    return JsonResponse(health_status, status=http_status)


class HealthAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return health_check_view(request)


@login_required(login_url="/login/")
def home_view(request):
    """
    Dashboard / Landing page view based on user role with live aggregated ERP metrics.
    """
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )

    template_map = {
        "SUPER_ADMIN": "pages/dashboards/super_admin.html",
        "REQUESTER": "pages/dashboards/requester.html",
        "DEPT_APPROVER": "pages/dashboards/dept_approver.html",
        "PROC_EXEC": "pages/dashboards/proc_exec.html",
        "PROC_MGR": "pages/dashboards/proc_mgr.html",
        "FINANCE_AP": "pages/dashboards/finance_ap.html",
        "STORES_RECEIVER": "pages/dashboards/stores_receiver.html",
        "LEGAL_MGR": "pages/dashboards/legal_mgr.html",
        "AUDITOR": "pages/dashboards/auditor.html",
        "VENDOR_USER": "pages/dashboards/vendor_user.html",
    }

    template_name = template_map.get(role_code, "pages/dashboard.html")

    if role_code == "DEPT_APPROVER":
        context = {
            "project_name": "ProcureSphere 360",
            "version": "1.0.0-DRAFT",
            "role_code": role_code,
        }
        context.update(build_department_dashboard_context(request.user, request.GET))
        context["review_form"] = DepartmentNeedReviewForm()
        return render(request, template_name, context)

    total_pr_count = PurchaseRequisition.objects.count()
    pending_pr_count = PurchaseRequisition.objects.filter(
        status__in=["SUBMITTED", "MANAGER_REVIEW", "BUDGET_REVIEW"]
    ).count()

    total_vendors = Vendor.objects.count()
    active_vendors = Vendor.objects.filter(status="ACTIVE").count()
    kyc_review_vendors = Vendor.objects.filter(status="KYC_REVIEW").count()

    open_sourcing_events = SourcingEvent.objects.filter(
        status__in=["PUBLISHED", "BID_WINDOW"]
    ).count()
    total_pos = PurchaseOrder.objects.count()

    total_invoices = SupplierInvoice.objects.count()
    pending_exceptions = MatchException.objects.filter(status="OPEN").count()

    allocated_budget = Budget.objects.aggregate(total=Sum("allocated_amount"))["total"] or 0
    committed_spend = (
        SpendLedger.objects.filter(entry_type="COMMITMENT").aggregate(total=Sum("amount"))["total"]
        or 0
    )
    actual_spend = (
        SpendLedger.objects.filter(entry_type="ACTUAL").aggregate(total=Sum("amount"))["total"] or 0
    )

    avg_scorecard = VendorScorecard.objects.aggregate(avg=Avg("composite_score"))["avg"] or 0.0

    context = {
        "project_name": "ProcureSphere 360",
        "version": "1.0.0-DRAFT",
        "role_code": role_code,
        "metrics": {
            "total_pr_count": total_pr_count,
            "pending_pr_count": pending_pr_count,
            "total_vendors": total_vendors,
            "active_vendors": active_vendors,
            "kyc_review_vendors": kyc_review_vendors,
            "open_sourcing_events": open_sourcing_events,
            "total_pos": total_pos,
            "total_invoices": total_invoices,
            "pending_exceptions": pending_exceptions,
            "allocated_budget": float(allocated_budget),
            "committed_spend": float(committed_spend),
            "actual_spend": float(actual_spend),
            "avg_scorecard": round(float(avg_scorecard), 1),
        },
    }

    if role_code == "SUPER_ADMIN":
        pr_data = get_pr_aging_report()
        spend_data = get_spend_analytics_report()
        po_data = get_po_status_report()
        inv_data = get_invoice_exception_aging_report()
        contract_data = get_contract_expiry_report()
        scorecard_data = get_supplier_performance_report()

        context["dashboard_summary"] = {
            "total_prs": len(pr_data),
            "total_pos": len(po_data),
            "total_spend": sum(item["actual"] for item in spend_data) if spend_data else 0,
            "pending_exceptions": len([item for item in inv_data if item["status"] == "OPEN"]),
            "expiring_contracts": len(
                [item for item in contract_data if 0 <= item["days_to_expiry"] <= 60]
            ),
            "vendor_count": len(scorecard_data),
        }

    return render(request, template_name, context)
