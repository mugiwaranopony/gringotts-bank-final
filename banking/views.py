# banking/views.py
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_POST

from .models import (
    AlreadyResolved,
    InsufficientFunds,
    PaymentRequest,
    accept_payment_request,
    cancel_payment_request,
    decline_payment_request,
    deposit_money,
    get_or_create_account,
    request_money,
    transfer_money,
    withdraw_money,
)
from .forms import DepositForm, RequestMoneyForm, TransferForm, WithdrawForm


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
            "pending_requests": account.requests_received.filter(
                status=PaymentRequest.PENDING
            ).count(),
        },
    )


@login_required
def payment_requests(request):
    account = get_or_create_account(request.user)
    return render(
        request,
        "banking/payment_requests.html",
        {
            "incoming": account.requests_received.select_related("requester__owner"),
            "outgoing": account.requests_sent.select_related("payer__owner"),
        },
    )


@login_required
def request_payment(request):
    account = get_or_create_account(request.user)
    if request.method == "POST":
        form = RequestMoneyForm(request.POST)
        if form.is_valid():
            payer_name = form.cleaned_data["payer"]
            if payer_name == request.user.username:
                form.add_error("payer", "You cannot request money from yourself!")
            else:
                payer_user = get_user_model().objects.get(username=payer_name)
                request_money(
                    requester=account,
                    payer=get_or_create_account(payer_user),
                    amount=form.cleaned_data["amount"],
                    note=form.cleaned_data["note"],
                )
                messages.success(
                    request,
                    f"You asked {payer_name} for ${form.cleaned_data['amount']}.",
                )
                return redirect("payment_requests")
    else:
        form = RequestMoneyForm()
    return render(request, "banking/request_payment.html", {"form": form})


# Only these three actions exist. Anything else in the URL is a 404 rather
# than quietly falling through to one of them.
RESOLVERS = {
    "accept": accept_payment_request,
    "decline": decline_payment_request,
    "cancel": cancel_payment_request,
}


@login_required
@require_POST
def resolve_payment_request(request, pk, action):
    """Accept, decline or cancel a payment request.

    POST only: these change money and state, so they must not happen just
    because someone followed a link or a browser prefetched it.
    """
    resolve = RESOLVERS.get(action)
    if resolve is None:
        raise Http404("No such action.")

    account = get_or_create_account(request.user)
    payment_request = get_object_or_404(PaymentRequest, pk=pk)

    try:
        # Raises PermissionDenied (-> 403) if this request is not the
        # user's to resolve. We deliberately let that one bubble up.
        resolve(payment_request, account)
    except InsufficientFunds:
        messages.error(request, "Not enough money in your account!")
    except AlreadyResolved:
        messages.warning(request, "That request has already been dealt with.")
    else:
        if action == "accept":
            messages.success(
                request,
                f"You paid {payment_request.requester.owner.username} "
                f"${payment_request.amount}.",
            )
        else:
            messages.info(request, f"Request {action}d.")

    return redirect("payment_requests")


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
