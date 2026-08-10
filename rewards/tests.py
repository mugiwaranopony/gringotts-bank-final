from decimal import Decimal
from unittest import mock
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from banking.models import (
    WELCOME_BONUS,
    InsufficientFunds,
    Transaction,
    get_or_create_account,
    withdraw_money,
)

from .views import (
    GALLEON_TO_USD_RATE,
    REWARD_PRODUCTS,
    galleons_to_usd,
)

User = get_user_model()
PASSWORD = "testpass123"


class RewardsPageTests(TestCase):
    def test_rewards_page_is_public_and_uses_the_expected_templates(self):
        response = self.client.get(reverse("rewards"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "rewards/rewards.html")
        self.assertTemplateUsed(response, "base.html")

    def test_rewards_page_contains_exactly_twelve_products(self):
        response = self.client.get(reverse("rewards"))

        self.assertEqual(len(response.context["products"]), 12)
        self.assertContains(response, "Gringotts Exclusive", count=12)
        self.assertContains(response, "data-bs-toggle=\"modal\"", count=12)

    def test_rewards_grid_has_three_desktop_columns(self):
        response = self.client.get(reverse("rewards"))

        self.assertContains(
            response,
            "class=\"col-12 col-md-6 col-lg-4\"",
            count=12,
        )
        self.assertContains(
            response,
            "class=\"card rewards-card h-100\"",
            count=12,
        )

    def test_every_usd_price_uses_the_single_exchange_rate(self):
        response = self.client.get(reverse("rewards"))

        self.assertEqual(GALLEON_TO_USD_RATE, Decimal("5.00"))
        for product in response.context["products"]:
            with self.subTest(product=product["product_id"]):
                self.assertEqual(
                    product["usd_price"],
                    Decimal(product["galleon_price"]) * GALLEON_TO_USD_RATE,
                )

    def test_catalogue_contains_the_requested_products(self):
        products_by_id = {product["product_id"]: product for product in REWARD_PRODUCTS}

        self.assertEqual(products_by_id["dinner-for-two"]["galleon_price"], 12)
        self.assertEqual(products_by_id["nimbus-2000"]["galleon_price"], 50)
        self.assertEqual(
            products_by_id["first-class-journey-package"]["galleon_price"],
            15,
        )

    def test_catalogue_defines_all_twelve_individual_image_filenames(self):
        expected_images = {
            "dinner-for-two": "rewards/images/three-broomsticks.jpg",
            "honeydukes-sweet-box": "rewards/images/honeydukes.jpg",
            "nimbus-2000": "rewards/images/nimbus-2000.jpg",
            "deluxe-joke-box": "rewards/images/weasleys-wizard-wheezes.jpg",
            "wizarding-book-collection": "rewards/images/flourish-and-blotts.jpg",
            "premium-wizard-robes": "rewards/images/madam-malkins.jpg",
            "premium-wand-care-package": "rewards/images/ollivanders.jpg",
            "magical-pet-care-package": "rewards/images/magical-menagerie.jpg",
            "one-night-stay": "rewards/images/leaky-cauldron.jpg",
            "premium-owl-care-kit": "rewards/images/eylops-owl-emporium.jpg",
            "mystery-magical-curio": "rewards/images/borgin-and-burkes.jpg",
            "first-class-journey-package": "rewards/images/hogwarts-express.jpg",
        }
        actual_images = {
            product["product_id"]: product["image_filename"]
            for product in REWARD_PRODUCTS
        }

        self.assertEqual(actual_images, expected_images)


class RewardPurchaseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            password=PASSWORD,
        )
        self.account = get_or_create_account(self.user)
        self.purchase_url = reverse("purchase_reward", args=["nimbus-2000"])

    def login(self):
        self.client.login(username="alice", password=PASSWORD)

    def withdrawals(self):
        return self.account.transactions.filter(
            transaction_type=Transaction.WITHDRAW
        )

    def test_anonymous_user_cannot_complete_a_purchase(self):
        response = self.client.post(self.purchase_url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS)
        self.assertFalse(self.withdrawals().exists())

    def test_purchase_endpoint_rejects_get_requests(self):
        self.login()

        response = self.client.get(self.purchase_url)

        self.assertEqual(response.status_code, 405)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS)
        self.assertFalse(self.withdrawals().exists())

    def test_nimbus_purchase_deducts_the_server_calculated_usd_amount(self):
        self.login()

        response = self.client.post(self.purchase_url)

        self.assertRedirects(response, reverse("rewards"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("750.00"))

    def test_successful_purchase_creates_existing_history_transaction(self):
        self.login()

        self.client.post(self.purchase_url)

        purchase = self.withdrawals().get()
        self.assertEqual(purchase.amount, Decimal("250.00"))
        self.assertEqual(purchase.transaction_type, Transaction.WITHDRAW)
        self.assertIn("Purchase", purchase.description)
        self.assertIn("Quality Quidditch Supplies", purchase.description)
        self.assertIn("Nimbus 2000", purchase.description)
        self.assertIn("50 Galleons", purchase.description)
        self.assertIn("Gringotts Privileges", purchase.description)

        history = self.client.get(reverse("transaction_list"))
        self.assertContains(history, "Quality Quidditch Supplies")
        self.assertContains(history, "50 Galleons")
        self.assertContains(history, "$250.00")

    def test_successful_purchase_uses_redirect_after_post_and_a_message(self):
        self.login()

        response = self.client.post(self.purchase_url)

        self.assertRedirects(
            response,
            reverse("rewards"),
            fetch_redirect_response=False,
        )
        page = self.client.get(reverse("rewards"))
        self.assertContains(
            page,
            "Purchase successful! Nimbus 2000 was purchased for "
            "50 Galleons ($250.00).",
        )

    def test_insufficient_balance_changes_nothing_and_shows_an_error(self):
        self.account.balance = Decimal("100.00")
        self.account.save(update_fields=["balance"])
        self.login()

        response = self.client.post(self.purchase_url)

        self.assertRedirects(
            response,
            reverse("rewards"),
            fetch_redirect_response=False,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("100.00"))
        self.assertFalse(self.withdrawals().exists())

        page = self.client.get(reverse("rewards"))
        self.assertContains(page, "Insufficient balance")
        self.assertContains(
            page,
            "This purchase requires $250.00, but your available balance is "
            "$100.00.",
        )

    def test_submitted_price_fields_cannot_change_the_real_cost(self):
        self.login()

        self.client.post(
            self.purchase_url,
            {
                "galleon_price": "1",
                "usd_price": "0.01",
                "amount": "0.01",
            },
        )

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("750.00"))
        self.assertEqual(self.withdrawals().get().amount, Decimal("250.00"))

    def test_purchase_post_requires_a_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.post(self.purchase_url)

        self.assertEqual(response.status_code, 403)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS)
        self.assertFalse(self.withdrawals().exists())

    def test_unknown_product_is_rejected_without_a_debit(self):
        self.login()
        unknown_url = reverse("purchase_reward", args=["made-up-price"])

        response = self.client.post(unknown_url)

        self.assertEqual(response.status_code, 404)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS)
        self.assertFalse(self.withdrawals().exists())

    def test_transaction_failure_rolls_back_the_balance_deduction(self):
        description = (
            "Purchase — Quality Quidditch Supplies: Nimbus 2000 · "
            "50 Galleons · Gringotts Privileges"
        )
        failure = mock.patch.object(
            Transaction.objects,
            "create",
            side_effect=RuntimeError("database is down"),
        )

        with failure, self.assertRaises(RuntimeError):
            withdraw_money(
                self.account,
                galleons_to_usd(50),
                description=description,
            )

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, WELCOME_BONUS)
        self.assertFalse(self.withdrawals().exists())

    def test_rewards_money_uses_grouping_and_two_decimal_places(self):
        self.account.balance = Decimal("1001240.00")
        self.account.save(update_fields=["balance"])
        self.login()

        response = self.client.get(reverse("rewards"))

        self.assertContains(response, "Current account balance")
        self.assertContains(response, "Available balance: $1,001,240.00")
        self.assertContains(response, "$1,001,240.00")
        self.assertContains(response, "$1,000,990.00")
        self.assertContains(response, "$250.00")
        self.assertContains(response, "1 Galleon = $5.00")
