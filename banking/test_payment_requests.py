# banking/test_payment_requests.py
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import (
    WELCOME_BONUS,
    AlreadyResolved,
    InsufficientFunds,
    PaymentRequest,
    Transaction,
    accept_payment_request,
    cancel_payment_request,
    decline_payment_request,
    get_or_create_account,
    request_money,
)
from .tests import PASSWORD, make_user


class PaymentRequestSetup(TestCase):
    def setUp(self):
        # alice asks bob for money throughout.
        self.alice = get_or_create_account(make_user("alice"))
        self.bob = get_or_create_account(make_user("bob"))
        self.mallory = get_or_create_account(make_user("mallory"))
        self.payment_request = request_money(
            requester=self.alice,
            payer=self.bob,
            amount=Decimal("250.00"),
            note="Dinner",
        )


class RequestMoneyTests(PaymentRequestSetup):
    def test_a_new_request_starts_pending_and_moves_no_money(self):
        self.assertEqual(self.payment_request.status, PaymentRequest.PENDING)
        self.assertTrue(self.payment_request.is_pending)
        self.assertIsNone(self.payment_request.resolved_at)

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS)
        self.assertEqual(self.bob.balance, WELCOME_BONUS)
        self.assertEqual(Transaction.objects.exclude(
            transaction_type=Transaction.BONUS
        ).count(), 0)

    def test_cannot_request_money_from_yourself(self):
        with self.assertRaises(ValueError):
            request_money(self.alice, self.alice, Decimal("10.00"))

    def test_database_rejects_a_request_to_yourself(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PaymentRequest.objects.create(
                    requester=self.alice, payer=self.alice, amount=Decimal("10.00")
                )

    def test_database_rejects_a_non_positive_amount(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PaymentRequest.objects.create(
                    requester=self.alice, payer=self.bob, amount=Decimal("0.00")
                )


class AcceptTests(PaymentRequestSetup):
    def test_accepting_moves_the_money_and_marks_it_accepted(self):
        accepted = accept_payment_request(self.payment_request, self.bob)

        self.assertEqual(accepted.status, PaymentRequest.ACCEPTED)
        self.assertIsNotNone(accepted.resolved_at)

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS + Decimal("250.00"))
        self.assertEqual(self.bob.balance, WELCOME_BONUS - Decimal("250.00"))

    def test_accepting_writes_both_sides_of_the_ledger(self):
        accept_payment_request(self.payment_request, self.bob)

        sent = self.bob.transactions.get(transaction_type=Transaction.TRANSFER_OUT)
        received = self.alice.transactions.get(
            transaction_type=Transaction.TRANSFER_IN
        )
        self.assertEqual(sent.amount, Decimal("250.00"))
        self.assertEqual(received.amount, Decimal("250.00"))

    def test_only_the_payer_can_accept(self):
        for account in (self.alice, self.mallory):
            with self.subTest(account=account.owner.username):
                with self.assertRaises(PermissionDenied):
                    accept_payment_request(self.payment_request, account)

        self.payment_request.refresh_from_db()
        self.assertTrue(self.payment_request.is_pending)

    def test_accepting_twice_only_pays_once(self):
        """The double-click case: the second attempt must be refused."""
        accept_payment_request(self.payment_request, self.bob)

        with self.assertRaises(AlreadyResolved):
            accept_payment_request(self.payment_request, self.bob)

        self.bob.refresh_from_db()
        self.assertEqual(self.bob.balance, WELCOME_BONUS - Decimal("250.00"))
        self.assertEqual(
            self.bob.transactions.filter(
                transaction_type=Transaction.TRANSFER_OUT
            ).count(),
            1,
        )

    def test_a_request_you_cannot_afford_stays_pending(self):
        expensive = request_money(self.alice, self.bob, WELCOME_BONUS * 2)

        with self.assertRaises(InsufficientFunds):
            accept_payment_request(expensive, self.bob)

        expensive.refresh_from_db()
        self.assertTrue(expensive.is_pending)
        self.assertIsNone(expensive.resolved_at)

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS)
        self.assertEqual(self.bob.balance, WELCOME_BONUS)


class DeclineAndCancelTests(PaymentRequestSetup):
    def test_payer_can_decline(self):
        declined = decline_payment_request(self.payment_request, self.bob)

        self.assertEqual(declined.status, PaymentRequest.DECLINED)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.balance, WELCOME_BONUS)

    def test_requester_cannot_decline_their_own_request(self):
        with self.assertRaises(PermissionDenied):
            decline_payment_request(self.payment_request, self.alice)

    def test_requester_can_cancel(self):
        cancelled = cancel_payment_request(self.payment_request, self.alice)

        self.assertEqual(cancelled.status, PaymentRequest.CANCELLED)

    def test_payer_cannot_cancel_someone_elses_request(self):
        with self.assertRaises(PermissionDenied):
            cancel_payment_request(self.payment_request, self.bob)

    def test_a_declined_request_cannot_then_be_accepted(self):
        decline_payment_request(self.payment_request, self.bob)

        with self.assertRaises(AlreadyResolved):
            accept_payment_request(self.payment_request, self.bob)

        self.bob.refresh_from_db()
        self.assertEqual(self.bob.balance, WELCOME_BONUS)

    def test_a_cancelled_request_cannot_then_be_paid(self):
        cancel_payment_request(self.payment_request, self.alice)

        with self.assertRaises(AlreadyResolved):
            accept_payment_request(self.payment_request, self.bob)

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, WELCOME_BONUS)


class PaymentRequestViewTests(PaymentRequestSetup):
    def resolve_url(self, action, payment_request=None):
        return reverse(
            "resolve_payment_request",
            args=[(payment_request or self.payment_request).pk, action],
        )

    def login(self, username):
        self.client.login(username=username, password=PASSWORD)

    def test_login_is_required_to_see_requests(self):
        response = self.client.get(reverse("payment_requests"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_list_separates_incoming_from_outgoing(self):
        self.login("bob")
        response = self.client.get(reverse("payment_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.payment_request, list(response.context["incoming"]))
        self.assertEqual(list(response.context["outgoing"]), [])

    def test_creating_a_request_through_the_form(self):
        self.login("alice")
        response = self.client.post(
            reverse("request_payment"),
            {"payer": "bob", "amount": "40.00", "note": "Taxi"},
        )

        self.assertRedirects(response, reverse("payment_requests"))
        created = PaymentRequest.objects.get(amount=Decimal("40.00"))
        self.assertEqual(created.requester, self.alice)
        self.assertEqual(created.payer, self.bob)
        self.assertEqual(created.note, "Taxi")

    def test_cannot_request_from_yourself_through_the_form(self):
        self.login("alice")
        response = self.client.post(
            reverse("request_payment"),
            {"payer": "alice", "amount": "40.00", "note": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "payer",
            "You cannot request money from yourself!",
        )

    def test_cannot_request_from_an_unknown_user(self):
        self.login("alice")
        response = self.client.post(
            reverse("request_payment"),
            {"payer": "voldemort", "amount": "40.00", "note": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "payer",
            "No user named 'voldemort' at the bank.",
        )

    def test_payer_can_accept_through_the_view(self):
        self.login("bob")
        response = self.client.post(self.resolve_url("accept"))

        self.assertRedirects(response, reverse("payment_requests"))
        self.payment_request.refresh_from_db()
        self.assertEqual(self.payment_request.status, PaymentRequest.ACCEPTED)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.balance, WELCOME_BONUS - Decimal("250.00"))

    def test_a_stranger_gets_403_and_the_request_is_untouched(self):
        self.login("mallory")
        response = self.client.post(self.resolve_url("accept"))

        self.assertEqual(response.status_code, 403)
        self.payment_request.refresh_from_db()
        self.assertTrue(self.payment_request.is_pending)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.balance, WELCOME_BONUS)

    def test_get_requests_do_not_change_anything(self):
        """A prefetched or bookmarked link must never move money."""
        self.login("bob")
        response = self.client.get(self.resolve_url("accept"))

        self.assertEqual(response.status_code, 405)
        self.payment_request.refresh_from_db()
        self.assertTrue(self.payment_request.is_pending)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.balance, WELCOME_BONUS)

    def test_an_unknown_action_is_a_404(self):
        self.login("bob")
        response = self.client.post(self.resolve_url("banana"))

        self.assertEqual(response.status_code, 404)
        self.payment_request.refresh_from_db()
        self.assertTrue(self.payment_request.is_pending)

    def test_accepting_without_enough_money_shows_an_error(self):
        expensive = request_money(self.alice, self.bob, WELCOME_BONUS * 2)
        self.login("bob")
        response = self.client.post(
            self.resolve_url("accept", expensive), follow=True
        )

        self.assertContains(response, "Not enough money in your account!")
        expensive.refresh_from_db()
        self.assertTrue(expensive.is_pending)

    def test_dashboard_counts_pending_incoming_requests(self):
        self.login("bob")
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.context["pending_requests"], 1)
        self.assertContains(response, "payment request")

    def test_resolved_requests_are_not_counted_on_the_dashboard(self):
        decline_payment_request(self.payment_request, self.bob)
        self.login("bob")
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.context["pending_requests"], 0)
