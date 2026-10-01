from django.urls import path

from . import views

urlpatterns = [
    path("", views.list_view, name="requisitions_list"),
    path("approvals/", views.department_dashboard_view, name="department_dashboard"),
    path("<uuid:pr_id>/", views.requisition_detail_view, name="requisition_detail"),
    path(
        "<uuid:pr_id>/department-review/",
        views.department_need_review_view,
        name="department_need_review",
    ),
    path(
        "<uuid:pr_id>/clarification/",
        views.clarification_view,
        name="requisition_clarification",
    ),
    path(
        "attachments/<uuid:attachment_id>/download/",
        views.attachment_download_view,
        name="requisition_attachment_download",
    ),
]
