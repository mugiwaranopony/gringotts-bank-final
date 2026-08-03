# banking/urls.py
from django.urls import path

from .views import dashboard, transaction_list, deposit, withdraw, transfer

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("transactions/", transaction_list, name="transaction_list"),
    path("deposit/", deposit, name="deposit"),
    path("withdraw/", withdraw, name="withdraw"),
    path("transfer/", transfer, name="transfer"),
]
