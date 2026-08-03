# banking/models.py
from decimal import Decimal

from django.conf import settings
from django.db import models

WELCOME_BONUS = Decimal("1000.0")


class Account(models.Model):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account",
    )
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

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
