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


class UsernameField(forms.CharField):
    """A username that must belong to a real customer."""

    def clean(self, value):
        username = super().clean(value)
        if not get_user_model().objects.filter(username=username).exists():
            raise forms.ValidationError(f"No user named '{username}' at the bank.")
        return username


class TransferForm(forms.Form):
    recipient = UsernameField(max_length=150, label="Send to (username)")
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0.01,
        label="Amount ($)",
    )


class RequestMoneyForm(forms.Form):
    payer = UsernameField(max_length=150, label="Request from (username)")
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0.01,
        label="Amount ($)",
    )
    note = forms.CharField(
        max_length=200,
        required=False,
        label="What is it for? (optional)",
    )
