from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Account, Transaction, get_or_create_account


User = get_user_model()
PASSWORD = "testpass123"
FIXED_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=datetime_timezone.utc)


def create_transaction_at(account, when, transaction_type, amount, description=""):
    """Create a ledger row at a controlled time despite auto_now_add."""
    entry = Transaction.objects.create(
        account=account,
        transaction_type=transaction_type,
        amount=Decimal(amount),
        description=description,
    )
    Transaction.objects.filter(pk=entry.pk).update(timestamp=when)
    return entry


@override_settings(TIME_ZONE="UTC")
class MonthlySpendingDashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            password=PASSWORD,
        )
        self.account = get_or_create_account(self.user)
        self.client.force_login(self.user)
        self.url = reverse("dashboard")

    def get_dashboard(self, now=FIXED_NOW):
        with mock.patch("banking.views.timezone.now", return_value=now):
            return self.client.get(self.url)

    def add_entry(
        self,
        transaction_type,
        amount,
        description="",
        when=FIXED_NOW,
        account=None,
    ):
        return create_transaction_at(
            account or self.account,
            when,
            transaction_type,
            amount,
            description,
        )

    @staticmethod
    def breakdown(response):
        return {
            row["label"]: row["amount"]
            for row in response.context["monthly_spending_breakdown"]
        }

    def test_authenticated_dashboard_still_returns_200(self):
        response = self.get_dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "banking/dashboard.html")
        self.assertContains(response, "Monthly Spending")

    def test_current_month_range_includes_start_and_current_instant(self):
        month_start = FIXED_NOW.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        self.add_entry(Transaction.WITHDRAW, "25.00", when=month_start)
        self.add_entry(Transaction.TRANSFER_OUT, "30.00", when=FIXED_NOW)

        response = self.get_dashboard()

        self.assertEqual(
            response.context["monthly_spending_total"],
            Decimal("55.00"),
        )

    def test_previous_month_and_future_transactions_are_excluded(self):
        month_start = FIXED_NOW.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        self.add_entry(
            Transaction.WITHDRAW,
            "400.00",
            when=month_start - timedelta(microseconds=1),
        )
        self.add_entry(
            Transaction.TRANSFER_OUT,
            "500.00",
            when=FIXED_NOW + timedelta(microseconds=1),
        )

        response = self.get_dashboard()

        self.assertEqual(
            response.context["monthly_spending_total"],
            Decimal("0.00"),
        )

    def test_outgoing_transactions_are_classified_and_totalled_once(self):
        self.add_entry(
            Transaction.WITHDRAW,
            "250.00",
            "Purchase — Quality Quidditch Supplies: Nimbus 2000 · "
            "50 Galleons · Gringotts Privileges",
        )
        self.add_entry(
            Transaction.WITHDRAW,
            "500.00",
            "Loan Repayment — Gringotts Loans | Loan #7 | "
            "Remaining balance $600.00",
        )
        self.add_entry(Transaction.TRANSFER_OUT, "300.00", "To bob")
        self.add_entry(Transaction.WITHDRAW, "100.00")

        response = self.get_dashboard()
        breakdown = self.breakdown(response)

        self.assertEqual(
            list(breakdown),
            [
                "Gringotts Privileges",
                "Loan Repayments",
                "Transfers",
                "Withdrawals",
            ],
        )
        self.assertEqual(
            breakdown,
            {
                "Gringotts Privileges": Decimal("250.00"),
                "Loan Repayments": Decimal("500.00"),
                "Transfers": Decimal("300.00"),
                "Withdrawals": Decimal("100.00"),
            },
        )
        self.assertEqual(
            response.context["monthly_spending_total"],
            Decimal("1150.00"),
        )
        self.assertEqual(sum(breakdown.values()), Decimal("1150.00"))
        self.assertEqual(
            response.context["monthly_spending_chart_data"]["values"],
            ["250.00", "500.00", "300.00", "100.00"],
        )

    def test_credit_transactions_are_excluded_even_with_special_descriptions(self):
        reward_description = (
            "Purchase — Honeydukes: Sweet Box · "
            "6 Galleons · Gringotts Privileges"
        )
        loan_description = (
            "Loan Repayment — Gringotts Loans | Loan #2 | "
            "Remaining balance $0.00"
        )
        self.add_entry(Transaction.DEPOSIT, "250.00", reward_description)
        self.add_entry(Transaction.TRANSFER_IN, "500.00", loan_description)
        self.add_entry(Transaction.BONUS, "1000.00", reward_description)

        response = self.get_dashboard()

        self.assertEqual(
            response.context["monthly_spending_total"],
            Decimal("0.00"),
        )
        self.assertEqual(response.context["monthly_spending_breakdown"], [])

    def test_type_and_description_priority_prevent_overlapping_categories(self):
        overlapping_description = (
            "Purchase — Loan Repayment — Gringotts Loans | Loan #9 · "
            "Gringotts Privileges"
        )
        self.add_entry(Transaction.WITHDRAW, "40.00", overlapping_description)
        self.add_entry(Transaction.TRANSFER_OUT, "60.00", overlapping_description)

        response = self.get_dashboard()

        self.assertEqual(
            self.breakdown(response),
            {
                "Gringotts Privileges": Decimal("40.00"),
                "Transfers": Decimal("60.00"),
            },
        )
        self.assertEqual(
            response.context["monthly_spending_total"],
            Decimal("100.00"),
        )

    def test_another_users_transactions_are_not_included(self):
        other_user = User.objects.create_user(username="bob", password=PASSWORD)
        other_account = get_or_create_account(other_user)
        self.add_entry(Transaction.WITHDRAW, "999.00", account=other_account)
        self.add_entry(Transaction.WITHDRAW, "15.00")

        response = self.get_dashboard()

        self.assertEqual(
            response.context["monthly_spending_total"],
            Decimal("15.00"),
        )
        self.assertEqual(
            self.breakdown(response),
            {"Withdrawals": Decimal("15.00")},
        )

    def test_empty_month_shows_empty_state_without_chart_assets(self):
        response = self.get_dashboard()

        self.assertContains(response, "No spending this month.")
        self.assertContains(
            response,
            "Your vault has remained suspiciously undisturbed.",
        )
        self.assertNotContains(response, 'id="monthly-spending-chart"')
        self.assertNotContains(response, "chart.umd.min.js")

    def test_money_uses_grouping_and_exactly_two_decimal_places(self):
        self.add_entry(Transaction.WITHDRAW, "12345.67")

        response = self.get_dashboard()

        self.assertContains(response, "$12,345.67", count=2)

    def test_non_positive_ledger_rows_are_not_counted_as_spending(self):
        self.add_entry(Transaction.WITHDRAW, "0.00")
        self.add_entry(Transaction.TRANSFER_OUT, "-10.00")

        response = self.get_dashboard()

        self.assertEqual(
            response.context["monthly_spending_total"],
            Decimal("0.00"),
        )

    def test_chart_js_and_dashboard_css_are_scoped_to_the_dashboard(self):
        self.add_entry(Transaction.WITHDRAW, "25.00")

        dashboard_response = self.get_dashboard()
        history_response = self.client.get(reverse("transaction_list"))

        self.assertContains(dashboard_response, "banking/css/dashboard.css")
        self.assertContains(dashboard_response, "chart.js@4.5.1")
        self.assertContains(
            dashboard_response,
            'aria-label="Donut chart of monthly spending.',
        )
        self.assertNotContains(history_response, "chart.js@4.5.1")
        self.assertNotContains(history_response, "banking/css/dashboard.css")

    def test_january_dashboard_excludes_the_previous_year(self):
        january_now = datetime(
            2026,
            1,
            5,
            12,
            0,
            tzinfo=datetime_timezone.utc,
        )
        self.add_entry(
            Transaction.WITHDRAW,
            "70.00",
            when=datetime(
                2025,
                12,
                31,
                23,
                59,
                tzinfo=datetime_timezone.utc,
            ),
        )
        self.add_entry(Transaction.WITHDRAW, "30.00", when=january_now)

        response = self.get_dashboard(now=january_now)

        self.assertEqual(
            response.context["monthly_spending_total"],
            Decimal("30.00"),
        )
        self.assertContains(response, "January 2026")


class DashboardAuthenticationTests(TestCase):
    def test_anonymous_behavior_is_unchanged_and_does_not_create_an_account(self):
        user = User.objects.create_user(username="anonymous-test", password=PASSWORD)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('login')}?next={reverse('dashboard')}")
        self.assertFalse(Account.objects.filter(owner=user).exists())
