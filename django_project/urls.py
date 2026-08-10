# django_project/urls.py
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("bank/", include("banking.urls")),
    path("rewards/", include("rewards.urls")),
    path("loans/", include("loans.urls")),
    path("", include("pages.urls")),
]
