# banking/admin.py
from django.contrib import admin

from .models import Account, PaymentRequest, Transaction


class AccountAdmin(admin.ModelAdmin):
    list_display = ["owner", "balance", "created_at"]


class TransactionAdmin(admin.ModelAdmin):
    list_display = ["account", "transaction_type", "amount", "timestamp"]
    list_filter = ["transaction_type"]


class PaymentRequestAdmin(admin.ModelAdmin):
    list_display = ["requester", "payer", "amount", "status", "created_at"]
    list_filter = ["status"]


admin.site.register(Account, AccountAdmin)
admin.site.register(Transaction, TransactionAdmin)
admin.site.register(PaymentRequest, PaymentRequestAdmin)
