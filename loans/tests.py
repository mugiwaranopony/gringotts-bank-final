from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from banking.models import Account, Transaction

from .forms import LoanApplicationForm
from .models import LoanApplication


User = get_user_model()
PASSWORD = "testpass123"


def valid_application_data(**overrides):
    data = {
        "requested_amount": "5000.00",
        "repayment_term": str(LoanApplication.TERM_12_MONTHS),
        "purpose": "Personal / Nimbus-related financial decisions",
        "employment": "Ministry archivist",
        "monthly_income": "3200.00",
        "previous_default": "NO",
        "owes_goblins": "UNPROVEN",
        "dragons_on_property": "0",
        "unauthorized_vault_entry": "NO",
        "financial_decision_rating": "QUESTIONABLE",
        "income_loss_repayment": "SAVINGS",
        "collateral": "GOLD",
    }
    data.update(overrides)
    return data


def create_application(user, **overrides):
    data = valid_application_data(**overrides)
    return LoanApplication.objects.create(user=user, **data)


class LoanApplicationFormTests(TestCase):
    def test_form_exposes_only_fields_the_customer_can_answer(self):
        form = LoanApplicationForm()

        self.assertEqual(
            list(form.fields),
            [
                "requested_amount",
                "repayment_term",
                "purpose",
                "employment",
                "monthly_income",
                "previous_default",
                "owes_goblins",
                "dragons_on_property",
                "unauthorized_vault_entry",
                "financial_decision_rating",
                "income_loss_repayment",
                "collateral",
            ],
        )
        self.assertNotIn("user", form.fields)
        self.assertNotIn("status", form.fields)

    def test_requested_amount_must_be_greater_than_zero(self):
        form = LoanApplicationForm(
            data=valid_application_data(requested_amount="0.00")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("requested_amount", form.errors)

    def test_monthly_income_cannot_be_negative(self):
        form = LoanApplicationForm(
            data=valid_application_data(monthly_income="-0.01")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("monthly_income", form.errors)


class LoanApplicationConstraintTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice")

    def test_database_rejects_a_non_positive_requested_amount(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_application(self.user, requested_amount="0.00")

    def test_database_rejects_negative_monthly_income(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_application(self.user, monthly_income="-0.01")


class LoanPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            password=PASSWORD,
        )
        self.other_user = User.objects.create_user(
            username="bob",
            password=PASSWORD,
        )
        self.url = reverse("loans")

    def login(self):
        self.client.login(username=self.user.username, password=PASSWORD)

    def test_anonymous_users_are_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.assertIn("next=/loans/", response.url)

    def test_authenticated_users_can_open_the_loans_page(self):
        self.login()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "loans/loan_application.html")
        self.assertTemplateUsed(response, "base.html")
        self.assertContains(response, "Gringotts Loans")
        self.assertContains(response, "My Loan Applications")

    def test_submission_requires_a_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.post(self.url, valid_application_data())

        self.assertEqual(response.status_code, 403)
        self.assertFalse(LoanApplication.objects.exists())

    def test_valid_submission_is_owned_by_user_and_starts_pending(self):
        self.login()

        response = self.client.post(self.url, valid_application_data())

        self.assertRedirects(
            response,
            self.url,
            fetch_redirect_response=False,
        )
        application = LoanApplication.objects.get()
        self.assertEqual(application.user, self.user)
        self.assertEqual(application.status, LoanApplication.PENDING)

    def test_posted_user_and_approved_status_are_ignored(self):
        self.login()
        data = valid_application_data(
            user=str(self.other_user.pk),
            status=LoanApplication.APPROVED,
        )

        self.client.post(self.url, data)

        application = LoanApplication.objects.get()
        self.assertEqual(application.user, self.user)
        self.assertEqual(application.status, LoanApplication.PENDING)

    def test_all_questionnaire_answers_are_saved(self):
        self.login()
        data = valid_application_data(
            previous_default="AZKABAN",
            owes_goblins="YES",
            dragons_on_property="DEFINE_RESIDE",
            unauthorized_vault_entry="LEGAL_ADVICE",
            financial_decision_rating="NIMBUS",
            income_loss_repayment="POLYJUICE",
            collateral="WAIT_WHAT",
        )

        self.client.post(self.url, data)

        application = LoanApplication.objects.get()
        self.assertEqual(application.previous_default, "AZKABAN")
        self.assertEqual(application.owes_goblins, "YES")
        self.assertEqual(application.dragons_on_property, "DEFINE_RESIDE")
        self.assertEqual(
            application.unauthorized_vault_entry,
            "LEGAL_ADVICE",
        )
        self.assertEqual(application.financial_decision_rating, "NIMBUS")
        self.assertEqual(application.income_loss_repayment, "POLYJUICE")
        self.assertEqual(application.collateral, "WAIT_WHAT")

    def test_users_only_see_their_own_applications(self):
        create_application(self.user, purpose="Alice visible application")
        create_application(self.other_user, purpose="Bob private application")
        self.login()

        response = self.client.get(self.url)

        self.assertQuerySetEqual(
            response.context["applications"],
            LoanApplication.objects.filter(user=self.user),
        )
        self.assertContains(response, "Alice visible application")
        self.assertNotContains(response, "Bob private application")

    def test_money_uses_grouping_and_exactly_two_decimal_places(self):
        create_application(self.user, requested_amount="5000.00")
        self.login()

        response = self.client.get(self.url)

        self.assertContains(response, "$5,000.00")

    def test_first_born_choice_has_the_requested_legal_warning(self):
        self.login()

        response = self.client.get(self.url)

        self.assertContains(response, 'value="FIRST_BORN"')
        self.assertContains(response, 'id="first-born-warning"')
        self.assertContains(response, "Please disregard that option.")
        self.assertContains(response, "revised in 1473.")

    def test_success_and_preliminary_assessment_messages_are_shown(self):
        self.login()

        response = self.client.post(
            self.url,
            valid_application_data(),
            follow=True,
        )
        messages = [str(message) for message in get_messages(response.wsgi_request)]

        self.assertIn(
            "Your Gringotts loan application has been submitted for review.",
            messages,
        )
        self.assertIn(
            "Preliminary Goblin Risk Assessment: "
            "Concerning, but not unusually so.",
            messages,
        )
        self.assertEqual(
            LoanApplication.objects.get().status,
            LoanApplication.PENDING,
        )

    def test_pending_application_submission_does_not_move_money(self):
        self.login()
        self.assertFalse(Account.objects.filter(owner=self.user).exists())

        self.client.get(self.url)
        self.client.post(self.url, valid_application_data())

        self.assertFalse(Account.objects.filter(owner=self.user).exists())
        self.assertFalse(Transaction.objects.exists())


class LoanApplicationAdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice")
        self.superuser = User.objects.create_superuser(
            username="head-goblin",
            email="goblin@example.com",
            password=PASSWORD,
        )
        self.client.force_login(self.superuser)
        self.application = create_application(
            self.user,
            previous_default="AZKABAN",
            owes_goblins="UNPROVEN",
            dragons_on_property="DEFINE_RESIDE",
            unauthorized_vault_entry="LEGAL_ADVICE",
            financial_decision_rating="NIMBUS",
            income_loss_repayment="POLYJUICE",
            collateral="FIRST_BORN",
        )
        self.change_url = reverse(
            "admin:loans_loanapplication_change",
            args=[self.application.pk],
        )

    def test_application_is_registered_and_answers_are_visible_in_admin(self):
        self.assertIn(LoanApplication, admin.site._registry)

        response = self.client.get(self.change_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.username)
        self.assertContains(response, "I prefer not to discuss Azkaban")
        self.assertContains(response, "Not any that can prove it")
        self.assertContains(response, 'Define &quot;reside&quot;')
        self.assertContains(
            response,
            "My solicitor has advised me not to answer this question",
        )
        self.assertContains(response, "First-born child")

    def test_ordinary_admin_save_cannot_approve_or_move_money(self):
        account = Account.objects.create(
            owner=self.user,
            balance=Decimal("4321.00"),
        )
        existing_transaction = Transaction.objects.create(
            account=account,
            transaction_type=Transaction.DEPOSIT,
            amount=Decimal("25.00"),
            description="Existing ledger entry",
        )
        transaction_ids = list(Transaction.objects.values_list("pk", flat=True))

        response = self.client.post(
            self.change_url,
            {"status": LoanApplication.APPROVED, "_save": "Save"},
        )

        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        account.refresh_from_db()
        self.assertEqual(self.application.status, LoanApplication.PENDING)
        self.assertIsNone(self.application.disbursed_at)
        self.assertEqual(account.balance, Decimal("4321.00"))
        self.assertEqual(
            list(Transaction.objects.values_list("pk", flat=True)),
            transaction_ids,
        )
        self.assertTrue(Transaction.objects.filter(pk=existing_transaction.pk).exists())

    def test_ordinary_admin_save_does_not_create_a_missing_bank_account(self):
        self.assertFalse(Account.objects.filter(owner=self.user).exists())

        response = self.client.post(
            self.change_url,
            {"status": LoanApplication.APPROVED, "_save": "Save"},
        )

        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, LoanApplication.PENDING)
        self.assertIsNone(self.application.disbursed_at)
        self.assertFalse(Account.objects.filter(owner=self.user).exists())
        self.assertFalse(Transaction.objects.exists())
