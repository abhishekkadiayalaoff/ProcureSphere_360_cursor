from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import path

from .models import Notification


@login_required(login_url="/login/")
def open_notification_view(request, pk):
    note = get_object_or_404(Notification, pk=pk, recipient=request.user)
    note.is_read = True
    note.save(update_fields=["is_read", "updated_at"])
    return redirect(note.target_url or "/")


urlpatterns = [
    path("<uuid:pk>/open/", open_notification_view, name="notification_open"),
]
