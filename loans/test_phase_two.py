import threading
from decimal import Decimal
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, OperationalError, connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from banking.models import Account, Transaction

from .forms import LoanApplicationForm, LoanRepaymentForm
from .models import (
    LoanAccountMissing,
    LoanAlreadyDisbursed,
    LoanApplication,
    LoanInsufficientFunds,
    LoanNotEligible,
    LoanRepaymentError,
    RepaymentExceedsRemaining,
    approve_and_disburse_loan,
    loan_disbursement_description,
    loan_repayment_description,
    make_loan_repayment,
    reject_undisbursed_loan,
)
from .tests import PASSWORD, create_application, valid_application_data


User = get_user_model()


def create_active_loan(
    user,
    *,
    requested_amount="5000.00",
    repayment_term=LoanApplication.TERM_12_MONTHS,
    amount_repaid="0.00",
):
    return create_application(
        user,
        requested_amount=requested_amount,
        repayment_term=str(repayment_term),
        status=LoanApplication.APPROVED,
        amount_repaid=amount_repaid,
        disbursed_at=timezone.now(),
    )


class LoanCalculationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="calculator")

    def test_correct_interest_rate_for_all_four_terms(self):
        expected = {
            6: Decimal("0.05"),
            12: Decimal("0.08"),
            24: Decimal("0.12"),
            36: Decimal("0.16"),
        }

        for term, rate in expected.items():
            with self.subTest(term=term):
                self.assertEqual(
                    LoanApplication.interest_rate_for_term(term),
                    rate,
                )

    def test_correct_total_repayment_for_all_four_terms(self):
        expected = {
            6: Decimal("105.00"),
            12: Decimal("108.00"),
            24: Decimal("112.00"),
            36: Decimal("116.00"),
        }

        for term, total in expected.items():
            with self.subTest(term=term):
                application = create_application(
                    self.user,
                    requested_amount="100.00",
                    repayment_term=str(term),
                )
                application.refresh_from_db()
                self.assertEqual(application.total_repayment, total)

    def test_five_thousand_over_twelve_months_is_fifty_four_hundred(self):
        application = create_application(
            self.user,
            requested_amount="5000.00",
            repayment_term="12",
        )
        application.refresh_from_db()

        self.assertEqual(application.interest_rate, Decimal("0.08"))
        self.assertEqual(application.interest_rate_percent, Decimal("8.00"))
        self.assertEqual(application.total_repayment, Decimal("5400.00"))

    def test_total_repayment_rounds_to_cents_with_decimal_math(self):
        application = create_application(
            self.user,
            requested_amount="0.10",
            repayment_term="6",
        )
        application.refresh_from_db()

        self.assertEqual(application.total_repayment, Decimal("0.11"))

    def test_phase_two_fields_start_empty_and_unfunded(self):
        application = create_application(self.user)
        application.refresh_from_db()

        self.assertEqual(application.amount_repaid, Decimal("0.00"))
        self.assertIsNone(application.disbursed_at)
        self.assertIsNone(application.fully_repaid_at)
        self.assertFalse(application.is_disbursed)
        self.assertFalse(application.is_active)
        self.assertFalse(application.is_fully_repaid)

    def test_remaining_active_and_repaid_states_are_derived(self):
        application = create_active_loan(
            self.user,
            requested_amount="100.00",
            repayment_term=6,
            amount_repaid="25.00",
        )
        application.refresh_from_db()

        self.assertEqual(application.total_repayment, Decimal("105.00"))
        self.assertEqual(application.remaining_balance, Decimal("80.00"))
        self.assertTrue(application.is_active)

        application.amount_repaid = application.total_repayment
        application.fully_repaid_at = timezone.now()
        application.save(update_fields=["amount_repaid", "fully_repaid_at"])
        self.assertEqual(application.remaining_balance, Decimal("0.00"))
        self.assertTrue(application.is_fully_repaid)
        self.assertFalse(application.is_active)

    def test_database_rejects_negative_amount_repaid(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_application(self.user, amount_repaid="-0.01")

    def test_phase_two_fields_are_not_exposed_by_application_form(self):
        form = LoanApplicationForm()

        for field_name in (
            "amount_repaid",
            "disbursed_at",
            "fully_repaid_at",
            "interest_rate",
            "total_repayment",
        ):
            with self.subTest(field=field_name):
                self.assertNotIn(field_name, form.fields)


class LoanDisbursementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="borrower")

    def setUp(self):
        self.account = Account.objects.create(
            owner=self.user,
            balance=Decimal("1000.00"),
        )
        self.application = create_application(self.user)

    def deposit_transactions(self):
        return self.account.transactions.filter(
            transaction_type=Transaction.DEPOSIT,
        )

    def assert_unfunded(self, expected_status=LoanApplication.PENDING):
        self.application.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(self.application.status, expected_status)
        self.assertIsNone(self.application.disbursed_at)
        self.assertEqual(self.account.balance, Decimal("1000.00"))
        self.assertFalse(self.deposit_transactions().exists())

    def test_pending_loan_does_not_disburse_on_save_or_page_load(self):
        self.application.save()
        self.client.force_login(self.user)
        self.client.get(reverse("loans"))

        self.assert_unfunded()

    def test_rejected_loan_cannot_be_disbursed(self):
        self.application.status = LoanApplication.REJECTED
        self.application.save(update_fields=["status"])

        with self.assertRaises(LoanNotEligible):
            approve_and_disburse_loan(self.application.pk)

        self.assert_unfunded(expected_status=LoanApplication.REJECTED)

    def test_approval_disburses_exactly_the_principal(self):
        funded = approve_and_disburse_loan(self.application.pk)

        self.account.refresh_from_db()
        self.assertEqual(funded.status, LoanApplication.APPROVED)
        self.assertIsNotNone(funded.disbursed_at)
        self.assertEqual(self.account.balance, Decimal("6000.00"))

    def test_disbursement_creates_one_described_deposit_transaction(self):
        funded = approve_and_disburse_loan(self.application.pk)

        entry = self.deposit_transactions().get()
        self.assertEqual(entry.amount, Decimal("5000.00"))
        self.assertEqual(entry.transaction_type, Transaction.DEPOSIT)
        self.assertEqual(entry.description, loan_disbursement_description(funded))
        self.assertEqual(
            entry.description,
            "Loan Disbursement — Gringotts Loans | Loan #"
            f"{funded.pk} | 12 months | 8% fixed interest | "
            "Total repayment $5,400.00",
        )

    def test_disbursement_appears_in_existing_transaction_history(self):
        approve_and_disburse_loan(self.application.pk)
        self.client.force_login(self.user)

        response = self.client.get(reverse("transaction_list"))

        self.assertContains(response, "Loan Disbursement — Gringotts Loans")
        self.assertContains(response, "+$5,000.00")

    def test_loan_cannot_be_disbursed_twice(self):
        approve_and_disburse_loan(self.application.pk)

        with self.assertRaises(LoanAlreadyDisbursed):
            approve_and_disburse_loan(self.application.pk)

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("6000.00"))
        self.assertEqual(self.deposit_transactions().count(), 1)

    def test_resave_and_status_toggling_cannot_disburse_twice(self):
        approve_and_disburse_loan(self.application.pk)
        self.application.refresh_from_db()
        self.application.save()
        self.application.status = LoanApplication.PENDING
        self.application.save(update_fields=["status"])
        self.application.status = LoanApplication.APPROVED
        self.application.save(update_fields=["status"])

        with self.assertRaises(LoanAlreadyDisbursed):
            approve_and_disburse_loan(self.application.pk)

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("6000.00"))
        self.assertEqual(self.deposit_transactions().count(), 1)

    def test_legacy_approved_loan_waits_for_explicit_disbursement(self):
        self.application.status = LoanApplication.APPROVED
        self.application.save(update_fields=["status"])
        self.application.refresh_from_db()

        self.assert_unfunded(expected_status=LoanApplication.APPROVED)

        approve_and_disburse_loan(self.application.pk)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("6000.00"))
        self.assertEqual(self.deposit_transactions().count(), 1)

    def test_missing_existing_account_rolls_back_approval(self):
        self.account.delete()

        with self.assertRaises(LoanAccountMissing):
            approve_and_disburse_loan(self.application.pk)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, LoanApplication.PENDING)
        self.assertIsNone(self.application.disbursed_at)
        self.assertFalse(Transaction.objects.exists())

    def test_transaction_failure_rolls_back_disbursement_and_status(self):
        failure = mock.patch.object(
            Transaction.objects,
            "create",
            side_effect=RuntimeError("ledger unavailable"),
        )

        with failure, self.assertRaises(RuntimeError):
            approve_and_disburse_loan(self.application.pk)

        self.assert_unfunded()

    def test_rejection_moves_no_money_and_funded_loan_cannot_be_rejected(self):
        rejected = reject_undisbursed_loan(self.application.pk)
        self.assertEqual(rejected.status, LoanApplication.REJECTED)
        self.assert_unfunded(expected_status=LoanApplication.REJECTED)

        self.application.status = LoanApplication.PENDING
        self.application.save(update_fields=["status"])
        approve_and_disburse_loan(self.application.pk)
        with self.assertRaises(LoanNotEligible):
            reject_undisbursed_loan(self.application.pk)


class LoanAdminActionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="admin-borrower")
        cls.superuser = User.objects.create_superuser(
            username="head-goblin-phase-two",
            email="phase-two@example.com",
            password=PASSWORD,
        )
        cls.view_only_staff = User.objects.create_user(
            username="view-only-goblin",
            password=PASSWORD,
            is_staff=True,
        )
        cls.view_only_staff.user_permissions.add(
            Permission.objects.get(
                codename="view_loanapplication",
                content_type__app_label="loans",
            )
        )

    def setUp(self):
        self.client.force_login(self.superuser)
        self.account = Account.objects.create(
            owner=self.user,
            balance=Decimal("1000.00"),
        )
        self.application = create_application(self.user)
        self.changelist_url = reverse("admin:loans_loanapplication_changelist")

    def run_action(self, action):
        return self.client.post(
            self.changelist_url,
            {
                "action": action,
                "_selected_action": [str(self.application.pk)],
                "index": "0",
            },
        )

    def test_admin_approval_action_disburses_the_loan(self):
        response = self.run_action("approve_and_disburse_selected")

        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(self.application.status, LoanApplication.APPROVED)
        self.assertIsNotNone(self.application.disbursed_at)
        self.assertEqual(self.account.balance, Decimal("6000.00"))

    def test_repeating_admin_approval_action_does_not_double_deposit(self):
        self.run_action("approve_and_disburse_selected")
        self.run_action("approve_and_disburse_selected")

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("6000.00"))
        self.assertEqual(
            self.account.transactions.filter(
                transaction_type=Transaction.DEPOSIT,
            ).count(),
            1,
        )

    def test_admin_rejection_action_has_no_financial_side_effect(self):
        response = self.run_action("reject_selected")

        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(self.application.status, LoanApplication.REJECTED)
        self.assertEqual(self.account.balance, Decimal("1000.00"))
        self.assertFalse(Transaction.objects.exists())

    def test_admin_displays_phase_two_values_as_read_only(self):
        change_url = reverse(
            "admin:loans_loanapplication_change",
            args=[self.application.pk],
        )

        response = self.client.get(change_url)

        self.assertContains(response, "Phase Two Financial Details")
        self.assertContains(response, "8% fixed")
        self.assertContains(response, "$5,400.00")
        admin_instance = admin.site._registry[LoanApplication]
        for field_name in (
            "status",
            "display_interest_rate",
            "display_total_repayment",
            "amount_repaid",
            "display_remaining_balance",
            "loan_state",
            "disbursed_at",
            "fully_repaid_at",
        ):
            with self.subTest(field=field_name):
                self.assertIn(field_name, admin_instance.readonly_fields)

    def test_view_only_staff_cannot_see_or_forge_financial_actions(self):
        view_client = Client()
        view_client.force_login(self.view_only_staff)

        page = view_client.get(self.changelist_url)
        forged = view_client.post(
            self.changelist_url,
            {
                "action": "approve_and_disburse_selected",
                "_selected_action": [str(self.application.pk)],
                "index": "0",
            },
        )

        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Approve and disburse selected loans")
        self.assertNotContains(page, "Reject selected undisbursed loans")
        self.assertIn(forged.status_code, (200, 302))
        self.application.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(self.application.status, LoanApplication.PENDING)
        self.assertIsNone(self.application.disbursed_at)
        self.assertEqual(self.account.balance, Decimal("1000.00"))
        self.assertFalse(Transaction.objects.exists())


class LoanRepaymentFormTests(TestCase):
    def test_positive_two_decimal_repayment_is_valid(self):
        form = LoanRepaymentForm({"amount": "500.00"})

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["amount"], Decimal("500.00"))

    def test_zero_and_negative_repayments_are_invalid(self):
        for amount in ("0.00", "-1.00"):
            with self.subTest(amount=amount):
                form = LoanRepaymentForm({"amount": amount})
                self.assertFalse(form.is_valid())

    def test_more_than_two_decimal_places_is_invalid(self):
        form = LoanRepaymentForm({"amount": "10.001"})

        self.assertFalse(form.is_valid())


class LoanRepaymentServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="repayer")
        cls.other_user = User.objects.create_user(username="intruder")

    def setUp(self):
        self.account = Account.objects.create(
            owner=self.user,
            balance=Decimal("2000.00"),
        )
        self.application = create_active_loan(self.user)

    def repayments(self):
        return self.account.transactions.filter(
            transaction_type=Transaction.WITHDRAW,
        )

    def assert_finances_unchanged(
        self,
        *,
        balance=Decimal("2000.00"),
        amount_repaid=Decimal("0.00"),
    ):
        self.account.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(self.account.balance, balance)
        self.assertEqual(self.application.amount_repaid, amount_repaid)
        self.assertFalse(self.repayments().exists())

    def test_borrower_can_make_a_valid_partial_repayment(self):
        paid = make_loan_repayment(
            self.application.pk,
            self.user,
            Decimal("500.00"),
        )

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1500.00"))
        self.assertEqual(paid.amount_repaid, Decimal("500.00"))
        self.assertEqual(paid.remaining_balance, Decimal("4900.00"))
        self.assertIsNone(paid.fully_repaid_at)

    def test_repayment_creates_one_described_withdraw_transaction(self):
        paid = make_loan_repayment(
            self.application.pk,
            self.user,
            Decimal("500.00"),
        )

        entry = self.repayments().get()
        self.assertEqual(entry.amount, Decimal("500.00"))
        self.assertEqual(entry.transaction_type, Transaction.WITHDRAW)
        self.assertEqual(
            entry.description,
            loan_repayment_description(paid, Decimal("4900.00")),
        )
        self.assertEqual(
            entry.description,
            "Loan Repayment — Gringotts Loans | Loan #"
            f"{paid.pk} | Remaining balance $4,900.00",
        )

    def test_repayment_appears_in_existing_transaction_history(self):
        make_loan_repayment(
            self.application.pk,
            self.user,
            Decimal("500.00"),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("transaction_list"))

        self.assertContains(response, "Loan Repayment — Gringotts Loans")
        self.assertContains(response, "−$500.00")

    def test_zero_and_negative_repayments_fail_without_changes(self):
        for amount in (Decimal("0.00"), Decimal("-1.00")):
            with self.subTest(amount=amount):
                with self.assertRaises(LoanRepaymentError):
                    make_loan_repayment(self.application.pk, self.user, amount)
                self.assert_finances_unchanged()

    def test_repayment_with_fractional_cents_fails(self):
        with self.assertRaises(LoanRepaymentError):
            make_loan_repayment(
                self.application.pk,
                self.user,
                Decimal("1.001"),
            )

        self.assert_finances_unchanged()

    def test_overpayment_fails_without_changes(self):
        with self.assertRaises(RepaymentExceedsRemaining):
            make_loan_repayment(
                self.application.pk,
                self.user,
                Decimal("5400.01"),
            )

        self.assert_finances_unchanged()

    def test_repayment_above_available_balance_fails_without_changes(self):
        with self.assertRaises(LoanInsufficientFunds) as error:
            make_loan_repayment(
                self.application.pk,
                self.user,
                Decimal("2000.01"),
            )

        self.assertEqual(error.exception.available, Decimal("2000.00"))
        self.assert_finances_unchanged()

    def test_pending_rejected_and_undisbursed_loans_cannot_be_repaid(self):
        scenarios = [
            (LoanApplication.PENDING, None),
            (LoanApplication.REJECTED, None),
            (LoanApplication.APPROVED, None),
        ]
        for status, disbursed_at in scenarios:
            with self.subTest(status=status):
                self.application.status = status
                self.application.disbursed_at = disbursed_at
                self.application.save(update_fields=["status", "disbursed_at"])
                with self.assertRaises(LoanRepaymentError):
                    make_loan_repayment(
                        self.application.pk,
                        self.user,
                        Decimal("10.00"),
                    )
                self.assert_finances_unchanged()

    def test_another_user_cannot_repay_the_loan(self):
        with self.assertRaises(LoanApplication.DoesNotExist):
            make_loan_repayment(
                self.application.pk,
                self.other_user,
                Decimal("100.00"),
            )

        self.assert_finances_unchanged()

    def test_exact_final_repayment_marks_loan_paid_in_full(self):
        self.account.balance = Decimal("6000.00")
        self.account.save(update_fields=["balance"])

        paid = make_loan_repayment(
            self.application.pk,
            self.user,
            Decimal("5400.00"),
        )

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("600.00"))
        self.assertEqual(paid.amount_repaid, Decimal("5400.00"))
        self.assertEqual(paid.remaining_balance, Decimal("0.00"))
        self.assertIsNotNone(paid.fully_repaid_at)
        self.assertTrue(paid.is_fully_repaid)
        self.assertFalse(paid.is_active)

    def test_further_repayment_after_full_payment_fails(self):
        self.account.balance = Decimal("6000.00")
        self.account.save(update_fields=["balance"])
        make_loan_repayment(
            self.application.pk,
            self.user,
            Decimal("5400.00"),
        )

        with self.assertRaises(LoanRepaymentError):
            make_loan_repayment(
                self.application.pk,
                self.user,
                Decimal("1.00"),
            )

        self.account.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("600.00"))
        self.assertEqual(self.application.amount_repaid, Decimal("5400.00"))
        self.assertEqual(self.repayments().count(), 1)

    def test_loan_save_failure_rolls_back_withdrawal_and_transaction(self):
        failure = mock.patch.object(
            LoanApplication,
            "save",
            side_effect=RuntimeError("loan row unavailable"),
        )

        with failure, self.assertRaises(RuntimeError):
            make_loan_repayment(
                self.application.pk,
                self.user,
                Decimal("500.00"),
            )

        self.assert_finances_unchanged()


class LoanRepaymentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="web-borrower",
            password=PASSWORD,
        )
        cls.other_user = User.objects.create_user(
            username="web-intruder",
            password=PASSWORD,
        )

    def setUp(self):
        self.account = Account.objects.create(
            owner=self.user,
            balance=Decimal("2000.00"),
        )
        self.application = create_active_loan(self.user)
        self.url = reverse("repay_loan", args=[self.application.pk])

    def test_anonymous_user_cannot_repay(self):
        response = self.client.post(self.url, {"amount": "100.00"})

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("2000.00"))
        self.assertFalse(Transaction.objects.exists())

    def test_repayment_endpoint_rejects_get(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
        self.assertFalse(Transaction.objects.exists())

    def test_repayment_post_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.post(self.url, {"amount": "100.00"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Transaction.objects.exists())

    def test_valid_repayment_uses_redirect_after_post_and_success_message(self):
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            {"amount": "500.00"},
            follow=True,
        )

        self.assertRedirects(response, reverse("loans"))
        self.assertContains(
            response,
            f"Payment successful! $500.00 was applied to Loan "
            f"#{self.application.pk}. Remaining balance: $4,900.00.",
        )

    def test_another_user_receives_404_for_repayment(self):
        self.client.force_login(self.other_user)

        response = self.client.post(self.url, {"amount": "100.00"})

        self.assertEqual(response.status_code, 404)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("2000.00"))
        self.assertFalse(Transaction.objects.exists())

    def test_client_cannot_tamper_with_financial_or_owner_values(self):
        self.client.force_login(self.user)

        self.client.post(
            self.url,
            {
                "amount": "100.00",
                "user": str(self.other_user.pk),
                "interest_rate": "0",
                "total_repayment": "1.00",
                "remaining_balance": "1.00",
                "current_balance": "999999.00",
            },
        )

        self.account.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1900.00"))
        self.assertEqual(self.application.user, self.user)
        self.assertEqual(self.application.interest_rate, Decimal("0.08"))
        self.assertEqual(self.application.total_repayment, Decimal("5400.00"))
        self.assertEqual(self.application.amount_repaid, Decimal("100.00"))

    def test_invalid_and_overpayment_messages_leave_state_unchanged(self):
        self.client.force_login(self.user)

        invalid = self.client.post(self.url, {"amount": "0.00"}, follow=True)
        excessive = self.client.post(
            self.url,
            {"amount": "5400.01"},
            follow=True,
        )

        self.assertContains(invalid, "Repayment amount must be greater than zero")
        self.assertContains(
            excessive,
            "Repayment cannot exceed the remaining balance of $5,400.00.",
        )
        self.account.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("2000.00"))
        self.assertEqual(self.application.amount_repaid, Decimal("0.00"))
        self.assertFalse(Transaction.objects.exists())

    def test_insufficient_balance_message_uses_locked_server_balance(self):
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            {"amount": "2000.01", "current_balance": "999999.00"},
            follow=True,
        )

        self.assertContains(response, "Insufficient balance")
        self.assertContains(
            response,
            "This repayment requires $2,000.01, but your available balance "
            "is $2,000.00.",
        )
        self.assertFalse(Transaction.objects.exists())


class LoanPhaseTwoPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="page-borrower",
            password=PASSWORD,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_page_shows_all_four_server_defined_lending_rates(self):
        response = self.client.get(reverse("loans"))

        self.assertContains(response, "data-term=", count=4)
        for term, rate in ((6, 5), (12, 8), (24, 12), (36, 16)):
            with self.subTest(term=term):
                self.assertContains(response, f'data-term="{term}"')
                self.assertContains(response, f'data-rate="{rate}.00"')
        self.assertContains(response, "Estimated total repayment")
        self.assertNotContains(response, 'name="interest_rate"')
        self.assertNotContains(response, 'name="total_repayment"')

    def test_active_loan_shows_financial_details_and_repayment_form(self):
        application = create_active_loan(
            self.user,
            amount_repaid="500.00",
        )

        response = self.client.get(reverse("loans"))

        self.assertContains(response, "Active")
        self.assertContains(response, "Principal")
        self.assertContains(response, "$5,000.00")
        self.assertContains(response, "8%")
        self.assertContains(response, "$5,400.00")
        self.assertContains(response, "$500.00")
        self.assertContains(response, "$4,900.00")
        self.assertContains(
            response,
            f'id="repayment-amount-{application.pk}"',
        )
        self.assertContains(
            response,
            reverse("repay_loan", args=[application.pk]),
        )

    def test_fully_repaid_loan_has_no_further_payment_form(self):
        application = create_active_loan(self.user)
        application.refresh_from_db()
        application.amount_repaid = application.total_repayment
        application.fully_repaid_at = timezone.now()
        application.save(update_fields=["amount_repaid", "fully_repaid_at"])

        response = self.client.get(reverse("loans"))

        self.assertContains(response, "Repaid")
        self.assertContains(response, "Paid in full")
        self.assertNotContains(
            response,
            reverse("repay_loan", args=[application.pk]),
        )

    def test_pending_rejected_and_legacy_approved_loans_have_no_payment_form(self):
        create_application(self.user, purpose="Pending phase two")
        create_application(
            self.user,
            purpose="Rejected phase two",
            status=LoanApplication.REJECTED,
        )
        legacy = create_application(
            self.user,
            purpose="Legacy approved phase one",
            status=LoanApplication.APPROVED,
        )

        response = self.client.get(reverse("loans"))

        self.assertContains(response, "Pending")
        self.assertContains(response, "Rejected")
        self.assertContains(response, "Approved — Awaiting Funding")
        self.assertNotContains(
            response,
            reverse("repay_loan", args=[legacy.pk]),
        )
        self.assertNotContains(response, 'name="amount"')

    def test_loan_history_remains_responsive(self):
        create_active_loan(self.user)

        response = self.client.get(reverse("loans"))

        self.assertContains(response, 'class="table-responsive"')
        self.assertContains(response, "loans-loan-metrics")


class LoanConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(username="concurrent-borrower")

    def test_simultaneous_disbursement_cannot_deposit_twice(self):
        account = Account.objects.create(
            owner=self.user,
            balance=Decimal("1000.00"),
        )
        application = create_application(self.user)
        barrier = threading.Barrier(2)
        outcomes = []

        def fund():
            barrier.wait()
            try:
                approve_and_disburse_loan(application.pk)
                outcomes.append("ok")
            except (LoanAlreadyDisbursed, OperationalError) as exc:
                outcomes.append(type(exc).__name__)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=fund) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        for thread in threads:
            self.assertFalse(thread.is_alive(), "disbursement thread deadlocked")
        account.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(account.balance, Decimal("6000.00"))
        self.assertIsNotNone(application.disbursed_at)
        self.assertEqual(
            account.transactions.filter(
                transaction_type=Transaction.DEPOSIT,
            ).count(),
            1,
        )
        self.assertEqual(outcomes.count("ok"), 1)

    def test_simultaneous_repayments_cannot_overpay_or_overdraw(self):
        account = Account.objects.create(
            owner=self.user,
            balance=Decimal("1000.00"),
        )
        application = create_active_loan(
            self.user,
            requested_amount="1000.00",
            repayment_term=6,
        )
        barrier = threading.Barrier(2)
        outcomes = []

        def repay():
            barrier.wait()
            try:
                make_loan_repayment(
                    application.pk,
                    self.user,
                    Decimal("600.00"),
                )
                outcomes.append("ok")
            except (
                LoanInsufficientFunds,
                RepaymentExceedsRemaining,
                OperationalError,
            ) as exc:
                outcomes.append(type(exc).__name__)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=repay) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        for thread in threads:
            self.assertFalse(thread.is_alive(), "repayment thread deadlocked")
        account.refresh_from_db()
        application.refresh_from_db()
        repayments = account.transactions.filter(
            transaction_type=Transaction.WITHDRAW,
        )
        self.assertEqual(outcomes.count("ok"), 1)
        self.assertEqual(account.balance, Decimal("400.00"))
        self.assertEqual(application.amount_repaid, Decimal("600.00"))
        self.assertEqual(application.remaining_balance, Decimal("450.00"))
        self.assertEqual(repayments.count(), 1)
