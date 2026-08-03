# banking/models.py
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction

WELCOME_BONUS = Decimal("1000.0")


class InsufficientFunds(Exception):
    """Raised when an account does not have enough money to cover an amount."""


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


def deposit_money(account, amount):
    """Add money to an account and record it in the ledger."""
    with transaction.atomic():
        account = _lock(account.pk)
        account.balance += amount
        account.save()
        return Transaction.objects.create(
            account=account,
            transaction_type=Transaction.DEPOSIT,
            amount=amount,
        )


def withdraw_money(account, amount):
    """Take money out of an account, or raise InsufficientFunds."""
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
