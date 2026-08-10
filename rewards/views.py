from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from banking.models import InsufficientFunds, get_or_create_account, withdraw_money

GALLEON_TO_USD_RATE = Decimal("5.00")
REWARD_IMAGE_PLACEHOLDER = "rewards/images/placeholder.svg"

REWARD_PRODUCTS = (
    {
        "product_id": "dinner-for-two",
        "partner": "Three Broomsticks",
        "name": "Dinner for Two",
        "description": (
            "A wizarding dinner for two in Hogsmeade including two Butterbeers."
        ),
        "galleon_price": 12,
        "image_filename": "rewards/images/three-broomsticks.jpg",
        "image_alt": "",
    },
    {
        "product_id": "honeydukes-sweet-box",
        "partner": "Honeydukes",
        "name": "Honeydukes Sweet Box",
        "description": (
            "A selection including Chocolate Frogs, Bertie Bott's Every Flavour "
            "Beans, and other wizarding sweets."
        ),
        "galleon_price": 6,
        "image_filename": "rewards/images/honeydukes.jpg",
        "image_alt": "",
    },
    {
        "product_id": "nimbus-2000",
        "partner": "Quality Quidditch Supplies",
        "name": "Nimbus 2000",
        "description": (
            "A classic high-performance broomstick from Quality Quidditch "
            "Supplies."
        ),
        "galleon_price": 50,
        "image_filename": "rewards/images/nimbus-2000.jpg",
        "image_alt": "",
    },
    {
        "product_id": "deluxe-joke-box",
        "partner": "Weasleys' Wizard Wheezes",
        "name": "Deluxe Joke Box",
        "description": (
            "A collection of magical jokes, tricks, and novelty products from "
            "Weasleys' Wizard Wheezes."
        ),
        "galleon_price": 10,
        "image_filename": "rewards/images/weasleys-wizard-wheezes.jpg",
        "image_alt": "",
    },
    {
        "product_id": "wizarding-book-collection",
        "partner": "Flourish and Blotts",
        "name": "Wizarding Book Collection",
        "description": (
            "A curated collection of magical books and wizarding literature."
        ),
        "galleon_price": 9,
        "image_filename": "rewards/images/flourish-and-blotts.jpg",
        "image_alt": "",
    },
    {
        "product_id": "premium-wizard-robes",
        "partner": "Madam Malkin's Robes for All Occasions",
        "name": "Premium Wizard Robes",
        "description": (
            "A fitted set of premium wizarding robes from Madam Malkin's."
        ),
        "galleon_price": 18,
        "image_filename": "rewards/images/madam-malkins.jpg",
        "image_alt": "",
    },
    {
        "product_id": "premium-wand-care-package",
        "partner": "Ollivanders",
        "name": "Premium Wand Care Package",
        "description": (
            "Professional wand inspection, cleaning, polishing, and maintenance."
        ),
        "galleon_price": 8,
        "image_filename": "rewards/images/ollivanders.jpg",
        "image_alt": "",
    },
    {
        "product_id": "magical-pet-care-package",
        "partner": "Magical Menagerie",
        "name": "Magical Pet Care Package",
        "description": (
            "A premium selection of magical pet supplies and accessories."
        ),
        "galleon_price": 11,
        "image_filename": "rewards/images/magical-menagerie.jpg",
        "image_alt": "",
    },
    {
        "product_id": "one-night-stay",
        "partner": "The Leaky Cauldron",
        "name": "One-Night Stay",
        "description": (
            "One night's accommodation at the famous wizarding inn, including "
            "breakfast."
        ),
        "galleon_price": 20,
        "image_filename": "rewards/images/leaky-cauldron.jpg",
        "image_alt": "",
    },
    {
        "product_id": "premium-owl-care-kit",
        "partner": "Eeylops Owl Emporium",
        "name": "Premium Owl Care Kit",
        "description": (
            "A complete owl-care package with selected supplies and accessories."
        ),
        "galleon_price": 7,
        "image_filename": "rewards/images/eylops-owl-emporium.jpg",
        "image_alt": "",
    },
    {
        "product_id": "mystery-magical-curio",
        "partner": "Borgin and Burkes",
        "name": "Mystery Magical Curio",
        "description": (
            "A carefully selected mysterious magical antique or curio."
        ),
        "galleon_price": 25,
        "image_filename": "rewards/images/borgin-and-burkes.jpg",
        "image_alt": "",
    },
    {
        "product_id": "first-class-journey-package",
        "partner": "Hogwarts Express",
        "name": "First-Class Journey Package",
        "description": (
            "A first-class wizarding travel package including reserved seating "
            "and refreshments."
        ),
        "galleon_price": 15,
        "image_filename": "rewards/images/hogwarts-express.jpg",
        "image_alt": "",
    },
)

PRODUCTS_BY_ID = {
    product["product_id"]:
        product for product in REWARD_PRODUCTS
}


def galleons_to_usd(galleons):
    return Decimal(galleons) * GALLEON_TO_USD_RATE


def _get_product(product_id):
    product = PRODUCTS_BY_ID.get(product_id)
    if product is None:
        raise Http404("No such Gringotts Privileges product.")
    return product


def rewards_page(request):
    account = None
    if request.user.is_authenticated:
        account = get_or_create_account(request.user)

    products = []
    for catalogue_product in REWARD_PRODUCTS:
        product = catalogue_product.copy()
        product["image"] = (
            product["image_filename"]
            if finders.find(product["image_filename"])
            else REWARD_IMAGE_PLACEHOLDER
        )
        product["usd_price"] = galleons_to_usd(product["galleon_price"])
        if account is not None:
            product["can_afford"] = account.balance >= product["usd_price"]
            product["balance_after"] = account.balance - product["usd_price"]
        products.append(product)

    return render(
        request,
        "rewards/rewards.html",
        {
            "account": account,
            "exchange_rate": GALLEON_TO_USD_RATE,
            "products": products,
        },
    )


@login_required
@require_POST
def purchase_reward(request, product_id):
    product = _get_product(product_id)
    usd_price = galleons_to_usd(product["galleon_price"])
    account = get_or_create_account(request.user)
    description = (
        f"Purchase — {product['partner']}: {product['name']} · "
        f"{product['galleon_price']} Galleons · Gringotts Privileges"
    )

    try:
        withdraw_money(account, usd_price, description=description)
    except InsufficientFunds:
        account.refresh_from_db()
        messages.error(
            request,
            (
                "Insufficient balance. "
                f"This purchase requires ${usd_price:.2f}, but your available "
                f"balance is ${account.balance:.2f}."
            ),
            extra_tags="danger",
        )
    else:
        messages.success(
            request,
            (
                f"Purchase successful! {product['name']} was purchased for "
                f"{product['galleon_price']} Galleons (${usd_price:.2f})."
            ),
        )

    return redirect("rewards")
