from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import path
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .models import PRAttachment, PRLine, PurchaseRequisition
from .selectors import (
    build_department_dashboard_context,
    build_requisition_detail,
    get_requisition_by_id,
    requisitions_visible_to,
)
from .services import (
    WorkflowConflict,
    department_need_review_service,
    next_workflow_label,
    request_pr_clarification_service,
)


class ApprovalActionThrottle(UserRateThrottle):
    scope = "department_approval"


def _api_error(code, message, http_status, details=None):
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


class PRLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PRLine
        fields = "__all__"


class PRAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PRAttachment
        fields = "__all__"


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    lines = PRLineSerializer(many=True, read_only=True)
    attachments = PRAttachmentSerializer(many=True, read_only=True)
    requester_email = serializers.CharField(source="requester.email", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = PurchaseRequisition
        fields = "__all__"


class PurchaseRequisitionViewSet(viewsets.ModelViewSet):
    queryset = (
        PurchaseRequisition.objects.select_related("requester", "department", "cost_center")
        .prefetch_related("lines", "attachments")
        .all()
    )
    serializer_class = PurchaseRequisitionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.role_code in [
            "SUPER_ADMIN",
            "PROC_EXEC",
            "PROC_MGR",
            "AUDITOR",
        ]:
            return self.queryset
        # Requester / Approver scoped by department
        if user.department_id:
            return self.queryset.filter(department_id=user.department_id)
        return self.queryset.filter(requester=user)


class DepartmentApprovalQueueAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role_code not in ("DEPT_APPROVER", "SUPER_ADMIN"):
            return _api_error(
                "PERMISSION_DENIED",
                "The department approval queue is limited to department approvers.",
                status.HTTP_403_FORBIDDEN,
            )
        context = build_department_dashboard_context(request.user, request.query_params)
        page = context["page_obj"]
        return Response(
            {
                "kpis": {
                    "pending_approval": context["kpi"]["pending_approval"],
                    "approved_today": context["kpi"]["approved_today"],
                    "rejected_today": context["kpi"]["rejected_today"],
                    "total_pending_value": str(context["kpi"]["total_pending_value"]),
                    "overdue_approvals": context["kpi"]["overdue_approvals"],
                    "average_approval_hours": context["kpi"]["average_approval_hours"],
                },
                "count": page.paginator.count,
                "page": page.number,
                "num_pages": page.paginator.num_pages,
                "results": [
                    {
                        "id": row["id"],
                        "pr_number": row["pr_number"],
                        "requester": row["requester"],
                        "department": row["department"],
                        "cost_center": row["cost_center"],
                        "description": row["description"],
                        "total_amount": str(row["total_amount"]),
                        "currency": row["currency"],
                        "budget_state": row["budget_state"],
                        "status": row["status"],
                        "priority": row["priority"],
                        "aging": row["aging"],
                        "next_step": row["next_step"],
                    }
                    for row in context["requisition_rows"]
                ],
            }
        )


class DepartmentApprovalDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        requisition = requisitions_visible_to(request.user).filter(pk=pk).first()
        if requisition is None:
            return _api_error(
                "NOT_FOUND", "Purchase requisition was not found.", status.HTTP_404_NOT_FOUND
            )
        detail = build_requisition_detail(request.user, requisition)
        row = detail["row"]
        return Response(
            {
                "id": row["id"],
                "pr_number": row["pr_number"],
                "status": row["status"],
                "justification": detail["justification"],
                "lines": [
                    {
                        "description": line["description"],
                        "quantity": str(line["quantity"]),
                        "unit": line["unit"],
                        "unit_price": str(line["unit_price"]),
                        "total": str(line["total"]),
                    }
                    for line in detail["lines"]
                ],
                "grand_total": str(detail["grand_total"]),
                "currency": detail["currency"],
                "budget": {
                    "state": detail["budget"]["state"],
                    "allocated": _money(detail["budget"]["allocated"]),
                    "available": _money(detail["budget"]["available"]),
                    "reserved": _money(detail["budget"]["reserved"]),
                    "committed": _money(detail["budget"]["committed"]),
                    "actual": _money(detail["budget"]["actual"]),
                    "requested": _money(detail["budget"]["requested"]),
                },
                "authority": {
                    "within_limit": detail["authority"]["within_limit"],
                    "limit_amount": _money(detail["authority"]["limit_amount"]),
                    "unlimited": detail["authority"]["unlimited"],
                    "message": detail["authority"]["message"],
                },
                "next_step": detail["next_step"],
                "can_decide": detail["can_decide"],
                "history": [
                    {
                        "actor": item["actor"],
                        "role": item["role"],
                        "action": item["action"],
                        "comment": item["comment"],
                        "previous_state": item["previous_state"],
                        "new_state": item["new_state"],
                        "timestamp": item["timestamp"].isoformat(),
                    }
                    for item in detail["history"]
                ],
            }
        )


def _money(value):
    return None if value is None else str(value)


class DepartmentNeedReviewAPIView(APIView):
    """
    Session-authenticated department need decision.
    POST /api/v1/requisitions/{id}/department-review/
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ApprovalActionThrottle]

    def post(self, request, pk):
        requisition = get_requisition_by_id(pk)
        if requisition is None:
            return Response(
                {
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "Purchase requisition was not found.",
                        "details": {},
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        decision = request.data.get("decision", "")
        comments = request.data.get("comments", "")
        try:
            updated = department_need_review_service(
                requisition=requisition,
                approver=request.user,
                decision=decision,
                comments=comments,
            )
        except DjangoPermissionDenied as exc:
            return Response(
                {"error": {"code": "PERMISSION_DENIED", "message": str(exc), "details": {}}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            return _api_error(
                "VALIDATION_ERROR",
                " ".join(exc.messages),
                status.HTTP_400_BAD_REQUEST,
                {"messages": exc.messages},
            )
        except WorkflowConflict as exc:
            return _api_error("CONFLICT", exc.message, status.HTTP_409_CONFLICT)
        return Response(
            {
                "pr_number": updated.pr_number,
                "status": updated.status,
                "decision": decision,
                "next_step": next_workflow_label(updated),
            }
        )


class RequisitionClarificationAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ApprovalActionThrottle]

    def post(self, request, pk):
        requisition = get_requisition_by_id(pk)
        if requisition is None:
            return _api_error(
                "NOT_FOUND", "Purchase requisition was not found.", status.HTTP_404_NOT_FOUND
            )
        question = request.data.get("question") or request.data.get("comments") or ""
        try:
            updated = request_pr_clarification_service(
                requisition=requisition,
                approver=request.user,
                question=question,
            )
        except DjangoPermissionDenied as exc:
            return _api_error("PERMISSION_DENIED", str(exc), status.HTTP_403_FORBIDDEN)
        except DjangoValidationError as exc:
            return _api_error(
                "VALIDATION_ERROR",
                " ".join(exc.messages),
                status.HTTP_400_BAD_REQUEST,
                {"messages": exc.messages},
            )
        except WorkflowConflict as exc:
            return _api_error("CONFLICT", exc.message, status.HTTP_409_CONFLICT)
        return Response(
            {
                "pr_number": updated.pr_number,
                "status": updated.status,
                "next_step": next_workflow_label(updated),
            }
        )


router = DefaultRouter()
router.register(r"", PurchaseRequisitionViewSet, basename="requisition")

urlpatterns = [
    path(
        "approval-queue/",
        DepartmentApprovalQueueAPIView.as_view(),
        name="requisition-approval-queue",
    ),
    path(
        "<uuid:pk>/approval-detail/",
        DepartmentApprovalDetailAPIView.as_view(),
        name="requisition-approval-detail",
    ),
    path(
        "<uuid:pk>/department-review/",
        DepartmentNeedReviewAPIView.as_view(),
        name="requisition-department-review",
    ),
    path(
        "<uuid:pk>/clarification/",
        RequisitionClarificationAPIView.as_view(),
        name="requisition-clarification",
    ),
] + router.urls
