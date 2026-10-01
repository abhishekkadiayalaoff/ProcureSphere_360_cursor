def shell(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"unread_notification_count": 0, "recent_notifications": []}
    from apps.notifications.models import Notification

    notes = Notification.objects.filter(recipient=user).order_by("-created_at")
    return {
        "unread_notification_count": notes.filter(is_read=False).count(),
        "recent_notifications": list(notes[:5]),
    }
