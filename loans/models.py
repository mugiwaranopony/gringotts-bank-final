from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from banking.models import Account, deposit_money, withdraw_money


MONEY_PLACES = Decimal("0.01")


class LoanAlreadyDisbursed(Exception):
    """Raised when a funded loan is submitted for funding again."""


class LoanNotEligible(Exception):
    """Raised when the requested loan operation is not allowed."""


class LoanAccountMissing(Exception):
    """Raised when the applicant does not have an existing bank account."""


class LoanRepaymentError(Exception):
    """Base error for a repayment that cannot be completed."""


class RepaymentExceedsRemaining(LoanRepaymentError):
    def __init__(self, remaining_balance):
        self.remaining_balance = remaining_balance
        super().__init__("Repayment exceeds the remaining loan balance.")


class LoanInsufficientFunds(LoanRepaymentError):
    def __init__(self, required, available):
        self.required = required
        self.available = available
        super().__init__("The bank account cannot cover this repayment.")


class LoanApplication(models.Model):
    TERM_6_MONTHS = 6
    TERM_12_MONTHS = 12
    TERM_24_MONTHS = 24
    TERM_36_MONTHS = 36
    REPAYMENT_TERM_CHOICES = [
        (TERM_6_MONTHS, "6 months"),
        (TERM_12_MONTHS, "12 months"),
        (TERM_24_MONTHS, "24 months"),
        (TERM_36_MONTHS, "36 months"),
    ]
    INTEREST_RATES_BY_TERM = {
        TERM_6_MONTHS: Decimal("0.05"),
        TERM_12_MONTHS: Decimal("0.08"),
        TERM_24_MONTHS: Decimal("0.12"),
        TERM_36_MONTHS: Decimal("0.16"),
    }

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    ]

    PREVIOUS_DEFAULT_CHOICES = [
        ("NO", "No"),
        ("YES", "Yes"),
        ("AZKABAN", "I prefer not to discuss Azkaban"),
    ]
    GOBLIN_DEBT_CHOICES = [
        ("NO", "No"),
        ("YES", "Yes"),
        ("UNPROVEN", "Not any that can prove it"),
    ]
    DRAGON_CHOICES = [
        ("0", "0"),
        ("1", "1"),
        ("2_PLUS", "2+"),
        ("DEFINE_RESIDE", 'Define "reside"'),
    ]
    VAULT_ENTRY_CHOICES = [
        ("NO", "No"),
        ("YES", "Yes"),
        (
            "LEGAL_ADVICE",
            "My solicitor has advised me not to answer this question",
        ),
    ]
    FINANCIAL_DECISION_CHOICES = [
        ("EXCELLENT", "Excellent"),
        ("GOOD", "Good"),
        ("QUESTIONABLE", "Questionable"),
        ("NIMBUS", "I am applying for this loan to buy a Nimbus"),
    ]
    INCOME_LOSS_REPAYMENT_CHOICES = [
        ("SAVINGS", "Savings"),
        ("SELL_ASSETS", "Sell assets"),
        ("FAMILY_SUPPORT", "Family support"),
        (
            "POLYJUICE",
            "A highly elaborate plan involving Polyjuice Potion",
        ),
    ]
    COLLATERAL_CHOICES = [
        ("PROPERTY", "Property"),
        ("GOLD", "Gold"),
        ("MAGICAL_OBJECTS", "Valuable magical objects"),
        ("FAMILY_HEIRLOOM", "Family heirloom"),
        ("FIRST_BORN", "First-born child"),
        ("WAIT_WHAT", "Wait, WHAT?"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_applications",
    )
    requested_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    repayment_term = models.PositiveSmallIntegerField(
        choices=REPAYMENT_TERM_CHOICES,
    )
    purpose = models.TextField(max_length=500)
    employment = models.CharField(max_length=150)
    monthly_income = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    previous_default = models.CharField(
        max_length=20,
        choices=PREVIOUS_DEFAULT_CHOICES,
        verbose_name="Have you previously defaulted on a loan?",
    )
    owes_goblins = models.CharField(
        max_length=20,
        choices=GOBLIN_DEBT_CHOICES,
        verbose_name="Do you currently owe money to any goblins?",
    )
    dragons_on_property = models.CharField(
        max_length=20,
        choices=DRAGON_CHOICES,
        verbose_name="How many dragons currently reside on your property?",
    )
    unauthorized_vault_entry = models.CharField(
        max_length=20,
        choices=VAULT_ENTRY_CHOICES,
        verbose_name=(
            "Have you ever entered a Gringotts vault without authorization?"
        ),
    )
    financial_decision_rating = models.CharField(
        max_length=20,
        choices=FINANCIAL_DECISION_CHOICES,
        verbose_name="Please rate your financial decision-making.",
    )
    income_loss_repayment = models.CharField(
        max_length=20,
        choices=INCOME_LOSS_REPAYMENT_CHOICES,
        verbose_name=(
            "If your income disappeared tomorrow, how would you repay Gringotts?"
        ),
    )
    collateral = models.CharField(
        max_length=20,
        choices=COLLATERAL_CHOICES,
        verbose_name="What are you willing to offer as collateral?",
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=PENDING,
    )
    amount_repaid = models.DecimalField(
        max_digits=13,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    disbursed_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
    )
    fully_repaid_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(requested_amount__gt=0),
                name="loan_requested_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(monthly_income__gte=0),
                name="loan_monthly_income_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_repaid__gte=0),
                name="loan_amount_repaid_non_negative",
            ),
        ]

    @classmethod
    def interest_rate_for_term(cls, repayment_term):
        return cls.INTEREST_RATES_BY_TERM[int(repayment_term)]

    @property
    def interest_rate(self):
        return self.interest_rate_for_term(self.repayment_term)

    @property
    def interest_rate_percent(self):
        return self.interest_rate * Decimal("100")

    @property
    def total_repayment(self):
        principal = Decimal(self.requested_amount)
        return (
            principal * (Decimal("1.00") + self.interest_rate)
        ).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)

    @property
    def remaining_balance(self):
        remaining = self.total_repayment - Decimal(self.amount_repaid)
        return max(remaining, Decimal("0.00"))

    @property
    def is_disbursed(self):
        return self.disbursed_at is not None

    @property
    def is_fully_repaid(self):
        return self.is_disbursed and self.remaining_balance == Decimal("0.00")

    @property
    def is_active(self):
        return (
            self.status == self.APPROVED
            and self.is_disbursed
            and not self.is_fully_repaid
        )

    def __str__(self):
        return (
            f"{self.user.username} - ${self.requested_amount} "
            f"({self.get_status_display()})"
        )


def loan_disbursement_description(application):
    return (
        "Loan Disbursement — Gringotts Loans | "
        f"Loan #{application.pk} | "
        f"{application.get_repayment_term_display()} | "
        f"{application.interest_rate_percent:.0f}% fixed interest | "
        f"Total repayment ${application.total_repayment:,.2f}"
    )


def loan_repayment_description(application, remaining_balance):
    return (
        "Loan Repayment — Gringotts Loans | "
        f"Loan #{application.pk} | "
        f"Remaining balance ${remaining_balance:,.2f}"
    )


def approve_and_disburse_loan(application_id):
    """Approve and fund one application, atomically and at most once."""
    with transaction.atomic():
        application = (
            LoanApplication.objects.select_for_update()
            .select_related("user")
            .get(pk=application_id)
        )

        if application.disbursed_at is not None:
            raise LoanAlreadyDisbursed("This loan has already been disbursed.")
        if application.status == LoanApplication.REJECTED:
            raise LoanNotEligible("Rejected loans cannot be disbursed.")

        try:
            account = Account.objects.get(owner_id=application.user_id)
        except Account.DoesNotExist as exc:
            raise LoanAccountMissing(
                "The applicant does not have an existing Gringotts account."
            ) from exc

        deposit_money(
            account,
            application.requested_amount,
            description=loan_disbursement_description(application),
        )
        application.status = LoanApplication.APPROVED
        application.disbursed_at = timezone.now()
        application.save(
            update_fields=["status", "disbursed_at", "updated_at"],
        )
        return application


def reject_undisbursed_loan(application_id):
    """Reject an application only while no funds have been released."""
    with transaction.atomic():
        application = LoanApplication.objects.select_for_update().get(
            pk=application_id
        )
        if application.disbursed_at is not None:
            raise LoanNotEligible("A disbursed loan cannot be rejected.")

        application.status = LoanApplication.REJECTED
        application.save(update_fields=["status", "updated_at"])
        return application


def make_loan_repayment(application_id, user, amount):
    """Pay down a user-owned active loan and write the existing bank ledger."""
    amount = Decimal(amount)
    if amount <= 0 or amount != amount.quantize(MONEY_PLACES):
        raise LoanRepaymentError(
            "Repayment amount must be greater than zero with at most two decimals."
        )

    with transaction.atomic():
        application = LoanApplication.objects.select_for_update().get(
            pk=application_id,
            user_id=user.pk,
        )
        if not application.is_active:
            raise LoanRepaymentError(
                "Only active, disbursed loans can receive repayments."
            )

        remaining_before = application.remaining_balance
        if amount > remaining_before:
            raise RepaymentExceedsRemaining(remaining_before)

        try:
            account = Account.objects.select_for_update().get(owner_id=user.pk)
        except Account.DoesNotExist as exc:
            raise LoanAccountMissing(
                "You do not have an existing Gringotts account."
            ) from exc

        if amount > account.balance:
            raise LoanInsufficientFunds(amount, account.balance)

        remaining_after = remaining_before - amount
        withdraw_money(
            account,
            amount,
            description=loan_repayment_description(
                application,
                remaining_after,
            ),
        )
        application.amount_repaid += amount
        update_fields = ["amount_repaid", "updated_at"]
        if remaining_after == Decimal("0.00"):
            application.fully_repaid_at = timezone.now()
            update_fields.append("fully_repaid_at")
        application.save(update_fields=update_fields)
        return application
