from django.urls import path

from .views import loans_page, repay_loan

urlpatterns = [
    path("", loans_page, name="loans"),
    path("<int:pk>/repay/", repay_loan, name="repay_loan"),
]
