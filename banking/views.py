# banking/views.py
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from .models import (
    InsufficientFunds,
    deposit_money,
    get_or_create_account,
    transfer_money,
    withdraw_money,
)
from .forms import DepositForm, TransferForm, WithdrawForm


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


@login_required
def deposit(request):
    account = get_or_create_account(request.user)
    if request.method == "POST":
        form = DepositForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            deposit_money(account, amount)
            messages.success(request, f"You deposited ${amount}.")
            return redirect("dashboard")
    else:
        form = DepositForm()
    return render(request, "banking/deposit.html", {"form": form})


@login_required
def withdraw(request):
    account = get_or_create_account(request.user)
    if request.method == "POST":
        form = WithdrawForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            try:
                withdraw_money(account, amount)
            except InsufficientFunds:
                form.add_error("amount", "Not enough money in your account!")
            else:
                messages.success(request, f"You withdrew ${amount}.")
                return redirect("dashboard")
    else:
        form = WithdrawForm()
    return render(request, "banking/withdraw.html", {"form": form})


@login_required
def transfer(request):
    account = get_or_create_account(request.user)
    if request.method == "POST":
        form = TransferForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            recipient_name = form.cleaned_data["recipient"]
            if recipient_name == request.user.username:
                form.add_error("recipient", "You cannot send money to yourself!")
            else:
                recipient_user = get_user_model().objects.get(username=recipient_name)
                recipient_account = get_or_create_account(recipient_user)
                try:
                    transfer_money(account, recipient_account, amount)
                except InsufficientFunds:
                    form.add_error("amount", "Not enough money in your account!")
                else:
                    messages.success(
                        request, f"You sent ${amount} to {recipient_name}."
                    )
                    return redirect("dashboard")
    else:
        form = TransferForm()
    return render(request, "banking/transfer.html", {"form": form})
