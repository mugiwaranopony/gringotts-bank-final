# banking/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Transaction, get_or_create_account


@login_required
def dashboard(request):
    account = get_or_create_account(request.user)
    recent_transactions = account.transactions.all()[:5]
    return render(
        request,
        "banking/dashboard.html",
        {
            "account": account,
            "recent_transactions": recent_transactions,
        },
    )


@login_required
def transaction_list(request):
    account = get_or_create_account(request.user)
    transactions = account.transactions.all()
    return render(
        request,
        "banking/transaction_list.html",
        {
            "transactions": transactions,
        },
    )
