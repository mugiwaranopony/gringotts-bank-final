# banking/tests.py
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import (
    WELCOME_BONUS,
    Account,
    InsufficientFunds,
    Transaction,
    deposit_money,
    get_or_create_account,
    transfer_money,
    withdraw_money,
)

User = get_user_model()
PASSWORD = "testpass123"


def make_user(username):
    return User.objects.create_user(username=username, password=PASSWORD)


class AccountTests(TestCase):
    def test_new_account_gets_the_welcome_bonus(self):
        account = get_or_create_account(make_user("alice"))
        self.assertEqual(account.balance, WELCOME_BONUS)

        bonus = account.transactions.get()
        self.assertEqual(bonus.transaction_type, Transaction.BONUS)
        self.assertEqual(bonus.amount, WELCOME_BONUS)

    def test_bonus_is_only_paid_once(self):
        user = make_user("alice")
        get_or_create_account(user)
        account = get_or_create_account(user)

        self.assertEqual(Account.objects.count(), 1)
        self.assertEqual(account.balance, WELCOME_BONUS)
        self.assertEqual(account.transactions.count(), 1)

    def test_database_rejects_a_negative_balance(self):
        account = get_or_create_account(make_user("alice"))
        account.balance = Decimal("-1.00")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                account.save()


class DepositTests(TestCase):
    def setUp(self):
        self.account = get_or_create_account(make_user("alice"))

    def test_deposit_increases_the_balance(self):
        deposit_money(self.account, Decimal("250.00"))

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS + Decimal("250.00"))

    def test_deposit_is_recorded_in_the_ledger(self):
        deposit = deposit_money(self.account, Decimal("250.00"))

        self.assertEqual(deposit.transaction_type, Transaction.DEPOSIT)
        self.assertEqual(deposit.amount, Decimal("250.00"))
        self.assertTrue(deposit.is_credit)


class WithdrawTests(TestCase):
    def setUp(self):
        self.account = get_or_create_account(make_user("alice"))

    def test_withdraw_decreases_the_balance(self):
        withdraw_money(self.account, Decimal("400.00"))

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS - Decimal("400.00"))

    def test_cannot_withdraw_more_than_the_balance(self):
        with self.assertRaises(InsufficientFunds):
            withdraw_money(self.account, WELCOME_BONUS + Decimal("0.01"))

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS)
        self.assertFalse(
            self.account.transactions.filter(
                transaction_type=Transaction.WITHDRAW
            ).exists()
        )

    def test_can_withdraw_the_whole_balance(self):
        withdraw_money(self.account, WELCOME_BONUS)

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("0.00"))


class TransferMoneyTests(TestCase):
    def setUp(self):
        self.alice = get_or_create_account(make_user("alice"))
        self.bob = get_or_create_account(make_user("bob"))

    def test_transfer_moves_money_between_accounts(self):
        transfer_money(self.alice, self.bob, Decimal("100.00"))

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS - Decimal("100.00"))
        self.assertEqual(self.bob.balance, WELCOME_BONUS + Decimal("100.00"))

    def test_transfer_writes_both_sides_of_the_ledger(self):
        transfer_money(self.alice, self.bob, Decimal("100.00"))

        sent = self.alice.transactions.get(transaction_type=Transaction.TRANSFER_OUT)
        received = self.bob.transactions.get(transaction_type=Transaction.TRANSFER_IN)

        self.assertEqual(sent.amount, Decimal("100.00"))
        self.assertEqual(sent.description, "To bob")
        self.assertFalse(sent.is_credit)

        self.assertEqual(received.amount, Decimal("100.00"))
        self.assertEqual(received.description, "From alice")
        self.assertTrue(received.is_credit)

    def test_transfer_does_not_create_or_destroy_money(self):
        total_before = self.alice.balance + self.bob.balance

        transfer_money(self.alice, self.bob, Decimal("321.00"))

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance + self.bob.balance, total_before)

    def test_transfer_larger_than_the_balance_changes_nothing(self):
        with self.assertRaises(InsufficientFunds):
            transfer_money(self.alice, self.bob, WELCOME_BONUS + Decimal("0.01"))

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS)
        self.assertEqual(self.bob.balance, WELCOME_BONUS)
        self.assertEqual(Transaction.objects.exclude(
            transaction_type=Transaction.BONUS
        ).count(), 0)

    def test_failure_part_way_through_rolls_everything_back(self):
        """Money must never leave one account without reaching the other."""
        boom = mock.patch.object(
            Transaction.objects, "create", side_effect=RuntimeError("database is down")
        )
        with boom, self.assertRaises(RuntimeError):
            transfer_money(self.alice, self.bob, Decimal("100.00"))

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS)
        self.assertEqual(self.bob.balance, WELCOME_BONUS)

    def test_cannot_transfer_to_the_same_account(self):
        with self.assertRaises(ValueError):
            transfer_money(self.alice, self.alice, Decimal("10.00"))

    def test_repeated_transfers_never_overdraw(self):
        """Spend the whole balance in small steps, then one step too far."""
        for _ in range(10):
            transfer_money(self.alice, self.bob, Decimal("100.00"))

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, Decimal("0.00"))

        with self.assertRaises(InsufficientFunds):
            transfer_money(self.alice, self.bob, Decimal("0.01"))


class StaleReadTests(TestCase):
    """The race, reproduced without threads.

    Two requests arriving at the same time both load the account before
    either of them saves, so both are holding a copy that says $1000. The
    danger is that both copies are allowed to spend that same $1000.
    """

    def setUp(self):
        self.alice = get_or_create_account(make_user("alice"))
        self.bob = get_or_create_account(make_user("bob"))

    def test_two_stale_copies_cannot_spend_the_same_money(self):
        first = Account.objects.get(pk=self.alice.pk)
        second = Account.objects.get(pk=self.alice.pk)
        self.assertEqual(first.balance, second.balance)

        withdraw_money(first, Decimal("600.00"))

        # The second copy still says $1000 in memory, but only $400 is left.
        with self.assertRaises(InsufficientFunds):
            withdraw_money(second, Decimal("600.00"))

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, Decimal("400.00"))

    def test_two_stale_copies_cannot_transfer_the_same_money(self):
        first = Account.objects.get(pk=self.alice.pk)
        second = Account.objects.get(pk=self.alice.pk)

        transfer_money(first, self.bob, Decimal("600.00"))

        with self.assertRaises(InsufficientFunds):
            transfer_money(second, self.bob, Decimal("600.00"))

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance, Decimal("400.00"))
        self.assertEqual(self.bob.balance, Decimal("1600.00"))
        self.assertEqual(
            self.alice.balance + self.bob.balance, WELCOME_BONUS * 2
        )


class TransferViewTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.alice = get_or_create_account(self.user)
        self.bob = get_or_create_account(make_user("bob"))
        self.url = reverse("transfer")
        self.client.login(username="alice", password=PASSWORD)

    def post(self, recipient, amount):
        return self.client.post(self.url, {"recipient": recipient, "amount": amount})

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_successful_transfer_redirects_to_the_dashboard(self):
        response = self.post("bob", "100.00")

        self.assertRedirects(response, reverse("dashboard"))
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS - Decimal("100.00"))
        self.assertEqual(self.bob.balance, WELCOME_BONUS + Decimal("100.00"))

    def test_transfer_to_an_unknown_user_is_rejected(self):
        response = self.post("voldemort", "100.00")

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "recipient",
            "No user named 'voldemort' at the bank.",
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS)

    def test_transfer_to_yourself_is_rejected(self):
        response = self.post("alice", "100.00")

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "recipient",
            "You cannot send money to yourself!",
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS)

    def test_transfer_without_enough_money_is_rejected(self):
        response = self.post("bob", "99999.00")

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "amount",
            "Not enough money in your account!",
        )
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS)
        self.assertEqual(self.bob.balance, WELCOME_BONUS)

    def test_transfer_of_zero_is_rejected(self):
        response = self.post("bob", "0")

        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS)


class WithdrawViewTests(TestCase):
    def setUp(self):
        self.account = get_or_create_account(make_user("alice"))
        self.url = reverse("withdraw")
        self.client.login(username="alice", password=PASSWORD)

    def test_successful_withdrawal_redirects_to_the_dashboard(self):
        response = self.client.post(self.url, {"amount": "50.00"})

        self.assertRedirects(response, reverse("dashboard"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS - Decimal("50.00"))

    def test_withdrawing_too_much_shows_a_form_error(self):
        response = self.client.post(self.url, {"amount": "99999.00"})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "amount",
            "Not enough money in your account!",
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS)


class DepositViewTests(TestCase):
    def setUp(self):
        self.account = get_or_create_account(make_user("alice"))
        self.url = reverse("deposit")
        self.client.login(username="alice", password=PASSWORD)

    def test_successful_deposit_redirects_to_the_dashboard(self):
        response = self.client.post(self.url, {"amount": "50.00"})

        self.assertRedirects(response, reverse("dashboard"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS + Decimal("50.00"))

    def test_negative_deposit_is_rejected(self):
        response = self.client.post(self.url, {"amount": "-50.00"})

        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS)
