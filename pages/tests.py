from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class HomePageTests(SimpleTestCase):
    def test_url_exists_at_correct_location(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_url_available_by_name(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

    def test_template_name_correct(self):
        response = self.client.get(reverse("home"))
        self.assertTemplateUsed(response, "pages/home.html")

    def test_template_content(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Gringotts Bank")


class AboutPageTests(SimpleTestCase):
    def test_url_exists_at_correct_location(self):
        response = self.client.get("/about/")
        self.assertEqual(response.status_code, 200)

    def test_url_available_by_name(self):
        response = self.client.get(reverse("about"))
        self.assertEqual(response.status_code, 200)

    def test_template_name_correct(self):
        response = self.client.get(reverse("about"))
        self.assertTemplateUsed(response, "pages/about.html")


class NavbarTests(TestCase):
    def test_logged_out_navbar_does_not_show_privileges_or_loans(self):
        response = self.client.get(reverse("home"))

        self.assertNotContains(
            response,
            f'<a class="nav-link" href="{reverse("rewards")}">Privileges</a>',
            html=True,
        )
        self.assertNotContains(
            response,
            f'<a class="nav-link" href="{reverse("loans")}">Loans</a>',
            html=True,
        )

    def test_logged_in_navbar_shows_new_and_existing_links(self):
        user = get_user_model().objects.create_user(
            username="navbar-user",
            password="testpass123",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("home"))

        expected_links = (
            ("home", "Home"),
            ("about", "About"),
            ("dashboard", "Dashboard"),
            ("transaction_list", "History"),
            ("payment_requests", "Requests"),
            ("rewards", "Privileges"),
            ("loans", "Loans"),
        )
        for url_name, label in expected_links:
            with self.subTest(label=label):
                self.assertContains(
                    response,
                    f'<a class="nav-link" href="{reverse(url_name)}">{label}</a>',
                    html=True,
                )

    def test_privileges_and_loans_named_routes_resolve_correctly(self):
        self.assertEqual(reverse("rewards"), "/rewards/")
        self.assertEqual(reverse("loans"), "/loans/")
