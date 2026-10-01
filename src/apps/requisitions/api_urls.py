from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import path
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from .models import PRAttachment, PRLine, PurchaseRequisition
from .selectors import get_requisition_by_id
from .services import department_need_review_service


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


class DepartmentNeedReviewAPIView(APIView):
    """
    Session-authenticated department need decision.
    POST /api/v1/requisitions/{id}/department-review/
    """

    permission_classes = [IsAuthenticated]

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
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": " ".join(exc.messages),
                        "details": {"messages": exc.messages},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "pr_number": updated.pr_number,
                "status": updated.status,
                "decision": decision,
            }
        )


router = DefaultRouter()
router.register(r"", PurchaseRequisitionViewSet, basename="requisition")

urlpatterns = [
    path(
        "<uuid:pk>/department-review/",
        DepartmentNeedReviewAPIView.as_view(),
        name="requisition-department-review",
    ),
] + router.urls
