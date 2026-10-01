import threading
import uuid

# Thread-local storage for request correlation context
_request_context = threading.local()


def get_current_request_id():
    return getattr(_request_context, "request_id", None)


def get_current_user():
    return getattr(_request_context, "user", None)


def get_client_ip():
    return getattr(_request_context, "client_ip", None)


def get_user_agent():
    return getattr(_request_context, "user_agent", "")


class RequestIDMiddleware:
    """
    Middleware that assigns a unique UUID correlation ID (X-Request-ID) to every HTTP request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.request_id = request_id
        _request_context.request_id = request_id

        # Determine client IP address
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR")
        _request_context.client_ip = ip
        _request_context.user_agent = request.META.get("HTTP_USER_AGENT", "")

        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        return response


class AuditContextMiddleware:
    """
    Middleware that sets the current user in thread-local storage for audit tracking.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            _request_context.user = request.user
        else:
            _request_context.user = None

        return self.get_response(request)
