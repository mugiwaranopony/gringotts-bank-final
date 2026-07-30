# banking/urls.py
from django.urls import path

from .views import dashboard, transaction_list

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("transactions/", transaction_list, name="transaction_list"),
]
