from django.contrib import admin, messages

from .models import (
    LoanAccountMissing,
    LoanAlreadyDisbursed,
    LoanApplication,
    LoanNotEligible,
    approve_and_disburse_loan,
    reject_undisbursed_loan,
)


@admin.register(LoanApplication)
class LoanApplicationAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "requested_amount",
        "repayment_term",
        "display_interest_rate",
        "display_total_repayment",
        "loan_state",
        "created_at",
    ]
    list_filter = ["status", "repayment_term", "disbursed_at", "created_at"]
    search_fields = ["user__username", "user__email", "purpose", "employment"]
    actions = ["approve_and_disburse_selected", "reject_selected"]
    readonly_fields = [
        "user",
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
        "status",
        "display_interest_rate",
        "display_total_repayment",
        "amount_repaid",
        "display_remaining_balance",
        "loan_state",
        "disbursed_at",
        "fully_repaid_at",
        "created_at",
        "updated_at",
    ]
    fieldsets = [
        (
            "Application",
            {
                "fields": (
                    "user",
                    "requested_amount",
                    "repayment_term",
                    "purpose",
                    "employment",
                    "monthly_income",
                    "status",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            "Phase Two Financial Details",
            {
                "fields": (
                    "display_interest_rate",
                    "display_total_repayment",
                    "amount_repaid",
                    "display_remaining_balance",
                    "loan_state",
                    "disbursed_at",
                    "fully_repaid_at",
                )
            },
        ),
        (
            "Gringotts Risk Questionnaire",
            {
                "fields": (
                    "previous_default",
                    "owes_goblins",
                    "dragons_on_property",
                    "unauthorized_vault_entry",
                    "financial_decision_rating",
                    "income_loss_repayment",
                    "collateral",
                )
            },
        ),
    ]

    @admin.display(description="Interest rate")
    def display_interest_rate(self, application):
        return f"{application.interest_rate_percent:.0f}% fixed"

    @admin.display(description="Total repayment")
    def display_total_repayment(self, application):
        return f"${application.total_repayment:,.2f}"

    @admin.display(description="Remaining balance")
    def display_remaining_balance(self, application):
        return f"${application.remaining_balance:,.2f}"

    @admin.display(description="Loan state")
    def loan_state(self, application):
        if application.is_fully_repaid:
            return "Repaid"
        if application.is_active:
            return "Active"
        if application.status == LoanApplication.APPROVED:
            return "Approved — Awaiting disbursement"
        return application.get_status_display()

    @admin.action(
        permissions=["change"],
        description="Approve and disburse selected loans",
    )
    def approve_and_disburse_selected(self, request, queryset):
        disbursed = 0
        for application_id in queryset.values_list("pk", flat=True):
            try:
                approve_and_disburse_loan(application_id)
            except LoanAlreadyDisbursed:
                self.message_user(
                    request,
                    f"Loan #{application_id} was already disbursed; no "
                    "additional funds were deposited.",
                    level=messages.WARNING,
                )
            except (LoanAccountMissing, LoanNotEligible) as exc:
                self.message_user(
                    request,
                    f"Loan #{application_id}: {exc}",
                    level=messages.ERROR,
                )
            else:
                disbursed += 1

        if disbursed:
            self.message_user(
                request,
                f"Approved and disbursed {disbursed} loan"
                f"{'s' if disbursed != 1 else ''}.",
                level=messages.SUCCESS,
            )

    @admin.action(
        permissions=["change"],
        description="Reject selected undisbursed loans",
    )
    def reject_selected(self, request, queryset):
        rejected = 0
        for application_id in queryset.values_list("pk", flat=True):
            try:
                reject_undisbursed_loan(application_id)
            except LoanNotEligible as exc:
                self.message_user(
                    request,
                    f"Loan #{application_id}: {exc}",
                    level=messages.ERROR,
                )
            else:
                rejected += 1

        if rejected:
            self.message_user(
                request,
                f"Rejected {rejected} loan"
                f"{'s' if rejected != 1 else ''} without moving funds.",
                level=messages.SUCCESS,
            )

    def has_add_permission(self, request):
        return False
