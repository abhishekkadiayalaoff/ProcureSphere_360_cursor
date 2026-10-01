from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.accounts.api_views import SessionLoginView, SessionLogoutView

from apps.accounts.views import portal_login_view, portal_logout_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", portal_login_view, name="login"),
    path("logout/", portal_logout_view, name="logout"),
    # Health Check & Frontend Domain pages
    path("", include("apps.core.urls")),
    path("vendors/", include("apps.vendors.urls")),
    path("requisitions/", include("apps.requisitions.urls")),
    path("orders/", include("apps.orders.urls")),
    path("contracts/", include("apps.contracts.urls")),
    path("invoices/", include("apps.invoices.urls")),
    path("notifications/", include("apps.notifications.urls")),
    # OpenAPI Schema & Swagger Docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # Session Authentication Endpoints
    path("api/v1/auth/login/", SessionLoginView.as_view(), name="session_login"),
    path("api/v1/auth/logout/", SessionLogoutView.as_view(), name="session_logout"),
    # Domain App API Endpoints (/api/v1/...)
    path("api/v1/accounts/", include("apps.accounts.api_urls")),
    path("api/v1/organization/", include("apps.organization.api_urls")),
    path("api/v1/approvals/", include("apps.approvals.api_urls")),
    path("api/v1/vendors/", include("apps.vendors.api_urls")),
    path("api/v1/requisitions/", include("apps.requisitions.api_urls")),
    path("api/v1/sourcing-events/", include("apps.sourcing.api_urls")),
    path("api/v1/purchase-orders/", include("apps.orders.api_urls")),
    path("api/v1/receipts/", include("apps.receipts.api_urls")),
    path("api/v1/invoices/", include("apps.invoices.api_urls")),
    path("api/v1/contracts/", include("apps.contracts.api_urls")),
    path("api/v1/budgets/", include("apps.budgets.api_urls")),
    path("api/v1/scorecards/", include("apps.scorecards.api_urls")),
    path("api/v1/notifications/", include("apps.notifications.api_urls")),
    path("api/v1/reports/", include("apps.reports.api_urls")),
    path("api/v1/audit/", include("apps.audit.api_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
