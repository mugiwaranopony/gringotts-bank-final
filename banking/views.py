# banking/views.py
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from .models import Transaction, get_or_create_account
from .forms import DepositForm, WithdrawForm, TransferForm


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
            account.balance += amount
            account.save()
            Transaction.objects.create(
                account=account,
                transaction_type=Transaction.DEPOSIT,
                amount=amount,
            )
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
            if amount > account.balance:
                form.add_error("amount", "Not enough money in your account!")
            else:
                account.balance -= amount
                account.save()
                Transaction.objects.create(
                    account=account,
                    transaction_type=Transaction.WITHDRAW,
                    amount=amount,
                )
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
            elif amount > account.balance:
                form.add_error("amount", "Not enough money in your account!")
            else:
                recipient_user = get_user_model().objects.get(username=recipient_name)
                recipient_account = get_or_create_account(recipient_user)

                account.balance -= amount
                account.save()
                recipient_account.balance += amount
                recipient_account.save()

                Transaction.objects.create(
                    account=account,
                    transaction_type=Transaction.TRANSFER_OUT,
                    amount=amount,
                    description=f"To {recipient_name}",
                )
                Transaction.objects.create(
                    account=recipient_account,
                    transaction_type=Transaction.TRANSFER_IN,
                    amount=amount,
                    description=f"From {request.user.username}",
                )
                messages.success(request, f"You sent ${amount} to {recipient_name}.")
                return redirect("dashboard")
    else:
        form = TransferForm()
    return render(request, "banking/transfer.html", {"form": form})
