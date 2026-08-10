# banking/models.py
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.utils import timezone

WELCOME_BONUS = Decimal("1000.0")


class InsufficientFunds(Exception):
    """Raised when an account does not have enough money to cover an amount."""


class AlreadyResolved(Exception):
    """Raised when a payment request has already been accepted or refused."""


class Account(models.Model):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account",
    )
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Last line of defence: even if a bug slips past the checks in
            # the code, the database refuses to store a negative balance.
            models.CheckConstraint(
                condition=models.Q(balance__gte=0),
                name="account_balance_not_negative",
            ),
        ]

    def __str__(self):
        return f"{self.owner.username} - ${self.balance}"


class Transaction(models.Model):
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    BONUS = "BONUS"

    TRANSACTION_TYPES = [
        (DEPOSIT, "Deposit"),
        (WITHDRAW, "Withdraw"),
        (TRANSFER_IN, "Transfer in"),
        (TRANSFER_OUT, "Transfer out"),
        (BONUS, "Welcome bonus"),
    ]

    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=200, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.account.owner.username}: {self.transaction_type} ${self.amount}"

    @property
    def is_credit(self):
        """Money comming IN to the account."""
        return self.transaction_type in (self.DEPOSIT, self.TRANSFER_IN, self.BONUS)


class PaymentRequest(models.Model):
    """One customer asking another to send them money.

    Nothing moves until the payer accepts, so a request is just a note
    with a status -- it is not a transaction.
    """

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"

    STATUSES = [
        (PENDING, "Pending"),
        (ACCEPTED, "Accepted"),
        (DECLINED, "Declined"),
        (CANCELLED, "Cancelled"),
    ]

    # The person who wants the money...
    requester = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="requests_sent",
    )
    # ...and the person being asked to pay it.
    payer = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="requests_received",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="payment_request_amount_positive",
            ),
            models.CheckConstraint(
                condition=~models.Q(requester=models.F("payer")),
                name="payment_request_not_to_yourself",
            ),
        ]

    def __str__(self):
        return (
            f"{self.requester.owner.username} asked "
            f"{self.payer.owner.username} for ${self.amount}"
        )

    @property
    def is_pending(self):
        return self.status == self.PENDING


def get_or_create_account(user):
    """Return the user's account. New accoutns get a welcome bonus!"""
    account, created = Account.objects.get_or_create(owner=user)
    if created:
        account.balance = WELCOME_BONUS
        account.save()
        Transaction.objects.create(
            account=account,
            transaction_type=Transaction.BONUS,
            amount=WELCOME_BONUS,
            description="Welcome to Gringotts Bank!",
        )
    return account


def _lock(pk):
    """Re-read an account, holding a row lock until the transaction ends.

    Without the lock two requests can both read the same balance, both
    subtract from it, and both save -- so one of the withdrawals is lost.
    Note that SQLite ignores select_for_update(); on that backend the
    atomic() block and the database constraint are what protect us.
    """
    return Account.objects.select_for_update().get(pk=pk)


def deposit_money(account, amount, description=""):
    """Add money to an account and record it in the ledger."""
    with transaction.atomic():
        account = _lock(account.pk)
        account.balance += amount
        account.save()
        return Transaction.objects.create(
            account=account,
            transaction_type=Transaction.DEPOSIT,
            amount=amount,
            description=description,
        )


def withdraw_money(account, amount, description=""):
    """Take money out of an account and record why, or raise InsufficientFunds."""
    with transaction.atomic():
        account = _lock(account.pk)
        if amount > account.balance:
            raise InsufficientFunds
        account.balance -= amount
        account.save()
        return Transaction.objects.create(
            account=account,
            transaction_type=Transaction.WITHDRAW,
            amount=amount,
            description=description,
        )


def transfer_money(sender, recipient, amount):
    """Move money between two accounts, all-or-nothing.

    Both sides happen inside one atomic() block, so we can never take money
    off one account without adding it to the other.
    """
    if sender.pk == recipient.pk:
        raise ValueError("Cannot transfer money to the same account.")

    with transaction.atomic():
        # Lock both rows, always lowest pk first. If alice pays bob while bob
        # pays alice, a fixed order stops the two requests deadlocking.
        locked = {pk: _lock(pk) for pk in sorted([sender.pk, recipient.pk])}
        sender, recipient = locked[sender.pk], locked[recipient.pk]

        if amount > sender.balance:
            raise InsufficientFunds

        sender.balance -= amount
        sender.save()
        recipient.balance += amount
        recipient.save()

        Transaction.objects.create(
            account=sender,
            transaction_type=Transaction.TRANSFER_OUT,
            amount=amount,
            description=f"To {recipient.owner.username}",
        )
        Transaction.objects.create(
            account=recipient,
            transaction_type=Transaction.TRANSFER_IN,
            amount=amount,
            description=f"From {sender.owner.username}",
        )


def request_money(requester, payer, amount, note=""):
    """Ask another customer to send you money. Moves nothing on its own."""
    if requester.pk == payer.pk:
        raise ValueError("Cannot request money from yourself.")

    return PaymentRequest.objects.create(
        requester=requester,
        payer=payer,
        amount=amount,
        note=note,
    )


def _claim_pending(payment_request, account, allowed_field):
    """Lock a pending request and check `account` is allowed to resolve it.

    The lock matters here for the same reason it did for transfers: without
    it, two clicks on Accept can both read status="PENDING" and both pay.
    """
    locked = PaymentRequest.objects.select_for_update().get(pk=payment_request.pk)
    if getattr(locked, allowed_field) != account.pk:
        raise PermissionDenied("That payment request is not yours to resolve.")
    if not locked.is_pending:
        raise AlreadyResolved
    return locked


def accept_payment_request(payment_request, payer):
    """Pay a request. Raises InsufficientFunds if the payer cannot cover it."""
    with transaction.atomic():
        locked = _claim_pending(payment_request, payer, "payer_id")

        # If this raises, the whole block rolls back and the request stays
        # pending -- so a failed payment never marks the request as done.
        transfer_money(locked.payer, locked.requester, locked.amount)

        locked.status = PaymentRequest.ACCEPTED
        locked.resolved_at = timezone.now()
        locked.save()
        return locked


def decline_payment_request(payment_request, payer):
    """Refuse a request you were sent."""
    with transaction.atomic():
        locked = _claim_pending(payment_request, payer, "payer_id")
        locked.status = PaymentRequest.DECLINED
        locked.resolved_at = timezone.now()
        locked.save()
        return locked


def cancel_payment_request(payment_request, requester):
    """Withdraw a request you sent, before it is paid."""
    with transaction.atomic():
        locked = _claim_pending(payment_request, requester, "requester_id")
        locked.status = PaymentRequest.CANCELLED
        locked.resolved_at = timezone.now()
        locked.save()
        return locked
