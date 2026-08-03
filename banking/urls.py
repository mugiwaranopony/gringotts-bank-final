# banking/urls.py
from django.urls import path

from .views import (
    dashboard,
    deposit,
    payment_requests,
    request_payment,
    resolve_payment_request,
    transaction_list,
    transfer,
    withdraw,
)

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("transactions/", transaction_list, name="transaction_list"),
    path("deposit/", deposit, name="deposit"),
    path("withdraw/", withdraw, name="withdraw"),
    path("transfer/", transfer, name="transfer"),
    path("requests/", payment_requests, name="payment_requests"),
    path("requests/new/", request_payment, name="request_payment"),
    path(
        "requests/<int:pk>/<str:action>/",
        resolve_payment_request,
        name="resolve_payment_request",
    ),
]
