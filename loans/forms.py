from decimal import Decimal

from django import forms

from .models import LoanApplication


class LoanApplicationForm(forms.ModelForm):
    class Meta:
        model = LoanApplication
        fields = [
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
        ]
        labels = {
            "requested_amount": "Requested loan amount ($)",
            "repayment_term": "Repayment period",
            "purpose": "Purpose of loan",
            "employment": "Current employment",
            "monthly_income": "Monthly income ($)",
        }
        help_texts = {
            "requested_amount": "Enter the amount you wish to borrow in USD.",
            "monthly_income": "Enter gross monthly income before deductions.",
        }
        widgets = {
            "requested_amount": forms.NumberInput(
                attrs={"min": "0.01", "step": "0.01"}
            ),
            "purpose": forms.Textarea(attrs={"rows": 3}),
            "employment": forms.TextInput(
                attrs={"placeholder": "Occupation or source of employment"}
            ),
            "monthly_income": forms.NumberInput(
                attrs={"min": "0", "step": "0.01"}
            ),
        }


class LoanRepaymentForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Repayment amount ($)",
    )
