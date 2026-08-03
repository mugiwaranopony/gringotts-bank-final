# banking/forms.py
from django import forms
from django.contrib.auth import get_user_model


class DepositForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0.01,
        label="Amount ($)",
    )


class WithdrawForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0.01,
        label="Amount ($)",
    )


class TransferForm(forms.Form):
    recipient = forms.CharField(max_length=150, label="Send to (username)")
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0.01,
        label="Amount ($)",
    )

    def clean_recipient(self):
        username = self.cleaned_data["recipient"]
        if not get_user_model().objects.filter(username=username).exists():
            raise forms.ValidationError(f"No user named '{username}' at the bank.")
        return username
